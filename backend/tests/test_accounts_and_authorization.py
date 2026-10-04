"""P-026 G3 (artisan authorization by WhatsApp) and G4 (the owner's accounts page)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.api.deps import get_db
from app.core.access import AccessVerifier, get_access_verifier
from app.core.config import get_settings
from app.main import app
from app.models.artisan_authorization import ArtisanAuthorization
from app.models.audit_event import AuditEvent
from app.services import cloudflare_access
from tests.test_admin_api import AUD, CUSTODIAN_EMAIL, TEAM, FakeJWKS, auth, make_token
from tests.test_admin_writes import new_artisan  # noqa: F401

OWNER = "owner@example.org"
FIXED = "ops@example.org"
NEW = "nuevo@example.org"


@pytest.fixture()
def owner_client(db_session, monkeypatch):
    v = AccessVerifier(TEAM, AUD, (FIXED, CUSTODIAN_EMAIL, OWNER), jwks_client=FakeJWKS(),
                       custodians=(CUSTODIAN_EMAIL,), owners=(OWNER,))
    app.dependency_overrides[get_access_verifier] = lambda: v

    def _db():
        yield db_session

    app.dependency_overrides[get_db] = _db
    s = get_settings()
    monkeypatch.setattr(s, "admin_emails", f"{FIXED},{CUSTODIAN_EMAIL},{OWNER}")
    monkeypatch.setattr(s, "owner_emails", OWNER)
    monkeypatch.setattr(s, "custodian_emails", CUSTODIAN_EMAIL)
    yield TestClient(app)
    app.dependency_overrides.pop(get_access_verifier, None)
    app.dependency_overrides.pop(get_db, None)


def H(email=OWNER, **extra):
    return {**auth(make_token(email=email)), "X-Artesa-Admin": "1", **extra}


def accounts(c, email=OWNER):
    return c.get("/api/admin/v1/accounts", headers=auth(make_token(email=email)))


# --- G4: accounts -------------------------------------------------------------------


def test_only_the_owner_sees_the_accounts_page(owner_client):
    me = owner_client.get("/api/admin/v1/me", headers=auth(make_token(email=OWNER))).json()
    assert "owner" in me["roles"] and "custodian" in me["roles"]
    assert accounts(owner_client, FIXED).status_code == 403
    assert accounts(owner_client, CUSTODIAN_EMAIL).status_code == 403
    body = accounts(owner_client).json()
    assert body["data"][0]["email"] == OWNER and body["data"][0]["owner"] is True
    assert {a["email"] for a in body["data"]} == {OWNER, FIXED, CUSTODIAN_EMAIL}
    assert body["sync_configured"] is False


def test_add_change_and_remove_an_account_without_restart(owner_client, db_session):
    # Not allowed yet.
    assert owner_client.get("/api/admin/v1/me", headers=auth(make_token(email=NEW))).status_code == 403
    r = owner_client.post("/api/admin/v1/accounts", json={"email": "Nuevo@Example.org", "role": "editor"}, headers=H())
    assert r.status_code == 200, r.text
    assert r.json()["cloudflare"] == "not_configured"
    assert owner_client.get("/api/admin/v1/me", headers=auth(make_token(email=NEW))).json()["roles"] == ["editor"]
    # Editors cannot reach custody; after promotion they can.
    assert owner_client.get("/api/admin/v1/custody/pieces", headers=auth(make_token(email=NEW))).status_code == 403
    owner_client.post(f"/api/admin/v1/accounts/{NEW}/role", json={"role": "custodian"}, headers=H())
    roles = owner_client.get("/api/admin/v1/me", headers=auth(make_token(email=NEW))).json()["roles"]
    assert roles == ["custodian", "designer", "editor"]
    assert owner_client.get("/api/admin/v1/custody/pieces", headers=auth(make_token(email=NEW))).status_code == 200
    # Rules: no duplicates, fixed accounts stay, bad input refused.
    assert owner_client.post("/api/admin/v1/accounts", json={"email": NEW, "role": "editor"},
                             headers=H()).json()["error"]["code"] == "account_exists"
    assert owner_client.post(f"/api/admin/v1/accounts/{FIXED}/remove", json={},
                             headers=H()).json()["error"]["code"] == "fixed_account"
    assert owner_client.post("/api/admin/v1/accounts", json={"email": "sin-arroba", "role": "editor"},
                             headers=H()).json()["error"]["code"] == "invalid_email"
    assert owner_client.post("/api/admin/v1/accounts", json={"email": "x@y.com", "role": "owner"},
                             headers=H()).status_code == 422
    # A custodian cannot manage accounts.
    assert owner_client.post("/api/admin/v1/accounts", json={"email": "z@y.com", "role": "editor"},
                             headers=H(email=CUSTODIAN_EMAIL)).status_code == 403
    # Removal takes effect at once.
    owner_client.post(f"/api/admin/v1/accounts/{NEW}/remove", json={}, headers=H())
    assert owner_client.get("/api/admin/v1/me", headers=auth(make_token(email=NEW))).status_code == 403
    actions = [e.action for e in db_session.execute(
        select(AuditEvent).where(AuditEvent.action.like("account.%")).order_by(AuditEvent.occurred_at)).scalars()]
    assert actions == ["account.added", "account.role_changed", "account.removed"]


def test_changes_are_mirrored_into_the_cloudflare_group(owner_client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "access_sync_api_token", "secret-token")
    monkeypatch.setattr(s, "access_sync_account_id", "acct")
    monkeypatch.setattr(s, "access_sync_group_id", "grp")
    calls = []
    monkeypatch.setattr(cloudflare_access, "sync_group", lambda **kw: calls.append(kw) or len(kw["emails"]))
    r = owner_client.post("/api/admin/v1/accounts", json={"email": NEW, "role": "designer"}, headers=H())
    assert r.json()["cloudflare"] == "synced"
    assert calls[-1]["emails"] == {OWNER, FIXED, CUSTODIAN_EMAIL, NEW} and calls[-1]["group_id"] == "grp"
    owner_client.post(f"/api/admin/v1/accounts/{NEW}/remove", json={}, headers=H())
    assert NEW not in calls[-1]["emails"]

    def boom(**_kw):
        raise cloudflare_access.SyncError("Cloudflare respondió 403 (10000)")

    monkeypatch.setattr(cloudflare_access, "sync_group", boom)
    r = owner_client.post("/api/admin/v1/accounts", json={"email": NEW, "role": "editor"}, headers=H())
    # The account still works in Gestión; the page shows the sync error to retry.
    assert r.status_code == 200 and r.json()["cloudflare"].startswith("Cloudflare respondió 403")
    assert "secret-token" not in r.text


def test_cloudflare_group_update_keeps_non_email_rules(monkeypatch):
    seen = {}

    def fake_call(method, url, token, body=None):
        if method == "GET":
            return {"name": "Gestión", "include": [{"email": {"email": "old@x.com"}}, {"email_domain": {"domain": "x.org"}}]}
        seen["body"] = body
        return {}

    monkeypatch.setattr(cloudflare_access, "_call", fake_call)
    cloudflare_access.sync_group(token="t", account_id="a", group_id="g", emails={"b@x.com", "a@x.com"})
    assert seen["body"]["include"] == [{"email_domain": {"domain": "x.org"}},
                                       {"email": {"email": "a@x.com"}}, {"email": {"email": "b@x.com"}}]


# --- G3: authorization to publish ------------------------------------------------------


def test_publishing_requires_the_artisans_authorization(owner_client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "require_artisan_authorization", True)
    c = owner_client
    artisan = c.post("/api/admin/v1/artisans", json={"full_name": "Rigoberto Ramírez", "biography": "Talla máscaras.",
                                                     "validation_whatsapp": "951 123 4567"}, headers=H()).json()
    assert artisan["validation_whatsapp"] == "529511234567"
    blocked = c.post(f"/api/admin/v1/artisans/{artisan['id']}/publish", json={},
                     headers=H(**{"If-Match": artisan["updated_at"]}))
    assert blocked.json()["error"]["code"] == "authorization_missing"

    link = c.post(f"/api/admin/v1/artisans/{artisan['id']}/authorization/request", json={}, headers=H()).json()
    assert link["whatsapp"] == "529511234567" and "/autorizacion/#" in link["url"]
    token = link["url"].split("#", 1)[1]
    opened = c.post("/api/v1/artisan-authorizations/resolve", json={"token": token}).json()
    assert opened["status"] == "open" and opened["full_name"] == "Rigoberto Ramírez" and opened["biography"]
    assert c.post("/api/v1/artisan-authorizations/decision", json={"token": token, "decision": "authorize"}).json() == {
        "status": "recorded"}
    assert c.post("/api/v1/artisan-authorizations/resolve", json={"token": token}).json() == {"status": "unavailable"}

    detail = c.get(f"/api/admin/v1/artisans/{artisan['id']}", headers=auth(make_token(email=OWNER))).json()
    assert detail["authorization"]["status"] == "authorized" and detail["authorization"]["medium"] == "whatsapp"
    ok = c.post(f"/api/admin/v1/artisans/{artisan['id']}/publish", json={}, headers=H(**{"If-Match": detail["updated_at"]}))
    assert ok.status_code == 200 and ok.json()["publication_status"] == "published"
    blob = " ".join(str(e.event_metadata) for e in db_session.execute(select(AuditEvent)).scalars())
    assert token not in blob and "9511234567" not in blob


def test_authorization_link_rules(owner_client, db_session):
    c = owner_client
    artisan = c.post("/api/admin/v1/artisans", json={"full_name": "Otro Artesano"}, headers=H()).json()
    assert c.post("/api/admin/v1/artisans", json={"full_name": "X", "validation_whatsapp": "123"},
                  headers=H()).status_code == 422
    first = c.post(f"/api/admin/v1/artisans/{artisan['id']}/authorization/request", json={}, headers=H()).json()
    second = c.post(f"/api/admin/v1/artisans/{artisan['id']}/authorization/request", json={}, headers=H()).json()
    old, new = first["url"].split("#")[1], second["url"].split("#")[1]
    assert c.post("/api/v1/artisan-authorizations/resolve", json={"token": old}).json() == {"status": "unavailable"}
    db_session.execute(update(ArtisanAuthorization).values(expires_at=func.now() - timedelta(minutes=1)))
    db_session.commit()
    assert c.post("/api/v1/artisan-authorizations/decision",
                  json={"token": new, "decision": "authorize"}).json() == {"status": "unavailable"}
    # Declining, in person, revoking.
    third = c.post(f"/api/admin/v1/artisans/{artisan['id']}/authorization/request", json={}, headers=H()).json()
    c.post("/api/v1/artisan-authorizations/decision",
           json={"token": third["url"].split("#")[1], "decision": "decline", "comment": "Todavía no"})
    detail = c.get(f"/api/admin/v1/artisans/{artisan['id']}", headers=auth(make_token(email=OWNER))).json()
    assert detail["authorization"] is None  # declined is closed; ask again later
    rec = c.post(f"/api/admin/v1/artisans/{artisan['id']}/authorization/record",
                 json={"note": "Firmó la hoja en el taller"}, headers=H()).json()
    assert rec["authorization"]["status"] == "authorized" and rec["authorization"]["medium"] == "en persona"
    assert c.post(f"/api/admin/v1/artisans/{artisan['id']}/authorization/request", json={},
                  headers=H()).json()["error"]["code"] == "already_authorized"
    rev = c.post(f"/api/admin/v1/artisans/{artisan['id']}/authorization/revoke",
                 json={"note": "Pidió retirar su historia"}, headers=H()).json()
    assert rev["authorization"] is None
    big = c.post("/api/v1/artisan-authorizations/resolve", content=b'{"token":"' + b"a" * 2000 + b'"}',
                 headers={"content-type": "application/json"})
    assert big.status_code == 413
