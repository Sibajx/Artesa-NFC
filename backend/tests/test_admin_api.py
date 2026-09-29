"""Gestión admin API, phase 1 (ADR-029): Cloudflare Access identity and the
read-only endpoints.

Tokens are signed here with throw-away RSA keys and verified by the real
AccessVerifier; only the JWKS download is replaced by an in-memory key set.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.deps import get_db
from app.core.access import ACCESS_JWT_HEADER, AccessVerifier, get_access_verifier
from app.main import app
from app.models.artisan import Artisan
from app.models.audit_event import AuditActorType, AuditEvent, AuditResult
from app.models.certificate import Certificate, CertificateStatus
from app.models.enums import PublicationStatus
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.models.piece import Piece

TEAM = "artesanfc-test.cloudflareaccess.com"
ISSUER = f"https://{TEAM}"
AUD = "0123456789abcdef" * 4
ADMIN_EMAIL = "ops@example.org"
TOKEN_HASH = "a1" * 32
PHYSICAL_UID = "04:A1:B2:C3:D4:E5:F6"

NOT_FOUND_BODY = {"error": {"code": "not_found", "message": "The requested resource does not exist."}}

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    """Stands in for jwt.PyJWKClient: same method, keys from memory."""

    def __init__(self, keys: dict[str, rsa.RSAPrivateKey] | None = None, *, down: bool = False) -> None:
        self.keys = {
            kid: jwt.PyJWK(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), algorithm="RS256")
            for kid, key in (keys if keys is not None else {"k1": _KEY}).items()
        }
        self.down = down

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        if self.down:
            raise jwt.PyJWKClientConnectionError("unreachable")
        kid = jwt.get_unverified_header(token).get("kid")
        if kid not in self.keys:
            raise jwt.PyJWKClientError("no matching key")
        return self.keys[kid]


def make_token(*, key=_KEY, kid="k1", algorithm="RS256", **overrides) -> str:
    now = int(time.time())
    claims = {"aud": [AUD], "iss": ISSUER, "iat": now, "exp": now + 600, "email": ADMIN_EMAIL, "sub": "user-1"}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def auth(token: str) -> dict[str, str]:
    return {ACCESS_JWT_HEADER: token}


@pytest.fixture()
def verifier():
    v = AccessVerifier(TEAM, AUD, (ADMIN_EMAIL,), jwks_client=FakeJWKS())
    app.dependency_overrides[get_access_verifier] = lambda: v
    yield v
    app.dependency_overrides.pop(get_access_verifier, None)


@pytest.fixture()
def client(verifier, db_session):
    def _db():
        yield db_session

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# --- identity ----------------------------------------------------------------


def test_admin_is_a_plain_404_when_not_configured():
    app.dependency_overrides[get_access_verifier] = lambda: None
    try:
        response = TestClient(app).get("/api/admin/v1/me", headers=auth(make_token()))
    finally:
        app.dependency_overrides.pop(get_access_verifier, None)
    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY
    assert response.headers["cache-control"] == "no-store"


def test_default_test_settings_leave_the_admin_api_disabled():
    assert get_access_verifier() is None


def test_valid_token_for_an_allowlisted_email(client):
    response = client.get("/api/admin/v1/me", headers=auth(make_token(email="OPS@Example.org")))
    assert response.status_code == 200
    assert response.json() == {"email": ADMIN_EMAIL}
    assert response.headers["cache-control"] == "no-store"


def test_missing_token_is_401(client):
    response = client.get("/api/admin/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response.headers["cache-control"] == "no-store"


def test_the_access_cookie_alone_is_not_accepted(client):
    client.cookies.set("CF_Authorization", make_token())
    response = client.get("/api/admin/v1/me")
    assert response.status_code == 401


@pytest.mark.parametrize("token_factory", [
    pytest.param(lambda: make_token(aud=["another-application"]), id="wrong-audience"),
    pytest.param(lambda: make_token(iss="https://evil.cloudflareaccess.com"), id="wrong-issuer"),
    pytest.param(lambda: make_token(exp=int(time.time()) - 120), id="expired"),
    pytest.param(lambda: make_token(key=_OTHER_KEY), id="signed-by-another-key-same-kid"),
    pytest.param(lambda: make_token(kid="unknown"), id="unknown-kid"),
    pytest.param(lambda: make_token(email=None), id="no-email-claim"),
    pytest.param(lambda: make_token(exp=None), id="no-exp-claim"),
    pytest.param(lambda: make_token(key="x" * 32, algorithm="HS256"), id="hs256"),
    pytest.param(lambda: jwt.encode({"email": ADMIN_EMAIL, "aud": AUD, "iss": ISSUER}, None, algorithm="none"), id="alg-none"),
    pytest.param(lambda: "not.a.jwt", id="malformed"),
])
def test_invalid_tokens_are_401(client, token_factory):
    token = token_factory()
    response = client.get("/api/admin/v1/me", headers=auth(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert token not in response.text


def test_valid_token_for_an_email_outside_the_allowlist_is_403(client):
    response = client.get("/api/admin/v1/me", headers=auth(make_token(email="someone@example.org")))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_key_set_unreachable_is_503(db_session):
    app.dependency_overrides[get_access_verifier] = lambda: AccessVerifier(TEAM, AUD, (ADMIN_EMAIL,), jwks_client=FakeJWKS(down=True))
    try:
        response = TestClient(app).get("/api/admin/v1/me", headers=auth(make_token()))
    finally:
        app.dependency_overrides.pop(get_access_verifier, None)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_unavailable"


def test_every_admin_route_requires_the_token(client):
    some_id = uuid.uuid4()
    for path in ("/api/admin/v1/artisans", f"/api/admin/v1/artisans/{some_id}", "/api/admin/v1/pieces",
                 f"/api/admin/v1/pieces/{some_id}", "/api/admin/v1/audit-events"):
        assert client.get(path).status_code == 401, path


def test_admin_routes_are_read_only(client):
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/admin/v1/artisans", headers=auth(make_token()))
        assert response.status_code == 405, method


def test_admin_routes_do_not_answer_cors_for_the_public_origin(client):
    response = client.get("/api/admin/v1/me", headers={**auth(make_token()), "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


# --- data ------------------------------------------------------------------------


def _seed(db_session) -> dict:
    draft_artisan = Artisan(slug="adm-draft-artisan", full_name="Borrador Uno",
                            public_contact={"phone": "+52 951 000 0000"})
    published_artisan = Artisan(slug="adm-published-artisan", full_name="Publicada Dos",
                                publication_status=PublicationStatus.published)
    db_session.add_all([draft_artisan, published_artisan])
    db_session.flush()
    draft_piece = Piece(slug="adm-draft-piece", public_code="ADM-001", artisan_id=published_artisan.id, name="Máscara borrador")
    public_piece = Piece(slug="adm-public-piece", public_code="ADM-002", artisan_id=published_artisan.id,
                         name="Máscara pública", publication_status=PublicationStatus.published)
    db_session.add_all([draft_piece, public_piece])
    db_session.flush()
    db_session.add(Certificate(piece_id=public_piece.id, status=CertificateStatus.active, token_hash=TOKEN_HASH,
                               issued_at=datetime.now(timezone.utc)))
    db_session.add(NfcTag(piece_id=public_piece.id, chip_model="NTAG213", status=NfcTagStatus.available,
                          physical_uid=PHYSICAL_UID))
    db_session.flush()
    return {"draft_artisan": draft_artisan, "published_artisan": published_artisan,
            "draft_piece": draft_piece, "public_piece": public_piece}


def test_artisan_list_includes_drafts_and_filters(client, db_session):
    rows = _seed(db_session)
    token = auth(make_token())

    listed = client.get("/api/admin/v1/artisans", headers=token).json()
    slugs = {a["slug"] for a in listed["data"]}
    assert {"adm-draft-artisan", "adm-published-artisan"} <= slugs
    assert listed["meta"]["total"] == len(listed["data"])

    drafts = client.get("/api/admin/v1/artisans?publication_status=draft", headers=token).json()["data"]
    assert all(a["publication_status"] == "draft" for a in drafts)
    assert "adm-draft-artisan" in {a["slug"] for a in drafts}

    found = client.get("/api/admin/v1/artisans?q=publicada", headers=token).json()["data"]
    assert [a["slug"] for a in found if a["slug"].startswith("adm-")] == ["adm-published-artisan"]
    published = next(a for a in found if a["slug"] == "adm-published-artisan")
    assert published["piece_count"] == 2 and published["id"] == str(rows["published_artisan"].id)


def test_search_treats_like_wildcards_literally(client, db_session):
    _seed(db_session)
    found = client.get("/api/admin/v1/artisans?q=%25", headers=auth(make_token())).json()["data"]
    assert not [a for a in found if a["slug"].startswith("adm-")]


def test_artisan_detail_shows_internal_fields_and_every_piece(client, db_session):
    rows = _seed(db_session)
    body = client.get(f"/api/admin/v1/artisans/{rows['published_artisan'].id}", headers=auth(make_token())).json()
    assert {p["slug"] for p in body["pieces"]} == {"adm-draft-piece", "adm-public-piece"}
    draft = client.get(f"/api/admin/v1/artisans/{rows['draft_artisan'].id}", headers=auth(make_token())).json()
    assert draft["public_contact"] == {"phone": "+52 951 000 0000"}
    assert draft["publication_status"] == "draft"


def test_piece_detail_never_exposes_token_hash_physical_uid_or_storage_path(client, db_session):
    rows = _seed(db_session)
    response = client.get(f"/api/admin/v1/pieces/{rows['public_piece'].id}", headers=auth(make_token()))
    assert response.status_code == 200
    body = response.json()
    assert body["publicly_visible"] is True
    assert [c["status"] for c in body["certificates"]] == ["active"]
    assert [t["status"] for t in body["nfc_tags"]] == ["available"]
    for forbidden in (TOKEN_HASH, PHYSICAL_UID, "token_hash", "physical_uid", "storage_path"):
        assert forbidden not in response.text


def test_piece_list_filters_and_visibility(client, db_session):
    rows = _seed(db_session)
    token = auth(make_token())
    by_artisan = client.get(f"/api/admin/v1/pieces?artisan_id={rows['published_artisan'].id}", headers=token).json()
    assert {p["slug"] for p in by_artisan["data"]} == {"adm-draft-piece", "adm-public-piece"}
    assert all(p["artisan_slug"] == "adm-published-artisan" for p in by_artisan["data"])
    draft = client.get(f"/api/admin/v1/pieces/{rows['draft_piece'].id}", headers=token).json()
    assert draft["publicly_visible"] is False and draft["certificates"] == [] and draft["nfc_tags"] == []


def test_unknown_and_invalid_ids(client):
    token = auth(make_token())
    assert client.get(f"/api/admin/v1/pieces/{uuid.uuid4()}", headers=token).json() == NOT_FOUND_BODY
    assert client.get(f"/api/admin/v1/artisans/{uuid.uuid4()}", headers=token).status_code == 404
    assert client.get("/api/admin/v1/pieces/not-a-uuid", headers=token).status_code == 422
    assert client.get("/api/admin/v1/artisans?publication_status=deleted", headers=token).status_code == 422


# --- audit_event -------------------------------------------------------------------


def _event(entity_id: uuid.UUID, action: str, at: datetime | None = None) -> AuditEvent:
    # now() is fixed for a whole transaction, so ordering tests set the time.
    return AuditEvent(actor_type=AuditActorType.admin_user, actor_email=ADMIN_EMAIL, entity_type="artisan",
                      entity_id=entity_id, action=action, result=AuditResult.success, event_metadata={"k": "v"},
                      **({"occurred_at": at} if at else {}))


def test_audit_events_are_listed_newest_first_and_filtered(client, db_session):
    rows = _seed(db_session)
    artisan_id = rows["draft_artisan"].id
    db_session.add(_event(artisan_id, "artisan.created", datetime(2026, 9, 1, 10, tzinfo=timezone.utc)))
    db_session.add(_event(artisan_id, "artisan.updated", datetime(2026, 9, 1, 11, tzinfo=timezone.utc)))
    db_session.add(_event(uuid.uuid4(), "artisan.created"))
    db_session.flush()
    body = client.get(f"/api/admin/v1/audit-events?entity_type=artisan&entity_id={artisan_id}",
                      headers=auth(make_token())).json()
    assert [e["action"] for e in body["data"]] == ["artisan.updated", "artisan.created"]
    assert body["data"][0]["actor_email"] == ADMIN_EMAIL and body["data"][0]["metadata"] == {"k": "v"}
    assert client.get("/api/admin/v1/audit-events?limit=0", headers=auth(make_token())).status_code == 422


@pytest.mark.parametrize("statement", [
    "UPDATE audit_event SET action = 'rewritten' WHERE id = :id",
    "DELETE FROM audit_event WHERE id = :id",
    "TRUNCATE audit_event",
])
def test_audit_event_is_append_only_in_the_database(db_session, statement):
    event = _event(uuid.uuid4(), "artisan.created")
    db_session.add(event)
    db_session.flush()
    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(text(statement), {"id": event.id})
