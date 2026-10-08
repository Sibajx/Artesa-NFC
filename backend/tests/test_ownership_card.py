"""ADR-030 phase 3: the buyer's card, unlocking the original, claiming the
piece, and the custodian's card / claim / stolen actions."""
from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.models.audit_event import AuditEvent, AuditResult
from app.services import mailer
from app.services import ownership as own
from app.services import provisioning as prov
from tests.test_admin_api import CUSTODIAN_EMAIL, auth, client, make_token, verifier  # noqa: F401
from tests.test_admin_custody_nfc import CH, UID, post, published_piece, state
from tests.test_admin_writes import H

NOTE = {"note": "Ticket revisado en persona"}
EMAIL = "Compradora@Example.com"


@pytest.fixture()
def outbox(monkeypatch):
    """Captures the emails the app would send: (to, subject, body)."""
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def last_code(outbox) -> str:
    return re.search(r"\b(\d{6})\b", outbox[-1][2]).group(1)


def confirm_owner(client, piece, outbox) -> None:
    """The custodian emails a code, the owner reads it out, the custodian confirms."""
    assert post(client, piece, "owner-code/send", {}).status_code == 200
    assert post(client, piece, "owner-code/verify", {"code": last_code(outbox)}).status_code == 200


def age_codes(db_session) -> None:
    """Pretends the codes sent so far were sent two minutes ago (the
    one-per-minute rule would otherwise stop a second send in the same test)."""
    from sqlalchemy import text
    db_session.execute(text("UPDATE owner_verification SET created_at = created_at - interval '2 minutes'"))


def certified(client) -> tuple[dict, str]:
    """A published piece with an active certificate; returns it and its token."""
    piece = published_piece(client)
    issued = post(client, piece, "issue", {"uid": UID}).json()
    return piece, issued["url"].removeprefix(prov.REHEARSAL_URL_BASE)


def card(client, piece, action="card/issue", body=None) -> dict:
    r = post(client, piece, action, body if body is not None else {})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


def unlock(client, token, key, pin=None):
    return client.post("/api/v1/certificates/unlock", json={"token": token, "key": key, "pin": pin})


def claim(client, token, key, email=EMAIL, pin="482915"):
    return client.post("/api/v1/certificates/claim", json={"token": token, "key": key, "email": email, "pin": pin})


def test_card_unlock_claim_pin(client, db_session):
    piece, token = certified(client)
    issued = card(client, piece)
    key = issued["key"]
    assert len(key) == 12 and key[4] == "-" and key[9] == "-" and issued["public_code"] == piece["public_code"]
    assert state(client, piece)["card"]["status"] == "active"

    assert unlock(client, token, "AAAA-AAAA-AA").json() == {"result": "invalid"}
    assert unlock(client, "x" * 43, key).json() == {"result": "invalid"}
    # Typed loosely: lower case, no dashes, O for 0 and I/L for 1.
    loose = key.replace("-", "").lower().replace("0", "o").replace("1", "l")
    r = unlock(client, token, loose)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["result"] == "unlocked" and body["piece"]["public_code"] == piece["public_code"]
    assert body["ownership"]["claimed"] is False and body["ownership"]["owner_email_masked"] is None

    assert claim(client, token, key, pin="123456").json()["error"]["code"] == "weak_pin"
    assert claim(client, token, key, pin="12ab56").json()["error"]["code"] == "invalid_pin"
    assert claim(client, token, key, email="sin-arroba").json()["error"]["code"] == "invalid_email"
    claimed = claim(client, token, key).json()
    assert claimed["result"] == "unlocked" and claimed["ownership"]["claimed"] is True
    assert claimed["ownership"]["owner_email_masked"] == "c***@example.com"
    assert state(client, piece)["claim"]["owner_email"] == "compradora@example.com"

    # From now on the card alone is not enough, and nobody can claim again.
    assert unlock(client, token, key).json() == {"result": "pin_required"}
    assert unlock(client, token, key, pin="000001").json() == {"result": "invalid"}
    assert unlock(client, token, key, pin="482915").json()["result"] == "unlocked"
    assert claim(client, token, key, email="otra@example.com", pin="771204").json() == {"result": "pin_required"}

    # No secret or buyer email in the audit trail.
    events = db_session.execute(select(AuditEvent)).scalars().all()
    blob = " ".join(str(e.event_metadata) for e in events)
    for secret in (key, key.replace("-", ""), "482915", token, "compradora"):
        assert secret not in blob
    actions = {e.action for e in events}
    assert {"custody.card_issued", "ownership.unlocked", "ownership.claimed", own.UNLOCK_FAILED} <= actions


