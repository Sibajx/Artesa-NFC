"""services/provisioning.py: eligibility, orchestration and provenance (issue N-09).

Uses the rolled-back `db_session`; the multi-commit CLI flows live in
test_provisioning_cli.py."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.models import Artisan, Piece
from app.models.certificate import CertificateStatus
from app.models.enums import PublicationStatus
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.services import provisioning as prov
from app.services.certificates import hash_certificate_token
from app.services.nfc_tags import InvalidPhysicalUid, NfcTagUidAlreadyRegistered, register_nfc_tag

UID_A = "04:A1:B2:C3:D4:E5:F6"
UID_B = "04:11:22:33:44:55:66"


def _piece(db_session, slug: str, *, published: bool = True, artisan_published: bool = True) -> str:
    artisan = Artisan(
        slug=f"artisan-{slug}",
        full_name=f"Artesano {slug}",
        publication_status=PublicationStatus.published if artisan_published else PublicationStatus.draft,
    )
    db_session.add(artisan)
    db_session.flush()
    piece = Piece(
        slug=slug,
        public_code=f"PC-{slug}",
        artisan_id=artisan.id,
        name=f"Pieza {slug}",
        publication_status=PublicationStatus.published if published else PublicationStatus.draft,
    )
    db_session.add(piece)
    db_session.flush()
    return piece.public_code


def _tag(db_session, code: str, uid: str) -> NfcTag:
    return db_session.query(NfcTag).filter_by(physical_uid=uid).one()


# --- state and eligibility -------------------------------------------------------------------------


def test_unknown_public_code_has_no_state(db_session):
    assert prov.load_piece_state(db_session, "NOPE") is None


def test_public_code_is_matched_exactly_after_trimming(db_session):
    code = _piece(db_session, "exact-1")
    assert prov.load_piece_state(db_session, f"  {code}\n").public_code == code
    assert prov.load_piece_state(db_session, code.lower()) is None


def test_fresh_piece_is_issueable(db_session):
    state = prov.load_piece_state(db_session, _piece(db_session, "fresh-1"))
    assert prov.issue_blockers(state) == []
    assert prov.recommended_action(state) == "issue"
    assert state.active_certificate is None and state.tags == ()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"published": False}, [prov.PIECE_NOT_PUBLISHED]),
        ({"artisan_published": False}, [prov.ARTISAN_NOT_PUBLISHED]),
        ({"published": False, "artisan_published": False}, [prov.PIECE_NOT_PUBLISHED, prov.ARTISAN_NOT_PUBLISHED]),
    ],
)
def test_unpublished_pieces_or_artisans_block_issue_and_rotate(db_session, kwargs, expected):
    state = prov.load_piece_state(db_session, _piece(db_session, "unpub-1", **kwargs))
    assert prov.issue_blockers(state) == expected
    assert prov.PIECE_NOT_PUBLISHED in prov.rotate_blockers(state) or prov.ARTISAN_NOT_PUBLISHED in prov.rotate_blockers(state)


def test_revoke_does_not_depend_on_publication(db_session):
    # Only the missing certificate blocks it: an unpublished piece must still
    # be revocable.
    state = prov.load_piece_state(db_session, _piece(db_session, "unpub-2", published=False))
    assert prov.revoke_blockers(state) == [prov.NO_ACTIVE_CERTIFICATE]


def test_issue_blocks_on_an_active_certificate_and_on_a_tag_in_use(db_session):
    code = _piece(db_session, "block-1")
    prov.execute_issue(db_session, code, UID_A, operator="t")

    state = prov.load_piece_state(db_session, code)

    assert prov.issue_blockers(state) == [prov.ACTIVE_CERTIFICATE_EXISTS, prov.TAG_IN_USE]


def test_recommended_action_follows_the_lifecycle(db_session):
    code = _piece(db_session, "rec-1")
    issued = prov.execute_issue(db_session, code, UID_A, operator="t")
    assert prov.recommended_action(prov.load_piece_state(db_session, code)) == "interrupted_rotate"

    prov.execute_program(db_session, code, issued.tag_id, operator="t")
    assert prov.recommended_action(prov.load_piece_state(db_session, code)) == "verify_then_optional_lock"

    prov.execute_lock(db_session, code, operator="t")
    assert prov.recommended_action(prov.load_piece_state(db_session, code)) == "locked"


# --- execute_issue ------------------------------------------------------------------------------------


def test_issue_registers_assigns_and_issues_but_does_not_program(db_session):
    code = _piece(db_session, "issue-1")

    result = prov.execute_issue(db_session, code, "04a1b2c3d4e5f6", operator="tester")
    state = prov.load_piece_state(db_session, code)

    assert state.active_certificate.id == result.certificate_id
    [tag] = state.tags
    assert tag.id == result.tag_id
    assert tag.physical_uid == UID_A and tag.status == NfcTagStatus.available  # decision F
    assert tag.programmed_at is None and tag.locked_at is None
    assert result.raw_token not in repr(result)
    stored = _tag(db_session, code, UID_A)
    assert "issue" in stored.notes and f"by=tester" in stored.notes
    assert result.raw_token not in stored.notes
    assert hash_certificate_token(result.raw_token) not in stored.notes


def test_issue_rejects_a_bad_uid_before_touching_the_database(db_session):
    code = _piece(db_session, "issue-2")
    with pytest.raises(InvalidPhysicalUid):
        prov.execute_issue(db_session, code, "not-a-uid", operator="t")
    assert prov.load_piece_state(db_session, code).tags == ()


def test_issue_of_an_unknown_piece_or_ineligible_piece_raises_precondition_failed(db_session):
    with pytest.raises(prov.PreconditionFailed) as info:
        prov.execute_issue(db_session, "NOPE", UID_A, operator="t")
    assert info.value.codes == (prov.PIECE_NOT_FOUND,)

    code = _piece(db_session, "issue-3", published=False)
    with pytest.raises(prov.PreconditionFailed) as info:
        prov.execute_issue(db_session, code, UID_A, operator="t")
    assert prov.PIECE_NOT_PUBLISHED in info.value.codes


def test_issue_with_a_duplicate_uid_creates_nothing_for_the_piece(db_session):
    other = _piece(db_session, "issue-4a")
    prov.execute_issue(db_session, other, UID_A, operator="t")
    code = _piece(db_session, "issue-4b")

    with pytest.raises(NfcTagUidAlreadyRegistered):
        prov.execute_issue(db_session, code, UID_A, operator="t")

    state = prov.load_piece_state(db_session, code)
    assert state.active_certificate is None and state.tags == ()


# --- execute_program / lock ---------------------------------------------------------------------------------


def test_program_requires_an_available_tag_and_an_active_certificate(db_session):
    code = _piece(db_session, "prog-1")
    issued = prov.execute_issue(db_session, code, UID_A, operator="t")

    step = prov.execute_program(db_session, code, issued.tag_id, operator="t")
    assert prov.load_piece_state(db_session, code).tags[0].status == NfcTagStatus.programmed
    assert "program" in step.record

    with pytest.raises(prov.PreconditionFailed) as info:  # not available any more
        prov.execute_program(db_session, code, issued.tag_id, operator="t")
    assert info.value.codes == (prov.TAG_NOT_AVAILABLE,)


def test_program_is_refused_when_the_certificate_was_revoked_meanwhile(db_session):
    code = _piece(db_session, "prog-2")
    issued = prov.execute_issue(db_session, code, UID_A, operator="t")
    prov.execute_revoke(db_session, code, reason="other", operator="t")

    with pytest.raises(prov.PreconditionFailed) as info:
        prov.execute_program(db_session, code, issued.tag_id, operator="t")
    assert info.value.codes == (prov.NO_ACTIVE_CERTIFICATE,)


def test_lock_only_from_a_programmed_tag(db_session):
    code = _piece(db_session, "lock-1")
    issued = prov.execute_issue(db_session, code, UID_A, operator="t")

    with pytest.raises(prov.PreconditionFailed) as info:  # still available
        prov.execute_lock(db_session, code, operator="t")
    assert info.value.codes == (prov.NO_PROGRAMMED_TAG,)

    prov.execute_program(db_session, code, issued.tag_id, operator="t")
    prov.execute_lock(db_session, code, operator="t")
    assert prov.load_piece_state(db_session, code).tags[0].status == NfcTagStatus.locked

    with pytest.raises(prov.PreconditionFailed) as info:
        prov.execute_lock(db_session, code, operator="t")
    assert prov.TAG_ALREADY_LOCKED in info.value.codes


# --- execute_rotate ---------------------------------------------------------------------------------------


def _programmed(db_session, slug: str, uid: str = UID_A) -> tuple[str, prov.IssueResult]:
    code = _piece(db_session, slug)
    issued = prov.execute_issue(db_session, code, uid, operator="t")
    prov.execute_program(db_session, code, issued.tag_id, operator="t")
    return code, issued


def test_rotate_same_tag_keeps_the_tag_programmed_and_records_the_reason(db_session):
    code, issued = _programmed(db_session, "rot-1")

    result = prov.execute_rotate(db_session, code, reason="compromised", new_physical_uid=None, operator="t")
    state = prov.load_piece_state(db_session, code)

    assert result.tag_id == issued.tag_id and result.needs_program is False
    assert result.retired_tag_ids == ()
    assert result.raw_token != issued.raw_token
    assert state.active_certificate.id == result.certificate_id
    assert state.tags[0].status == NfcTagStatus.programmed
    assert state.revoked_certificates == 1
    from app.models.certificate import Certificate

    old = db_session.get(Certificate, issued.certificate_id)
    assert old.status == CertificateStatus.revoked and old.revocation_reason == "compromised"


def test_rotate_same_tag_after_an_interrupted_issue_programs_it_later(db_session):
    code = _piece(db_session, "rot-2")
    issued = prov.execute_issue(db_session, code, UID_A, operator="t")  # never programmed

    result = prov.execute_rotate(db_session, code, reason="lost", new_physical_uid=None, operator="t")

    assert result.tag_id == issued.tag_id and result.needs_program is True


def test_rotate_new_tag_retires_the_old_ones_and_leaves_the_new_one_available(db_session):
    code, issued = _programmed(db_session, "rot-3")

    result = prov.execute_rotate(db_session, code, reason="damaged", new_physical_uid=UID_B, operator="t")
    state = prov.load_piece_state(db_session, code)

    assert result.retired_tag_ids == (issued.tag_id,)
    assert result.needs_program is True  # decision I: never `programmed` before the write
    [tag] = state.tags
    assert tag.id == result.tag_id and tag.status == NfcTagStatus.available and tag.physical_uid == UID_B
    old = db_session.get(NfcTag, issued.tag_id)
    assert old.status == NfcTagStatus.retired and "retire" in old.notes


def test_rotate_cannot_rewrite_a_locked_tag(db_session):
    code, _ = _programmed(db_session, "rot-4")
    prov.execute_lock(db_session, code, operator="t")

    with pytest.raises(prov.PreconditionFailed) as info:
        prov.execute_rotate(db_session, code, reason="lost", new_physical_uid=None, operator="t")
    assert info.value.codes == (prov.TAG_LOCKED_REQUIRES_NEW_TAG,)


def test_rotate_with_a_duplicate_new_uid_changes_nothing(db_session):
    other, _ = _programmed(db_session, "rot-5a", UID_B)
    code, issued = _programmed(db_session, "rot-5b", UID_A)

    with pytest.raises(NfcTagUidAlreadyRegistered):
        prov.execute_rotate(db_session, code, reason="lost", new_physical_uid=UID_B, operator="t")

    state = prov.load_piece_state(db_session, code)
    assert state.active_certificate.id == issued.certificate_id  # the old certificate is still active
    assert [t.status for t in state.tags] == [NfcTagStatus.programmed]


def test_rotate_requires_an_active_certificate_and_a_known_reason(db_session):
    code = _piece(db_session, "rot-6")
    with pytest.raises(prov.PreconditionFailed) as info:
        prov.execute_rotate(db_session, code, reason="lost", new_physical_uid=UID_A, operator="t")
    assert prov.NO_ACTIVE_CERTIFICATE in info.value.codes

    code2, _ = _programmed(db_session, "rot-6b")
    with pytest.raises(prov.PreconditionFailed) as info:
        prov.execute_rotate(db_session, code2, reason="because", new_physical_uid=None, operator="t")
    assert info.value.codes == (prov.INVALID_REASON,)


# --- execute_revoke ------------------------------------------------------------------------------------------


def test_revoke_records_the_reason_and_retires_every_tag_of_the_piece(db_session):
    code, issued = _programmed(db_session, "rev-1")

    result = prov.execute_revoke(db_session, code, reason="lost", operator="t")
    state = prov.load_piece_state(db_session, code)

    assert state.active_certificate is None and state.tags == ()
    assert result.retired_tag_ids == (issued.tag_id,)
    from app.models.certificate import Certificate

    cert = db_session.get(Certificate, issued.certificate_id)
    assert cert.status == CertificateStatus.revoked and cert.revocation_reason == "lost"
    assert prov.recommended_action(state) == "issue"  # the piece can be issued again


def test_revoke_without_an_active_certificate_is_refused(db_session):
    code = _piece(db_session, "rev-2")
    with pytest.raises(prov.PreconditionFailed) as info:
        prov.execute_revoke(db_session, code, reason="lost", operator="t")
    assert info.value.codes == (prov.NO_ACTIVE_CERTIFICATE,)


@pytest.mark.parametrize("reason", ["", "free text", "LOST", "compromised; drop table", "A" * 43])
def test_only_fixed_reason_codes_are_accepted(db_session, reason):
    code, _ = _programmed(db_session, "rev-3")
    with pytest.raises(prov.PreconditionFailed) as info:
        prov.execute_revoke(db_session, code, reason=reason, operator="t")
    assert info.value.codes == (prov.INVALID_REASON,)


# --- the in-process self-check ---------------------------------------------------------------------------------------


def test_self_check_needs_the_right_token_piece_and_publication(db_session):
    code = _piece(db_session, "check-1")
    other = _piece(db_session, "check-1b")
    issued = prov.execute_issue(db_session, code, UID_A, operator="t")

    assert prov.verify_token_resolves(db_session, issued.raw_token, code) is True
    assert prov.verify_token_resolves(db_session, issued.raw_token, other) is False  # right token, wrong piece
    assert prov.verify_token_resolves(db_session, "A" * 43, code) is False

    piece = db_session.query(Piece).filter_by(public_code=code).one()
    piece.publication_status = PublicationStatus.draft
    db_session.flush()
    assert prov.verify_token_resolves(db_session, issued.raw_token, code) is False

    piece.publication_status = PublicationStatus.published
    prov.execute_revoke(db_session, code, reason="other", operator="t")
    assert prov.verify_token_resolves(db_session, issued.raw_token, code) is False


# --- provenance ----------------------------------------------------------------------------------------------------------


def test_operation_record_is_one_non_secret_line():
    when = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
    record = prov.operation_record(
        "issue", "PC-1", operator="sibajx", tag_id=None, physical_uid=UID_A, reason="lost", note="x", now=when
    )
    assert record.startswith("2026-09-20T12:00:00Z issue piece=PC-1")
    assert "\n" not in record
    assert f"uid={UID_A}" in record and "reason=lost" in record and "by=sibajx" in record
    assert re.search(r"cli=\d+$", record)


@pytest.mark.parametrize("operator", ["", "a b", "x" * 33, "no\nnewline", "café"])
def test_operator_label_is_sanitized(operator):
    assert "by=unknown" in prov.operation_record("issue", "PC-1", operator=operator)


def test_secret_shaped_text_is_never_stored_in_tag_notes(db_session):
    tag = register_nfc_tag(db_session, physical_uid=UID_A)
    for shaped in ("A" * 43, "x" * 64, "note " + "b" * 40):
        with pytest.raises(ValueError):
            prov._append_note(tag, shaped)
    assert tag.notes is None
    prov._append_note(tag, "a normal note with a uuid 6f0c1c3e-0d61-4a3b-9a55-2f3f7a1b9c10")
    assert tag.notes.endswith("6f0c1c3e-0d61-4a3b-9a55-2f3f7a1b9c10")
