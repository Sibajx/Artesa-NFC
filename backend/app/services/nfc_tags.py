from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.models.piece import Piece

# DATA_MODEL.md section 2.4 / section 4 restriction B: a tag counts as
# "active" for a piece only while it is programmed or locked. Every function
# below that needs this predicate goes through this tuple / _active_tag_for_piece
# instead of repeating a subtly different `status.in_(...)` check.
_ACTIVE_STATUSES = (NfcTagStatus.programmed, NfcTagStatus.locked)

# Approved 2026-09-18 (issue #70): retirement is reachable from any
# non-terminal state. `replaced` and `retired` are separate terminal
# historical states that do not convert into each other, and retirement has
# no idempotency (retired -> retired is rejected like every other
# already-terminal transition in this service).
_RETIREMENT_ALLOWED_SOURCES = (NfcTagStatus.available, NfcTagStatus.programmed, NfcTagStatus.locked)


class NfcTagServiceError(Exception):
    """Base class for NFC tag lifecycle service errors."""


class InvalidNfcTagTransition(NfcTagServiceError):
    """Raised when a lifecycle transition is attempted from an invalid status."""


class ActiveNfcTagAlreadyExists(NfcTagServiceError):
    """Raised when a piece already has an active (programmed/locked) NFC tag."""


class ActiveNfcTagNotFound(NfcTagServiceError):
    """Raised when an operation requires an active NFC tag that does not exist."""


class NfcTagAlreadyAssigned(NfcTagServiceError):
    """Raised when an available tag is already assigned to a piece."""


@dataclass(frozen=True)
class NfcTagReplacementResult:
    """Result of replacing a piece's active NFC tag with a new one.

    Unlike the certificate service's activation/rotation results, neither
    field here is a bearer secret (SECURITY.md section 6: `physical_uid` is
    inventory metadata, not a credential), so no field needs `repr=False`.
    """

    replacement_tag: NfcTag
    replaced_tag: NfcTag


def _active_tag_for_piece(
    db: Session,
    piece_id: uuid.UUID,
    *,
    exclude_tag_id: uuid.UUID | None = None,
    for_update: bool = False,
) -> NfcTag | None:
    """Canonical lookup for "the active NFC tag of a piece" (DATA_MODEL.md
    section 2.4: `status IN ('programmed', 'locked')`). `exclude_tag_id` lets
    callers ask "is there an active tag *other than this one*" without a
    second, subtly different predicate.
    """
    stmt = select(NfcTag).where(NfcTag.piece_id == piece_id, NfcTag.status.in_(_ACTIVE_STATUSES))
    if exclude_tag_id is not None:
        stmt = stmt.where(NfcTag.id != exclude_tag_id)
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def assign_nfc_tag(db: Session, tag: NfcTag, piece: Piece) -> NfcTag:
    """Assign an available, unassigned tag to a piece.

    Assignment only sets `piece_id`; it never touches `status` or
    `programmed_at` (issue #70 point 2 — activation is a separate, explicit
    step via `program_nfc_tag`). `piece` is a loaded `Piece` ORM object
    (mirrors `certificates.activate_certificate` taking a `Certificate`
    object directly), so "the piece must exist" is satisfied by the caller
    having already fetched/created it — no redundant existence query here.
    """
    if tag.status != NfcTagStatus.available:
        raise InvalidNfcTagTransition(
            f"Tag {tag.id} cannot be assigned from status '{tag.status.value}'; "
            "only 'available' tags can be assigned."
        )
    if tag.piece_id is not None:
        raise NfcTagAlreadyAssigned(
            f"Tag {tag.id} is already assigned to piece {tag.piece_id}; reassign is not permitted."
        )

    tag.piece_id = piece.id
    db.flush()
    return tag


def program_nfc_tag(db: Session, tag: NfcTag) -> NfcTag:
    """Transition an assigned, available tag to programmed.

    Preconditions (DATA_MODEL.md section 2.4 CHECK
    `ck_nfc_tag_assignment_requires_piece`, issue #70 point 3): the tag must
    already have a `piece_id` (set by `assign_nfc_tag`), must be in
    `available` status — the only prior state a tag can be programmed from —
    and the piece must not already have another active tag. The partial
    unique index `uq_nfc_tag_one_active_per_piece` remains the authoritative
    guard against a concurrent/racing programming for the same piece; this
    application-level check only produces a clearer error in the common,
    non-racing case (same relationship as `activate_certificate`'s check to
    the certificate partial unique index).
    """
    if tag.piece_id is None:
        raise InvalidNfcTagTransition(f"Tag {tag.id} cannot be programmed without an assigned piece.")
    if tag.status != NfcTagStatus.available:
        raise InvalidNfcTagTransition(
            f"Tag {tag.id} cannot be programmed from status '{tag.status.value}'; "
            "only 'available' tags (already assigned to a piece) can be programmed."
        )

    if _active_tag_for_piece(db, tag.piece_id, exclude_tag_id=tag.id) is not None:
        raise ActiveNfcTagAlreadyExists(f"Piece {tag.piece_id} already has an active NFC tag.")

    tag.status = NfcTagStatus.programmed
    tag.programmed_at = datetime.now(timezone.utc)
    db.flush()
    return tag


