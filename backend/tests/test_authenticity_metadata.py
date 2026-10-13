"""PEND-030: the verification and the edition on the public certificate."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.core.access import ACCESS_JWT_HEADER
from app.models.audit_event import AuditEvent
from app.models.certificate import Certificate, CertificateStatus
from app.services import authenticity as auth_svc
from app.services import provisioning as prov
from tests.test_admin_api import ADMIN_EMAIL, auth, client, make_token, verifier  # noqa: F401
from tests.test_admin_custody_nfc import CH, post, published_piece, state
from tests.test_admin_writes import H

UID = "04:A1:B2:C3:D4:E5:F6"
BODY = {"verified_by": "Verificada en persona en el taller por Sol y Alexis", "verified_on": "2026-10-01",
        "edition": "3 de 10", "notes": "Caso piloto de Cuilápam"}


def put(client, piece, body, headers=None):
    return client.put(f"/api/admin/v1/custody/pieces/{piece['id']}/authenticity", json=body, headers=headers or CH())


def certified(client):
    piece = published_piece(client)
    issued = post(client, piece, "issue", {"uid": UID}).json()
    return piece, issued["url"].removeprefix(prov.REHEARSAL_URL_BASE)


def public(client, token) -> dict:
    r = client.post("/api/v1/certificates/resolve", json={"token": token})
    assert r.status_code == 200
    return r.json()["authenticity_metadata"]


# --- the allowlist ---------------------------------------------------------------


def test_projection_keeps_only_the_four_keys_with_the_right_types():
    got = auth_svc.project({"verified_by": "  Sol  ", "verified_on": "2026-10-01", "edition": 3, "notes": "n",
                            "internal_secret": "x", "other": {"a": 1}})
    assert (got.verified_by, got.verified_on, got.edition, got.notes) == ("Sol", date(2026, 10, 1), None, "n")
    assert auth_svc.project(None) == auth_svc.AuthenticityPublic(None, None, None, None)
    assert auth_svc.project({"verified_on": "not-a-date"}).verified_on is None


# --- Gestión writes, the public reads ---------------------------------------------


def test_set_shows_in_gestion_and_on_the_public_certificate(client, db_session):
    piece, token = certified(client)
    assert public(client, token) == {"notes": None, "verified_by": None, "verified_on": None, "edition": None}

    r = put(client, piece, BODY)
    assert r.status_code == 200, r.text
    assert r.json() == BODY
    assert state(client, piece)["authenticity"] == BODY
    assert public(client, token) == BODY

    event = db_session.execute(select(AuditEvent).where(AuditEvent.action == "custody.authenticity_set")).scalars().one()
    assert event.event_metadata["keys"] == ["edition", "notes", "verified_by", "verified_on"]
    assert BODY["verified_by"] not in str(event.event_metadata)


def test_empty_values_remove_the_keys_and_other_keys_are_kept_and_never_public(client, db_session):
    piece, token = certified(client)
    cert = db_session.execute(select(Certificate).where(Certificate.status == CertificateStatus.active)).scalars().one()
    cert.authenticity_metadata = {"internal_secret": "do-not-show"}
    db_session.commit()

    put(client, piece, BODY)
    assert "do-not-show" not in client.post("/api/v1/certificates/resolve", json={"token": token}).text
    r = put(client, piece, {"verified_by": "  ", "edition": "Pieza única"})
    assert r.json() == {"verified_by": None, "verified_on": None, "edition": "Pieza única", "notes": None}
    db_session.refresh(cert)
    assert cert.authenticity_metadata == {"internal_secret": "do-not-show", "edition": "Pieza única"}


def test_a_rotation_keeps_the_verification(client):
    piece, token = certified(client)
    put(client, piece, BODY)
    rotated = post(client, piece, "rotate", {"reason": "compromised"}).json()
    new_token = rotated["url"].removeprefix(prov.REHEARSAL_URL_BASE)
    assert new_token != token
    assert public(client, new_token) == BODY


# --- refusals ---------------------------------------------------------------------


def test_future_date_and_long_text_are_refused_with_the_field(client):
    piece, _ = certified(client)
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    r = put(client, piece, {"verified_on": tomorrow})
    assert r.status_code == 422 and r.json()["error"]["code"] == "verified_on_in_the_future"
    r = put(client, piece, {"edition": "x" * 61})
    assert r.status_code == 422 and r.json()["error"]["code"] == "edition_too_long"
    assert put(client, piece, {"notes": "x" * 1001}).status_code == 422
    assert put(client, piece, {"verified_on": "ayer"}).status_code == 422


def test_no_active_certificate_is_a_409(client):
    piece = published_piece(client)
    r = put(client, piece, BODY)
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_active_certificate"


def test_editors_cannot_write_it(client):
    piece, _ = certified(client)
    r = put(client, piece, BODY, headers=H(**{ACCESS_JWT_HEADER: make_token()}))
    assert r.status_code == 403
