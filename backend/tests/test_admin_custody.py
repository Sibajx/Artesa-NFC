"""ADR-030 phase 1: roles and the custody area."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.db_safety import UnsafeConfigurationError
from app.models.audit_event import AuditEvent, AuditResult
from tests.test_admin_api import (  # noqa: F401  (fixtures)
    ADMIN_EMAIL, CUSTODIAN_EMAIL, _seed, auth, client, make_token, verifier,
)


def test_roles_are_reported_by_me(client):
    me = client.get("/api/admin/v1/me", headers=auth(make_token(email=CUSTODIAN_EMAIL))).json()
    assert me == {"email": CUSTODIAN_EMAIL, "roles": ["custodian", "designer", "editor"]}


def test_editors_do_not_see_certificates_or_tags(client, db_session):
    rows = _seed(db_session)
    body = client.get(f"/api/admin/v1/pieces/{rows['public_piece'].id}", headers=auth(make_token())).json()
    assert body["custody_visible"] is False
    assert body["certificates"] == [] and body["nfc_tags"] == []


def test_custody_is_forbidden_to_editors_and_the_attempt_is_audited(client, db_session):
    r = client.get("/api/admin/v1/custody/pieces", headers=auth(make_token()))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    event = db_session.execute(select(AuditEvent).where(AuditEvent.action == "custody.denied")).scalars().one()
    assert (event.actor_email, event.result, event.entity_type) == (ADMIN_EMAIL, AuditResult.failure, "custody")
    assert event.event_metadata == {"path": "/api/admin/v1/custody/pieces"}


def test_custody_needs_a_token(client):
    assert client.get("/api/admin/v1/custody/pieces").status_code == 401


def test_custodians_get_the_certification_overview(client, db_session):
    rows = _seed(db_session)
    r = client.get("/api/admin/v1/custody/pieces", headers=auth(make_token(email=CUSTODIAN_EMAIL)))
    assert r.status_code == 200
    by_slug = {p["slug"]: p for p in r.json()["data"]}
    public = by_slug[rows["public_piece"].slug]
    assert (public["certificate_status"], public["tag_status"], public["ready_to_certify"]) == ("active", "available", False)
    draft = by_slug[rows["draft_piece"].slug]
    assert (draft["certificate_status"], draft["ready_to_certify"]) == (None, False)
    for forbidden in ("token_hash", "physical_uid"):
        assert forbidden not in r.text


@pytest.mark.parametrize("env, message", [
    ({"CUSTODIAN_EMAILS": "other@example.org"}, "Every address in CUSTODIAN_EMAILS must also be in ADMIN_EMAILS"),
    ({"DESIGNER_EMAILS": "not-an-email"}, "DESIGNER_EMAILS must be a comma-separated list"),
    ({"CUSTODY_ACCESS_AUD": "nope"}, "CUSTODY_ACCESS_AUD must be the 64-hex-character AUD tag"),
])
def test_role_configuration_is_validated(monkeypatch, env, message):
    base = {"ADMIN_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com", "ADMIN_ACCESS_AUD": "a" * 64,
            "ADMIN_EMAILS": "ops@example.org"}
    for key, value in {**base, **env}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(UnsafeConfigurationError, match=message):
        Settings()
