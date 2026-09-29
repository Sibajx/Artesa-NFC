"""Gestión admin API, phase 2: content writes (ADR-029, API_CONTRACT.md §14.2)."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.access import get_access_verifier
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.certificate import Certificate, CertificateStatus
from app.models.piece import Piece
from tests.test_admin_api import ADMIN_EMAIL, auth, client, make_token, verifier  # noqa: F401  (fixtures)


def H(**extra) -> dict[str, str]:
    return {**auth(make_token()), "X-Artesa-Admin": "1", **extra}


def audit_actions(db_session, entity_id) -> list[str]:
    return list(db_session.execute(
        select(AuditEvent.action).where(AuditEvent.entity_id == uuid.UUID(str(entity_id))).order_by(AuditEvent.occurred_at, AuditEvent.action)
    ).scalars())


def new_artisan(client, name="María López Ruiz", **fields) -> dict:
    r = client.post("/api/admin/v1/artisans", json={"full_name": name, **fields}, headers=H())
    assert r.status_code == 201, r.text
    return r.json()


def new_piece(client, artisan_id, name="Máscara de tigre", **fields) -> dict:
    r = client.post("/api/admin/v1/pieces", json={"artisan_id": artisan_id, "name": name, **fields}, headers=H())
    assert r.status_code == 201, r.text
    return r.json()


def act(client, kind, record, action, **body):
    return client.post(f"/api/admin/v1/{kind}/{record['id']}/{action}", json=body,
                       headers=H(**{"If-Match": record["updated_at"]}))


# --- artisans -------------------------------------------------------------------


def test_create_artisan_is_a_draft_with_generated_slug_and_audit(client, db_session):
    body = new_artisan(client, public_contact={"telefono": "+52 951 000 0000"})
    assert body["publication_status"] == "draft"
    assert body["slug"] == "maria-lopez-ruiz"
    assert body["state"] == "Oaxaca" and body["country"] == "México"
    assert audit_actions(db_session, body["id"]) == ["artisan.created"]
    event = db_session.execute(select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(body["id"]))).scalar_one()
    assert event.actor_email == ADMIN_EMAIL
    assert "public_contact" not in event.event_metadata["fields"]


def test_generated_slugs_do_not_collide_and_explicit_duplicates_are_409(client):
    first = new_artisan(client, name="Taller Ñuu")
    second = new_artisan(client, name="Taller Ñuu")
    assert (first["slug"], second["slug"]) == ("taller-nuu", "taller-nuu-2")
    r = client.post("/api/admin/v1/artisans", json={"full_name": "Otro", "slug": "taller-nuu"}, headers=H())
    assert r.status_code == 409
    assert r.json()["error"] == {"code": "duplicate", "message": "The slug is already in use.", "field": "slug"}


@pytest.mark.parametrize("payload", [
    {"full_name": ""},
    {"full_name": "   "},
    {"full_name": "Ok", "unexpected": 1},
    {"full_name": "Ok", "slug": "Not A Slug"},
    {"full_name": "Ok", "languages": ["x"] * 21},
    {},
])
def test_invalid_artisan_bodies_are_422(client, payload):
    assert client.post("/api/admin/v1/artisans", json=payload, headers=H()).status_code == 422


def test_update_requires_the_version_the_user_saw(client, db_session):
    a = new_artisan(client)
    url = f"/api/admin/v1/artisans/{a['id']}"
    assert client.patch(url, json={"locality": "Cuilápam"}, headers=H()).status_code == 428
    assert client.patch(url, json={"locality": "x"}, headers=H(**{"If-Match": "yesterday"})).status_code == 400

    ok = client.patch(url, json={"locality": "Cuilápam", "biography": "  "}, headers=H(**{"If-Match": a["updated_at"]}))
    assert ok.status_code == 200 and ok.json()["locality"] == "Cuilápam" and ok.json()["biography"] is None
    assert ok.json()["updated_at"] != a["updated_at"]

    stale = client.patch(url, json={"locality": "Otra"}, headers=H(**{"If-Match": a["updated_at"]}))
    assert stale.status_code == 412 and stale.json()["error"]["code"] == "stale"
    assert audit_actions(db_session, a["id"]) == ["artisan.created", "artisan.updated"]


def test_update_audit_masks_public_contact_and_skips_no_op_changes(client, db_session):
    a = new_artisan(client)
    url = f"/api/admin/v1/artisans/{a['id']}"
    r = client.patch(url, json={"public_contact": {"tel": "123"}}, headers=H(**{"If-Match": a["updated_at"]}))
    event = db_session.execute(select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(a["id"]),
                                                        AuditEvent.action == "artisan.updated")).scalar_one()
    assert event.event_metadata["changes"] == {"public_contact": {"changed": True}}
    same = client.patch(url, json={"full_name": a["full_name"]}, headers=H(**{"If-Match": r.json()["updated_at"]}))
    assert same.status_code == 200 and same.json()["updated_at"] == r.json()["updated_at"]
    assert audit_actions(db_session, a["id"]).count("artisan.updated") == 1


def test_artisan_lifecycle_and_rules(client, db_session):
    a = new_artisan(client)
    p = act(client, "artisans", a, "publish")
    assert p.status_code == 200 and p.json()["publication_status"] == "published"
    assert act(client, "artisans", p.json(), "publish").json()["error"]["code"] == "invalid_transition"

    slug_change = client.patch(f"/api/admin/v1/artisans/{a['id']}", json={"slug": "otro-slug"},
                               headers=H(**{"If-Match": p.json()["updated_at"]}))
    assert slug_change.status_code == 409 and slug_change.json()["error"]["code"] == "draft_only"

    piece = new_piece(client, a["id"])
    published_piece = act(client, "pieces", piece, "publish").json()
    blocked = act(client, "artisans", p.json(), "archive", reason="retiro")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "has_published_pieces"

    act(client, "pieces", published_piece, "unpublish")
    archived = act(client, "artisans", p.json(), "archive", reason="retiro")
    assert archived.status_code == 200 and archived.json()["publication_status"] == "archived"
    restored = act(client, "artisans", archived.json(), "restore")
    assert restored.json()["publication_status"] == "draft"
    assert audit_actions(db_session, a["id"]) == [
        "artisan.created", "artisan.published", "artisan.archived", "artisan.restored"]
    reason = db_session.execute(select(AuditEvent.event_metadata).where(
        AuditEvent.entity_id == uuid.UUID(a["id"]), AuditEvent.action == "artisan.archived")).scalar_one()
    assert reason == {"from": "published", "to": "archived", "reason": "retiro"}


def test_a_refused_transition_leaves_no_audit_event(client, db_session):
    a = new_artisan(client)
    before = db_session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()
    assert act(client, "artisans", a, "unpublish").status_code == 409
    assert db_session.execute(select(func.count()).select_from(AuditEvent)).scalar_one() == before


# --- pieces ---------------------------------------------------------------------------


def test_create_piece_generates_a_code_and_is_hidden_until_both_are_published(client):
    a = new_artisan(client)
    piece = new_piece(client, a["id"], materials=["copal"], creation_year=2025, dimensions={"alto_cm": 32.5})
    assert re.fullmatch(r"ANFC-[A-HJ-NP-Z2-9]{6}", piece["public_code"])
    assert piece["slug"] == "mascara-de-tigre" and piece["publication_status"] == "draft"
    live = act(client, "pieces", piece, "publish").json()
    assert live["publicly_visible"] is False  # artisan still a draft
    act(client, "artisans", a, "publish")
    assert client.get(f"/api/admin/v1/pieces/{piece['id']}", headers=auth(make_token())).json()["publicly_visible"] is True


def test_piece_codes_and_draft_only_fields(client):
    a = new_artisan(client)
    piece = new_piece(client, a["id"], public_code="anfc-demo-1")
    assert piece["public_code"] == "ANFC-DEMO-1"
    dup = client.post("/api/admin/v1/pieces", json={"artisan_id": a["id"], "name": "x", "public_code": "ANFC-DEMO-1"}, headers=H())
    assert dup.status_code == 409 and dup.json()["error"]["field"] == "public_code"
    live = act(client, "pieces", piece, "publish").json()
    change = client.patch(f"/api/admin/v1/pieces/{piece['id']}", json={"public_code": "ANFC-OTRO"},
                          headers=H(**{"If-Match": live["updated_at"]}))
    assert change.status_code == 409 and change.json()["error"]["code"] == "draft_only"
    ok = client.patch(f"/api/admin/v1/pieces/{piece['id']}", json={"description": "Nueva", "public_code": "ANFC-DEMO-1"},
                      headers=H(**{"If-Match": live["updated_at"]}))
    assert ok.status_code == 200 and ok.json()["description"] == "Nueva"


def test_pieces_cannot_go_to_unknown_or_archived_artisans(client):
    assert client.post("/api/admin/v1/pieces", json={"artisan_id": str(uuid.uuid4()), "name": "x"},
                       headers=H()).json()["error"]["code"] == "unknown_artisan"
    a = new_artisan(client)
    act(client, "artisans", a, "archive")
    assert client.post("/api/admin/v1/pieces", json={"artisan_id": a["id"], "name": "x"},
                       headers=H()).json()["error"]["code"] == "archived_artisan"


def test_archiving_a_piece_with_an_active_certificate_is_refused(client, db_session):
    a = new_artisan(client)
    piece = new_piece(client, a["id"])
    db_session.add(Certificate(piece_id=uuid.UUID(piece["id"]), status=CertificateStatus.active,
                               token_hash="c3" * 32, issued_at=datetime.now(timezone.utc)))
    db_session.flush()
    r = act(client, "pieces", piece, "archive")
    assert r.status_code == 409 and r.json()["error"]["code"] == "active_certificate"
    assert db_session.get(Piece, uuid.UUID(piece["id"])).publication_status.value == "draft"


def test_availability_change_is_audited(client, db_session):
    a = new_artisan(client)
    piece = new_piece(client, a["id"])
    r = client.post(f"/api/admin/v1/pieces/{piece['id']}/availability", json={"availability_status": "exhibited"},
                    headers=H(**{"If-Match": piece["updated_at"]}))
    assert r.status_code == 200 and r.json()["availability_status"] == "exhibited"
    assert audit_actions(db_session, piece["id"]) == ["piece.created", "piece.availability_changed"]
    bad = client.post(f"/api/admin/v1/pieces/{piece['id']}/availability", json={"availability_status": "sold"},
                      headers=H(**{"If-Match": r.json()["updated_at"]}))
    assert bad.status_code == 422


# --- request guard --------------------------------------------------------------------


def test_writes_need_the_admin_header_json_and_a_same_origin(client):
    url = "/api/admin/v1/artisans"
    body = {"full_name": "X"}
    assert client.post(url, json=body, headers=auth(make_token())).status_code == 403
    assert client.post(url, json=body, headers=H(Origin="https://evil.example")).status_code == 403
    assert client.post(url, json=body, headers=H(Origin="http://testserver")).status_code == 201
    form = client.post(url, data={"full_name": "X"}, headers=H())
    assert form.status_code == 415
    assert client.post(url, json=body, headers={"X-Artesa-Admin": "1"}).status_code == 401


def test_writes_are_404_when_admin_is_not_configured():
    app.dependency_overrides[get_access_verifier] = lambda: None
    try:
        r = TestClient(app).post("/api/admin/v1/artisans", json={"full_name": "X"}, headers=H())
    finally:
        app.dependency_overrides.pop(get_access_verifier, None)
    assert r.status_code == 404


def test_write_responses_are_not_cacheable(client):
    r = client.post("/api/admin/v1/artisans", json={"full_name": "Sin caché"}, headers=H())
    assert r.headers["cache-control"] == "no-store"