def lock_nfc_tag(db: Session, tag: NfcTag) -> NfcTag:
    """Transition a programmed tag to locked.

    Only `programmed -> locked` is permitted (issue #70 point 4): `available`,
    `replaced`, and `retired` are all rejected, and there is no invented
    idempotency — `locked -> locked` raises the normal invalid-transition
    error like every other already-terminal-adjacent case in this service.
    """
    if tag.status != NfcTagStatus.programmed:
        raise InvalidNfcTagTransition(
            f"Tag {tag.id} cannot be locked from status '{tag.status.value}'; "
            "only 'programmed' tags can be locked."
        )

    tag.status = NfcTagStatus.locked
    tag.locked_at = datetime.now(timezone.utc)
    db.flush()
    return tag


def retire_nfc_tag(db: Session, tag: NfcTag) -> NfcTag:
    """Retire a tag removed from service without being superseded through
    the replacement lifecycle (approved 2026-09-18).

    Allowed from `available`, `programmed`, or `locked`. Rejected from
    `replaced` (a distinct terminal state: the tag stopped being active
    because another tag superseded it — never converted into `retired`
    later) and from `retired` itself (no idempotency).

    Preserves every existing field untouched — `physical_uid`, `piece_id`,
    `programmed_at`, `locked_at`, `notes` — and the row itself is never
    deleted; only `status` changes.
    """
    if tag.status not in _RETIREMENT_ALLOWED_SOURCES:
        raise InvalidNfcTagTransition(
            f"Tag {tag.id} cannot be retired from status '{tag.status.value}'; "
            "only 'available', 'programmed', or 'locked' tags can be retired."
        )

    tag.status = NfcTagStatus.retired
    db.flush()
    return tag


def replace_nfc_tag(db: Session, piece_id: uuid.UUID, replacement_tag_id: uuid.UUID) -> NfcTagReplacementResult:
    """Replace a piece's active NFC tag with a fresh one, atomically.

    Flow (issue #70 point 6): locate the piece's current active tag -> mark
    it `replaced` (history preserved, `physical_uid`/`programmed_at`/
    `locked_at` untouched — only `status` changes) -> assign the
    replacement tag to the same piece -> program the replacement. Final
    state has exactly one active tag for the piece.

    Runs inside a SAVEPOINT (`Session.begin_nested`), mirroring
    `certificates.rotate_certificate`'s transaction strategy exactly: if any
    step fails, everything since the start of the replacement is rolled
    back — the old tag stays in its original state and the replacement is
    not left partially assigned/programmed — regardless of what the
    caller's outer transaction does afterwards.

    Row locking, same reasoning as `rotate_certificate`: the current active
    tag is located with `SELECT ... FOR UPDATE` so two concurrent
    replacements for the same piece can't both read the same active tag
    before either commits and both try to mark it replaced. The replacement
    tag row is *also* locked `FOR UPDATE` by its own id, because unlike
    certificate rotation (which always creates a brand-new row for the
    replacement) this replacement tag is an existing, pre-provisioned
    `available` row that a second, unrelated `replace_nfc_tag`/
    `program_nfc_tag` call could otherwise race to claim at the same time;
    locking it serializes that race too. The partial unique index remains
    the final, authoritative guard in both cases.
    """
    with db.begin_nested():
        active_tag = db.execute(
            select(NfcTag)
            .where(NfcTag.piece_id == piece_id, NfcTag.status.in_(_ACTIVE_STATUSES))
            .with_for_update()
        ).scalar_one_or_none()

        if active_tag is None:
            raise ActiveNfcTagNotFound(f"Piece {piece_id} has no active NFC tag to replace.")

        replacement_tag = db.execute(
            select(NfcTag).where(NfcTag.id == replacement_tag_id).with_for_update()
        ).scalar_one()

        piece = active_tag.piece
        active_tag.status = NfcTagStatus.replaced
        db.flush()

        assign_nfc_tag(db, replacement_tag, piece)
        program_nfc_tag(db, replacement_tag)

    return NfcTagReplacementResult(replacement_tag=replacement_tag, replaced_tag=active_tag)
