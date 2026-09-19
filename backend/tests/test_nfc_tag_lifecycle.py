from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Artisan, Piece
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.services.nfc_tags import (
    ActiveNfcTagAlreadyExists,
    ActiveNfcTagNotFound,
    InvalidNfcTagTransition,
    NfcTagAlreadyAssigned,
    NfcTagReplacementResult,
    assign_nfc_tag,
    lock_nfc_tag,
    program_nfc_tag,
    replace_nfc_tag,
    retire_nfc_tag,
)


def _make_piece(db_session, slug: str) -> Piece:
    artisan = Artisan(slug=f"artisan-for-{slug}", full_name="Artisan")
    db_session.add(artisan)
    db_session.flush()
    piece = Piece(slug=slug, public_code=f"PC-{slug}", artisan_id=artisan.id, name="Piece")
    db_session.add(piece)
    db_session.flush()
    return piece


def _available_tag(db_session, *, physical_uid: str | None = None) -> NfcTag:
    tag = NfcTag(chip_model="NTAG213", status=NfcTagStatus.available, physical_uid=physical_uid)
    db_session.add(tag)
    db_session.flush()
    return tag


def _assigned_tag(db_session, piece: Piece, *, physical_uid: str | None = None) -> NfcTag:
    tag = _available_tag(db_session, physical_uid=physical_uid)
    return assign_nfc_tag(db_session, tag, piece)


def _programmed_tag(db_session, piece: Piece, *, physical_uid: str | None = None) -> NfcTag:
    tag = _assigned_tag(db_session, piece, physical_uid=physical_uid)
    return program_nfc_tag(db_session, tag)


def _locked_tag(db_session, piece: Piece, *, physical_uid: str | None = None) -> NfcTag:
    tag = _programmed_tag(db_session, piece, physical_uid=physical_uid)
    return lock_nfc_tag(db_session, tag)


# --- Assignment --------------------------------------------------------------


def test_assignment_available_tag_can_be_assigned(db_session):
    piece = _make_piece(db_session, "piece-assign-ok")
    tag = _available_tag(db_session)

    assign_nfc_tag(db_session, tag, piece)

    assert tag.piece_id == piece.id


def test_assignment_preserves_available_status(db_session):
    piece = _make_piece(db_session, "piece-assign-status")
    tag = _available_tag(db_session)

    assign_nfc_tag(db_session, tag, piece)

    assert tag.status == NfcTagStatus.available


def test_assignment_does_not_set_programmed_at(db_session):
    piece = _make_piece(db_session, "piece-assign-no-programmed-at")
    tag = _available_tag(db_session)

    assign_nfc_tag(db_session, tag, piece)

    assert tag.programmed_at is None


def test_assignment_rejects_non_available_tag(db_session):
    piece = _make_piece(db_session, "piece-assign-bad-status")
    tag = _programmed_tag(db_session, piece)

    other_piece = _make_piece(db_session, "piece-assign-bad-status-other")
    with pytest.raises(InvalidNfcTagTransition):
        assign_nfc_tag(db_session, tag, other_piece)


def test_assignment_rejects_already_assigned_tag(db_session):
    piece = _make_piece(db_session, "piece-assign-dup-a")
    other_piece = _make_piece(db_session, "piece-assign-dup-b")
    tag = _assigned_tag(db_session, piece)

    with pytest.raises(NfcTagAlreadyAssigned):
        assign_nfc_tag(db_session, tag, other_piece)


# --- Programming ---------------------------------------------------------------


def test_programming_assigned_available_tag_succeeds(db_session):
    piece = _make_piece(db_session, "piece-program-ok")
    tag = _assigned_tag(db_session, piece)

    program_nfc_tag(db_session, tag)

    assert tag.status == NfcTagStatus.programmed
    assert tag.piece_id == piece.id


def test_programming_sets_programmed_at(db_session):
    piece = _make_piece(db_session, "piece-program-sets-at")
    tag = _assigned_tag(db_session, piece)

    program_nfc_tag(db_session, tag)

    assert tag.programmed_at is not None


