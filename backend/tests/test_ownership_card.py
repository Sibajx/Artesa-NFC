"""ADR-030 phase 3: the buyer's card, unlocking the original, claiming the
piece, and the custodian's card / claim / stolen actions."""
from __future__ import annotations

from sqlalchemy import select

from app.models.audit_event import AuditEvent, AuditResult
from app.services import ownership as own
from app.services import provisioning as prov
from tests.test_admin_api import CUSTODIAN_EMAIL, auth, client, make_token, verifier  # noqa: F401
from tests.test_admin_custody_nfc import CH, UID, post, published_piece, state
from tests.test_admin_writes import H

NOTE = {"note": "Ticket revisado en persona"}
EMAIL = "Compradora@Example.com"


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


def test_replace_block_transfer(client):
    piece, token = certified(client)
    first = card(client, piece)["key"]
    claim(client, token, first)

    # Lost card: a new key; the claim (and its PIN) stays with the owner.
    second = card(client, piece, "card/replace", NOTE)["key"]
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

    # Forgotten PIN: release the claim; the owner claims again with the card.
    claim(client, token, third)
    post(client, piece, "claim/release", NOTE)
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