def test_lockout_and_custodian_unblock(client):
    piece, token = certified(client)
    key = card(client, piece)["key"]
    for _ in range(own.CARD_FREE_FAILURES):
        assert unlock(client, token, "AAAA-AAAA-AA").json() == {"result": "invalid"}
    locked = unlock(client, token, key)
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "too_many_attempts"
    assert locked.json()["error"]["retry_after"] > 0
    s = state(client, piece)["card"]
    assert s["failed_attempts"] == own.CARD_FREE_FAILURES and s["locked_until"]

    assert post(client, piece, "card/unblock", {"note": "x"}).status_code == 422  # note too short
    assert post(client, piece, "card/unblock", NOTE).status_code == 200
    assert unlock(client, token, key).json()["result"] == "unlocked"


def test_replace_block_transfer(client, outbox, db_session):
    piece, token = certified(client)
    first = card(client, piece)["key"]
    claim(client, token, first)

    # Lost card: the owner is confirmed by email first, then a new key; the
    # claim (and its PIN) stays with the owner.
    assert post(client, piece, "card/replace", NOTE).json()["error"]["code"] == "owner_not_verified"
    confirm_owner(client, piece, outbox)
    second = card(client, piece, "card/replace", NOTE)["key"]
    assert post(client, piece, "card/replace", NOTE).json()["error"]["code"] == "owner_not_verified"  # works once
    assert second != first
    assert unlock(client, token, first, pin="482915").json() == {"result": "invalid"}
    assert unlock(client, token, second, pin="482915").json()["result"] == "unlocked"

    # Stolen wallet: block at once.
    post(client, piece, "card/block", NOTE)
    assert state(client, piece)["card"]["status"] == "blocked"
    assert unlock(client, token, second, pin="482915").json() == {"result": "invalid"}
    assert post(client, piece, "card/block", NOTE).json()["error"]["code"] == "card_already_blocked"

    # Sold: the claim is released and a fresh card is issued.
    third = card(client, piece, "transfer", NOTE)["key"]
    after = state(client, piece)
    assert after["claim"] is None and after["card"]["status"] == "active"
    assert unlock(client, token, third).json()["ownership"]["claimed"] is False

    # Forgotten PIN at the desk: confirm by email, release the claim; the owner claims again.
    claim(client, token, third)
    assert post(client, piece, "claim/release", NOTE).json()["error"]["code"] == "owner_not_verified"
    age_codes(db_session)
    confirm_owner(client, piece, outbox)
    assert post(client, piece, "claim/release", NOTE).status_code == 200
    assert unlock(client, token, third).json()["result"] == "unlocked"


def test_reported_stolen(client):
    piece, token = certified(client)
    key = card(client, piece)["key"]
    post(client, piece, "stolen", NOTE)
    resolved = client.post("/api/v1/certificates/resolve", json={"token": token}).json()
    assert resolved["authenticity"]["status"] == "authentic" and resolved["authenticity"]["reported_stolen"] is True
    assert unlock(client, token, key).json() == {"result": "reported_stolen"}
    assert post(client, piece, "stolen", NOTE).json()["error"]["code"] == "already_reported_stolen"
    cleared = post(client, piece, "stolen/clear", NOTE).json()
    assert cleared["reported_stolen_at"] is None
    assert unlock(client, token, key).json()["result"] == "unlocked"


def test_card_rules_and_roles(client):
    piece = published_piece(client)
    assert post(client, piece, "card/issue", {}).json()["error"]["code"] == "no_active_certificate"
    post(client, piece, "issue", {"uid": UID})
    card(client, piece)
    assert post(client, piece, "card/issue", {}).json()["error"]["code"] == "card_exists"
    assert post(client, piece, "card/replace", {}).status_code == 422  # a note is required
    assert post(client, piece, "card/issue", {}, headers=H()).status_code == 403  # editor