def test_programming_without_piece_rejected(db_session):
    tag = _available_tag(db_session)

    with pytest.raises(InvalidNfcTagTransition):
        program_nfc_tag(db_session, tag)


def test_programming_invalid_state_rejected(db_session):
    piece = _make_piece(db_session, "piece-program-bad-state")
    tag = _locked_tag(db_session, piece)

    with pytest.raises(InvalidNfcTagTransition):
        program_nfc_tag(db_session, tag)


def test_programming_rejects_when_piece_already_has_active_tag(db_session):
    piece = _make_piece(db_session, "piece-program-conflict")
    _programmed_tag(db_session, piece)

    second_tag = _assigned_tag(db_session, piece)
    with pytest.raises(ActiveNfcTagAlreadyExists):
        program_nfc_tag(db_session, second_tag)


def test_db_constraint_rejects_duplicate_active_tag_bypassing_service_check(db_session):
    """The application-level check in `program_nfc_tag` is a courtesy; the
    partial unique index `uq_nfc_tag_one_active_per_piece` (DATA_MODEL.md
    section 4 restriction B) is what actually guarantees at most one active
    tag per piece, even if something inserts a second active row directly
    through the ORM without going through the service at all.
    """
    piece = _make_piece(db_session, "piece-program-db-safety")
    _programmed_tag(db_session, piece)

    db_session.add(NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.locked))
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- Locking -------------------------------------------------------------------


def test_locking_programmed_to_locked_succeeds(db_session):
    piece = _make_piece(db_session, "piece-lock-ok")
    tag = _programmed_tag(db_session, piece)

    lock_nfc_tag(db_session, tag)

    assert tag.status == NfcTagStatus.locked


def test_locking_sets_locked_at(db_session):
    piece = _make_piece(db_session, "piece-lock-sets-at")
    tag = _programmed_tag(db_session, piece)

    lock_nfc_tag(db_session, tag)

    assert tag.locked_at is not None


def test_locking_preserves_programmed_at(db_session):
    piece = _make_piece(db_session, "piece-lock-preserves-programmed-at")
    tag = _programmed_tag(db_session, piece)
    programmed_at_before = tag.programmed_at

    lock_nfc_tag(db_session, tag)

    assert tag.programmed_at == programmed_at_before


def test_locking_preserves_piece_association(db_session):
    piece = _make_piece(db_session, "piece-lock-preserves-piece")
    tag = _programmed_tag(db_session, piece)

    lock_nfc_tag(db_session, tag)

    assert tag.piece_id == piece.id


def test_locking_rejects_available_source(db_session):
    tag = _available_tag(db_session)

    with pytest.raises(InvalidNfcTagTransition):
        lock_nfc_tag(db_session, tag)


def test_locking_rejects_replaced_source(db_session):
    piece = _make_piece(db_session, "piece-lock-rejects-replaced")
    tag = NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.replaced)
    db_session.add(tag)
    db_session.flush()

    with pytest.raises(InvalidNfcTagTransition):
        lock_nfc_tag(db_session, tag)


def test_locking_rejects_retired_source(db_session):
    piece = _make_piece(db_session, "piece-lock-rejects-retired")
    tag = NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.retired)
    db_session.add(tag)
    db_session.flush()

    with pytest.raises(InvalidNfcTagTransition):
        lock_nfc_tag(db_session, tag)


def test_locking_rejects_already_locked(db_session):
    piece = _make_piece(db_session, "piece-lock-rejects-locked")
    tag = _locked_tag(db_session, piece)

    with pytest.raises(InvalidNfcTagTransition):
        lock_nfc_tag(db_session, tag)


# --- Retirement ------------------------------------------------------------------


def test_retirement_from_available_succeeds(db_session):
    tag = _available_tag(db_session)

    retire_nfc_tag(db_session, tag)

    assert tag.status == NfcTagStatus.retired


