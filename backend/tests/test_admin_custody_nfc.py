"""ADR-030 phase 2: certify a piece and write its tag from Gestión (Web NFC)."""
from __future__ import annotations

from sqlalchemy import select

from app.core.access import ACCESS_JWT_HEADER
from app.models.audit_event import AuditEvent
from app.services import provisioning as prov
from tests.test_admin_api import CUSTODIAN_EMAIL, auth, client, make_token, verifier  # noqa: F401
from tests.test_admin_writes import H, act, new_artisan, new_piece

UID = "04:A1:B2:C3:D4:E5:F6"
OTHER_UID = "04-11-22-33-44-55-66"


def CH(**extra) -> dict[str, str]:
    return H(**{ACCESS_JWT_HEADER: make_token(email=CUSTODIAN_EMAIL), **extra})


def published_piece(client) -> dict:
    artisan = new_artisan(client, name="Rigoberto Ramírez")
    piece = new_piece(client, artisan["id"], name="El Negrito")
    act(client, "artisans", artisan, "publish")
    return act(client, "pieces", piece, "publish").json()


def post(client, piece, action, body, headers=None):
    return client.post(f"/api/admin/v1/custody/pieces/{piece['id']}/{action}", json=body, headers=headers or CH())


def state(client, piece) -> dict:
    return client.get(f"/api/admin/v1/custody/pieces/{piece['id']}/state",
                      headers=auth(make_token(email=CUSTODIAN_EMAIL))).json()


def test_issue_program_lock_end_to_end(client, db_session):
    piece = published_piece(client)
    assert state(client, piece)["recommended_action"] == "issue"

    r = post(client, piece, "issue", {"uid": "04a1b2c3d4e5f6"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    issued = r.json()
    assert issued["url"].startswith(prov.REHEARSAL_URL_BASE) and issued["uid"] == UID and issued["needs_program"]
    token = issued["url"].removeprefix(prov.REHEARSAL_URL_BASE)
    assert prov.verify_token_resolves(db_session, token, piece["public_code"])

    # A different chip read back: refused, nothing changes.
    wrong = post(client, piece, "program", {"tag_id": issued["tag_id"], "uid": OTHER_UID})
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "uid_mismatch"
    ok = post(client, piece, "program", {"tag_id": issued["tag_id"], "uid": UID})
    assert ok.status_code == 200
    assert [t["status"] for t in ok.json()["tags"]] == ["programmed"]
    assert ok.json()["recommended_action"] == "verify_then_optional_lock"

    locked = post(client, piece, "lock", {"uid": UID})
    assert locked.status_code == 200 and [t["status"] for t in locked.json()["tags"]] == ["locked"]

    events = db_session.execute(select(AuditEvent).where(AuditEvent.action.like("custody.%"))
                                .order_by(AuditEvent.occurred_at)).scalars().all()
    assert [e.action for e in events] == ["custody.issued", "custody.programmed", "custody.locked"]
    for e in events:
        assert e.actor_email == CUSTODIAN_EMAIL and token not in str(e.event_metadata)


def test_rotate_same_tag_then_revoke(client):
    piece = published_piece(client)
    issued = post(client, piece, "issue", {"uid": UID}).json()
    post(client, piece, "program", {"tag_id": issued["tag_id"], "uid": UID})
    rotated = post(client, piece, "rotate", {"reason": "compromised"})
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["url"] != issued["url"] and rotated.json()["needs_program"] is False
    # Rewriting the programmed tag is recorded without a status change.
    assert post(client, piece, "program", {"tag_id": issued["tag_id"], "uid": UID}).status_code == 200
    revoked = post(client, piece, "revoke", {"reason": "lost"})
    assert revoked.status_code == 200
    body = revoked.json()
    assert body["certificate_active"] is False and body["tags"] == [] and body["revoked_certificates"] == 2
    assert body["recommended_action"] == "issue"


def test_rules_and_validation(client):
    artisan = new_artisan(client, name="Sin Publicar")
    draft = new_piece(client, artisan["id"], name="Borrador")
    r = post(client, draft, "issue", {"uid": UID})
    assert r.status_code == 409 and r.json()["error"]["code"] == "piece_not_published"

    piece = published_piece(client)
    bad = post(client, piece, "issue", {"uid": "05:A1:B2:C3:D4:E5"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "uid_wrong_length"
    assert post(client, piece, "rotate", {"reason": "nope"}).json()["error"]["code"] in ("no_active_certificate", "invalid_reason")
    post(client, piece, "issue", {"uid": UID})
    again = post(client, piece, "issue", {"uid": OTHER_UID})
    assert again.status_code == 409 and again.json()["error"]["code"] == "active_certificate_exists"


def test_only_custodians_with_the_write_guard(client):
    piece = published_piece(client)
    editor = post(client, piece, "issue", {"uid": UID}, headers=H())
    assert editor.status_code == 403
    no_guard = client.post(f"/api/admin/v1/custody/pieces/{piece['id']}/issue", json={"uid": UID},
                           headers={ACCESS_JWT_HEADER: make_token(email=CUSTODIAN_EMAIL)})
    assert no_guard.status_code == 403
    assert client.get(f"/api/admin/v1/custody/pieces/{piece['id']}/state", headers=auth(make_token())).status_code == 403


def test_compatible_ntag213_clone_is_accepted_from_web_nfc(client, db_session):
    """B-031: the chips the team bought are NTAG213-compatible clones whose
    UID does not start with 04 (NXP). The phone reads the UID from the chip,
    so Gestión accepts any 7-byte UID; the typed CLI path stays strict."""
    clone = "53:44:9e:14:24:00:01"
    piece = published_piece(client)
    issued = post(client, piece, "issue", {"uid": clone})
    assert issued.status_code == 200, issued.text
    assert issued.json()["uid"] == "53:44:9E:14:24:00:01"
    ok = post(client, piece, "program", {"tag_id": issued.json()["tag_id"], "uid": clone})
    assert ok.status_code == 200 and [t["status"] for t in ok.json()["tags"]] == ["programmed"]
    assert post(client, piece, "lock", {"uid": clone}).status_code == 200
    event = db_session.execute(select(AuditEvent).where(AuditEvent.action == "custody.issued")).scalars().one()
    assert event.event_metadata["manufacturer_byte"] == "53"

    from app.services.nfc_tags import InvalidPhysicalUid, normalize_physical_uid
    try:
        normalize_physical_uid(clone)
    except InvalidPhysicalUid as exc:
        assert exc.reason == "not_nxp"
    else:
        raise AssertionError("the CLI path must still require 04")