def test_unlock_body_limit(client):
    r = client.post("/api/v1/certificates/unlock", content=b'{"token":"' + b"a" * 2000 + b'"}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_ip_limit_counts_recent_failures(db_session):
    ip = "203.0.113.7"
    assert not own.ip_is_limited(db_session, ip)
    for _ in range(own.IP_MAX_FAILURES):
        own._audit(db_session, action=own.UNLOCK_FAILED, result=AuditResult.failure, piece_id=None, ip=ip)
    db_session.flush()
    assert own.ip_is_limited(db_session, ip)
    assert not own.ip_is_limited(db_session, "203.0.113.8")


def test_secrets_helpers():
    key = own.generate_card_key()
    assert own.normalize_card_key(own.format_card_key(key)) == key
    assert own.normalize_card_key("short") is None and own.normalize_card_key("UUUUUUUUUU") is None
    stored = own.hash_secret(key)
    assert stored.startswith("scrypt$") and key not in stored
    other = ("Y" if key[0] == "X" else "X") + key[1:]
    assert own.verify_secret(key, stored) and not own.verify_secret(other, stored)
    assert not own.verify_secret(key, "garbage")
    assert own.mask_email("ana@example.com") == "a***@example.com"


def test_owner_code_for_the_custodian(client, outbox, db_session):
    piece, token = certified(client)
    key = card(client, piece)["key"]
    assert post(client, piece, "owner-code/send", {}).json()["error"]["code"] == "not_claimed"
    # Without a claim there is nobody to confirm: replacing the card needs nothing.
    key = card(client, piece, "card/replace", NOTE)["key"]
    claim(client, token, key)

    sent = post(client, piece, "owner-code/send", {})
    assert sent.status_code == 200 and sent.json() == {"sent_to": "compradora@example.com"}
    assert outbox[-1][0] == "compradora@example.com" and "ArtesaNFC" in outbox[-1][1]
    assert key not in outbox[-1][2] and "482915" not in outbox[-1][2]
    assert post(client, piece, "owner-code/send", {}).json()["error"]["code"] == "code_rate_limited"

    wrong = "000000" if last_code(outbox) != "000000" else "000001"
    assert post(client, piece, "owner-code/verify", {"code": wrong}).json()["error"]["code"] == "code_invalid"
    assert post(client, piece, "owner-code/verify", {"code": "12"}).status_code == 422
    assert state(client, piece)["claim"]["verified_until"] is None
    ok = post(client, piece, "owner-code/verify", {"code": last_code(outbox)})
    assert ok.status_code == 200 and ok.json()["claim"]["verified_until"]
    # Used up: it does not serve twice.
    assert post(client, piece, "claim/release", NOTE).status_code == 200
    assert post(client, piece, "owner-code/send", {}).json()["error"]["code"] == "not_claimed"

    events = db_session.execute(select(AuditEvent)).scalars().all()
    assert {"custody.owner_code_sent", "custody.owner_verified"} <= {e.action for e in events}
    assert last_code(outbox) not in " ".join(str(e.event_metadata) for e in events)


def test_owner_code_expires_and_locks(client, outbox, db_session):
    piece, token = certified(client)
    claim(client, token, card(client, piece)["key"])
    post(client, piece, "owner-code/send", {})
    good = last_code(outbox)
    wrong = "000000" if good != "000000" else "000001"
    for _ in range(own.CODE_MAX_ATTEMPTS):
        assert post(client, piece, "owner-code/verify", {"code": wrong}).json()["error"]["code"] == "code_invalid"
    # Too many tries: even the right code no longer serves.
    assert post(client, piece, "owner-code/verify", {"code": good}).json()["error"]["code"] == "code_invalid"

    from app.models.ownership import OwnerVerification
    row = db_session.execute(select(OwnerVerification)).scalars().first()
    assert row.code_hash.startswith("scrypt$") and good not in row.code_hash


def test_forgotten_pin_reset_by_email(client, outbox):
    piece, token = certified(client)
    key = card(client, piece)["key"]
    claim(client, token, key)

    def request(k=key):
        return client.post("/api/v1/certificates/pin-reset/request", json={"token": token, "key": k})

    def confirm(code, pin="739204", k=key):
        return client.post("/api/v1/certificates/pin-reset/confirm",
                           json={"token": token, "key": k, "code": code, "pin": pin})

    # A wrong key answers the same and sends nothing.
    assert request("AAAA-AAAA-AA").json() == {"result": "sent"} and outbox == []
    assert request().json() == {"result": "sent"} and len(outbox) == 1
    assert outbox[0][0] == "compradora@example.com" and key not in outbox[0][2]
    code = last_code(outbox)
    assert request().status_code == 429  # one every minute

    assert confirm(code, pin="123456").json()["error"]["code"] == "weak_pin"
    assert confirm(code, pin="12").json()["error"]["code"] == "invalid_pin"
    wrong = "000000" if code != "000000" else "000001"
    assert confirm(wrong).json() == {"result": "invalid"}
    assert confirm(code, k="AAAA-AAAA-AA").json() == {"result": "invalid"}
    done = confirm(code)
    assert done.status_code == 200 and done.json()["result"] == "unlocked"
    assert done.headers["cache-control"] == "no-store"

    # The new PIN works, the old one and the used code do not.
    assert unlock(client, token, key, pin="739204").json()["result"] == "unlocked"
    assert unlock(client, token, key, pin="482915").json() == {"result": "invalid"}
    assert confirm(code, pin="581937").json() == {"result": "invalid"}


def test_pin_reset_edge_cases(client, outbox):
    piece, token = certified(client)
    key = card(client, piece)["key"]
    url = "/api/v1/certificates/pin-reset/request"
    # Not claimed yet: the card alone opens it, there is no PIN to reset.
    assert client.post(url, json={"token": token, "key": key}).json() == {"result": "not_claimed"}
    claim(client, token, key)
    post(client, piece, "stolen", NOTE)
    assert client.post(url, json={"token": token, "key": key}).json() == {"result": "reported_stolen"}
    assert outbox == []


def test_pin_reset_without_mail_settings(client):
    """No SMTP settings: the owner is told, the custodian gets a conflict, nothing is stored."""
    piece, token = certified(client)
    key = card(client, piece)["key"]
    claim(client, token, key)
    r = client.post("/api/v1/certificates/pin-reset/request", json={"token": token, "key": key})
    assert r.status_code == 503 and r.json()["error"]["code"] == "mail_unavailable"
    assert post(client, piece, "owner-code/send", {}).json()["error"]["code"] == "mail_unavailable"
    assert unlock(client, token, key, pin="482915").json()["result"] == "unlocked"


def test_owner_override_when_the_email_is_lost(client, outbox, db_session):
    """Only the project owner may skip the email code, with a documented reason."""
    from app.main import app
    from app.core.access import AccessVerifier, get_access_verifier
    from tests import test_admin_api as t
    piece, token = certified(client)
    key = card(client, piece)["key"]
    claim(client, token, key)
    body = {"note": "Mostró su cédula y la factura de compra", "override_owner_check": True}

    # A custodian who is not the project owner may not use it: nothing changes.
    r = post(client, piece, "claim/release", body)
    assert r.status_code == 403 and state(client, piece)["claim"] is not None

    # The project owner needs a real note, and the override is audited.
    app.dependency_overrides[get_access_verifier] = lambda: AccessVerifier(
        t.TEAM, t.AUD, (t.ADMIN_EMAIL, CUSTODIAN_EMAIL), jwks_client=t.FakeJWKS(),
        custodians=(CUSTODIAN_EMAIL,), owners=(CUSTODIAN_EMAIL,))
    short = post(client, piece, "claim/release", {"note": "cédula vista", "override_owner_check": True})
    assert short.json()["error"]["code"] == "override_note_required"
    assert post(client, piece, "claim/release", body).status_code == 200
    assert state(client, piece)["claim"] is None
    events = db_session.execute(select(AuditEvent).where(AuditEvent.action == "custody.claim_released")).scalars().all()
    assert events[-1].event_metadata["owner_check_overridden"] is True

    # Same for replacing the card of a piece with a new owner.
    claim(client, token, card(client, piece, "transfer", NOTE)["key"])
    assert post(client, piece, "card/replace", body).status_code == 200