def test_retirement_from_programmed_succeeds(db_session):
    piece = _make_piece(db_session, "piece-retire-from-programmed")
    tag = _programmed_tag(db_session, piece)

    retire_nfc_tag(db_session, tag)

    assert tag.status == NfcTagStatus.retired


def test_retirement_from_locked_succeeds(db_session):
    piece = _make_piece(db_session, "piece-retire-from-locked")
    tag = _locked_tag(db_session, piece)

    retire_nfc_tag(db_session, tag)

    assert tag.status == NfcTagStatus.retired


def test_retired_tag_is_not_active(db_session):
    piece = _make_piece(db_session, "piece-retire-not-active")
    tag = _locked_tag(db_session, piece)

    retire_nfc_tag(db_session, tag)

    active = db_session.execute(
        select(NfcTag).where(
            NfcTag.piece_id == piece.id,
            NfcTag.status.in_([NfcTagStatus.programmed, NfcTagStatus.locked]),
        )
    ).scalar_one_or_none()
    assert active is None


def test_retirement_rejects_replaced_source(db_session):
    piece = _make_piece(db_session, "piece-retire-rejects-replaced")
    tag = NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.replaced)
    db_session.add(tag)
    db_session.flush()

    with pytest.raises(InvalidNfcTagTransition):
        retire_nfc_tag(db_session, tag)


def test_retirement_rejects_already_retired(db_session):
    tag = _available_tag(db_session)
    retire_nfc_tag(db_session, tag)

    with pytest.raises(InvalidNfcTagTransition):
        retire_nfc_tag(db_session, tag)


def test_retirement_from_locked_preserves_history_fields(db_session):
    """Issue #70 (approved 2026-09-18): retirement must preserve physical_uid,
    piece_id, programmed_at, locked_at, and notes untouched — including
    locked_at, which the widened `ck_nfc_tag_locked_at_matches_status`
    constraint now explicitly permits to survive past `locked`.
    """
    piece = _make_piece(db_session, "piece-retire-preserves-history")
    tag = _locked_tag(db_session, piece, physical_uid="uid-retire-history")
    tag.notes = "field note"
    db_session.flush()

    piece_id_before = tag.piece_id
    programmed_at_before = tag.programmed_at
    locked_at_before = tag.locked_at
    physical_uid_before = tag.physical_uid
    notes_before = tag.notes

    retire_nfc_tag(db_session, tag)

    assert tag.status == NfcTagStatus.retired
    assert tag.piece_id == piece_id_before
    assert tag.programmed_at == programmed_at_before
    assert tag.locked_at == locked_at_before
    assert tag.physical_uid == physical_uid_before
    assert tag.notes == notes_before


# --- Replacement -----------------------------------------------------------------


def test_replacement_transitions_old_active_tag_to_replaced(db_session):
    piece = _make_piece(db_session, "piece-replace-old-status")
    old_tag = _locked_tag(db_session, piece)
    replacement = _available_tag(db_session)

    replace_nfc_tag(db_session, piece.id, replacement.id)

    assert old_tag.status == NfcTagStatus.replaced


def test_replacement_assigns_and_programs_replacement_tag(db_session):
    piece = _make_piece(db_session, "piece-replace-new-tag")
    _locked_tag(db_session, piece)
    replacement = _available_tag(db_session)

    result = replace_nfc_tag(db_session, piece.id, replacement.id)

    assert result.replacement_tag.id == replacement.id
    assert result.replacement_tag.piece_id == piece.id
    assert result.replacement_tag.status == NfcTagStatus.programmed
    assert result.replacement_tag.programmed_at is not None


def test_replacement_from_locked_preserves_old_tag_history(db_session):
    """Issue #70 (approved 2026-09-18): a locked active tag transitioning to
    replaced must preserve physical_uid, programmed_at, locked_at, and
    piece_id — only status changes.
    """
    piece = _make_piece(db_session, "piece-replace-preserves-history")
    old_tag = _locked_tag(db_session, piece, physical_uid="uid-replace-history")
    physical_uid_before = old_tag.physical_uid
    programmed_at_before = old_tag.programmed_at
    locked_at_before = old_tag.locked_at
    piece_id_before = old_tag.piece_id
    replacement = _available_tag(db_session)

    replace_nfc_tag(db_session, piece.id, replacement.id)

    assert old_tag.status == NfcTagStatus.replaced
    assert old_tag.physical_uid == physical_uid_before
    assert old_tag.programmed_at == programmed_at_before
    assert old_tag.locked_at == locked_at_before
    assert old_tag.piece_id == piece_id_before


def test_replacement_leaves_exactly_one_active_tag(db_session):
    piece = _make_piece(db_session, "piece-replace-one-active")
    _locked_tag(db_session, piece)
    replacement = _available_tag(db_session)

    replace_nfc_tag(db_session, piece.id, replacement.id)

    active_tags = db_session.execute(
        select(NfcTag).where(
            NfcTag.piece_id == piece.id,
            NfcTag.status.in_([NfcTagStatus.programmed, NfcTagStatus.locked]),
        )
    ).scalars().all()
    assert len(active_tags) == 1
    assert active_tags[0].id == replacement.id


def test_replacement_without_active_tag_rejected(db_session):
    piece = _make_piece(db_session, "piece-replace-none-active")
    replacement = _available_tag(db_session)

    with pytest.raises(ActiveNfcTagNotFound):
        replace_nfc_tag(db_session, piece.id, replacement.id)


def test_replacement_with_invalid_replacement_tag_rejected(db_session):
    piece = _make_piece(db_session, "piece-replace-invalid-replacement")
    _locked_tag(db_session, piece)

    other_piece = _make_piece(db_session, "piece-replace-invalid-replacement-other")
    not_available_replacement = _programmed_tag(db_session, other_piece)

    with pytest.raises(InvalidNfcTagTransition):
        replace_nfc_tag(db_session, piece.id, not_available_replacement.id)


def test_replacement_with_already_assigned_replacement_tag_rejected(db_session):
    piece = _make_piece(db_session, "piece-replace-already-assigned-a")
    _locked_tag(db_session, piece)

    other_piece = _make_piece(db_session, "piece-replace-already-assigned-b")
    already_assigned_replacement = _assigned_tag(db_session, other_piece)

    with pytest.raises(NfcTagAlreadyAssigned):
        replace_nfc_tag(db_session, piece.id, already_assigned_replacement.id)


def test_replacement_forced_failure_rolls_back_completely(db_session):
    piece = _make_piece(db_session, "piece-replace-fail")
    old_tag = _locked_tag(db_session, piece)
    replacement = _available_tag(db_session)

    with patch("app.services.nfc_tags.program_nfc_tag", side_effect=RuntimeError("forced failure")):
        with pytest.raises(RuntimeError):
            replace_nfc_tag(db_session, piece.id, replacement.id)

    rows = db_session.execute(
        select(NfcTag).where(NfcTag.id.in_([old_tag.id, replacement.id]))
    ).scalars().all()
    by_id = {row.id: row for row in rows}

    assert by_id[old_tag.id].status == NfcTagStatus.locked
    assert by_id[replacement.id].status == NfcTagStatus.available
    assert by_id[replacement.id].piece_id is None


# --- Active tag lookup / different pieces ----------------------------------------


def test_active_tags_on_different_pieces_allowed(db_session):
    piece_a = _make_piece(db_session, "piece-nfc-active-multi-a")
    piece_b = _make_piece(db_session, "piece-nfc-active-multi-b")

    tag_a = _programmed_tag(db_session, piece_a)
    tag_b = _locked_tag(db_session, piece_b)

    active_tags = db_session.execute(
        select(NfcTag).where(NfcTag.status.in_([NfcTagStatus.programmed, NfcTagStatus.locked]))
    ).scalars().all()
    active_ids = {tag.id for tag in active_tags}
    assert tag_a.id in active_ids
    assert tag_b.id in active_ids
