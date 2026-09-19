from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.models.piece import Piece
from app.services.lifecycle import (
    LifecycleConflict,
    LifecycleError,
    LifecycleIntegrityError,
    lock_and_reload,
    run_in_savepoint,
    snapshot_and_expire,
)

T = TypeVar("T")

# Constraint identity the service maps deliberately (DATA_MODEL.md section 6).
# Any other constraint failure becomes NfcTagLifecycleIntegrityError.
_UQ_ONE_ACTIVE_PER_PIECE = "uq_nfc_tag_one_active_per_piece"

# The columns a transition decides on and rewrites. Reloaded under lock before
# every transition (services/lifecycle.py). physical_uid/notes/chip data are
# inventory metadata, never touched by a transition.
_LIFECYCLE_FIELDS = ("status", "piece_id", "programmed_at", "locked_at")

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


class NfcTagServiceError(LifecycleError):
    """Base class for NFC tag lifecycle service errors."""


class InvalidNfcTagTransition(NfcTagServiceError):
    """Raised when a lifecycle transition is attempted from an invalid status."""


class ActiveNfcTagAlreadyExists(NfcTagServiceError):
    """Raised when a piece already has an active (programmed/locked) NFC tag."""


class ActiveNfcTagNotFound(NfcTagServiceError):
    """Raised when an operation requires an active NFC tag that does not exist."""


class NfcTagAlreadyAssigned(NfcTagServiceError):
    """Raised when an available tag is already assigned to a piece."""


class NfcTagNotFound(NfcTagServiceError):
    """Raised when a tag referenced by id (e.g. the replacement) does not exist."""


class NfcTagLifecycleConflict(NfcTagServiceError, LifecycleConflict):
    """A concurrent transaction changed this tag's / piece's NFC lifecycle, or
    the caller's tag object was stale. Reload and decide again."""


class NfcTagLifecycleIntegrityError(NfcTagServiceError, LifecycleIntegrityError):
    """An unexpected constraint failure. Never carries database detail."""


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
    second, subtly different predicate. `for_update` locks the row and
    overwrites any stale copy already in the session's identity map with the
    persisted state.
    """
    stmt = select(NfcTag).where(NfcTag.piece_id == piece_id, NfcTag.status.in_(_ACTIVE_STATUSES))
    if exclude_tag_id is not None:
        stmt = stmt.where(NfcTag.id != exclude_tag_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return db.execute(stmt).scalar_one_or_none()


def _run(
    db: Session,
    work: Callable[[], T],
    *,
    known: Mapping[str, Callable[[], LifecycleError]] | None = None,
) -> T:
    return run_in_savepoint(
        db,
        work,
        conflict=NfcTagLifecycleConflict,
        integrity=NfcTagLifecycleIntegrityError,
        known=known,
    )


def _lock(db: Session, tag: NfcTag, believed: dict) -> bool:
    return lock_and_reload(db, tag, NfcTag, _LIFECYCLE_FIELDS, believed, conflict=NfcTagLifecycleConflict)


def _reject_state(stale: bool, tag: NfcTag, invalid: NfcTagServiceError) -> None:
    """Raise `invalid` (the transition is wrong for the state the caller knew)
    or, when the caller's copy was behind the database, a stale-state
    conflict. Messages carry ids and statuses only."""
    if stale:
        raise NfcTagLifecycleConflict(
            f"Tag {tag.id} changed state concurrently (now '{tag.status.value}'"
            f"{', piece ' + str(tag.piece_id) if tag.piece_id else ''}); reload and retry."
        )
    raise invalid


def assign_nfc_tag(db: Session, tag: NfcTag, piece: Piece) -> NfcTag:
    """Assign an available, unassigned tag to a piece.

    Assignment only sets `piece_id`; it never touches `status` or
    `programmed_at` (issue #70 point 2 — activation is a separate, explicit
    step via `program_nfc_tag`). `piece` is a loaded `Piece` ORM object
    (mirrors `certificates.activate_certificate` taking a `Certificate`
    object directly), so "the piece must exist" is satisfied by the caller
    having already fetched/created it — no redundant existence query here.

    Decided on the tag's locked, reloaded persisted state, so two racing
    assignments of one tag can no longer both succeed (last-writer-wins) and
    a stale handle can no longer move an already assigned/programmed tag to
    another piece: the loser gets `NfcTagLifecycleConflict`.
    """
    believed = snapshot_and_expire(db, tag, _LIFECYCLE_FIELDS)
    piece_id = piece.id

    def work() -> NfcTag:
        stale = _lock(db, tag, believed)
        if tag.status != NfcTagStatus.available:
            _reject_state(
                stale,
                tag,
                InvalidNfcTagTransition(
                    f"Tag {tag.id} cannot be assigned from status '{tag.status.value}'; "
                    "only 'available' tags can be assigned."
                ),
            )
        if tag.piece_id is not None:
            _reject_state(
                stale,
                tag,
                NfcTagAlreadyAssigned(
                    f"Tag {tag.id} is already assigned to piece {tag.piece_id}; reassign is not permitted."
                ),
            )

        tag.piece_id = piece_id
        db.flush()
        return tag

    return _run(db, work)


def program_nfc_tag(db: Session, tag: NfcTag) -> NfcTag:
    """Transition an assigned, available tag to programmed.

    Preconditions (DATA_MODEL.md section 2.4 CHECK
    `ck_nfc_tag_assignment_requires_piece`, issue #70 point 3): the tag must
    already have a `piece_id` (set by `assign_nfc_tag`), must be in
    `available` status — the only prior state a tag can be programmed from —
    and the piece must not already have another active tag. All of it is
    decided on the tag's locked, reloaded persisted state, so a stale handle
    can no longer resurrect a `retired`/`replaced` tag.

    The partial unique index `uq_nfc_tag_one_active_per_piece` remains the
    authoritative guard for two *different* tags of one piece racing: the
    loser's constraint failure is rolled back with its SAVEPOINT and raised as
    `ActiveNfcTagAlreadyExists` (what a serial run after the winner would have
    produced).
    """
    piece_id = tag.piece_id  # read before the lifecycle columns are expired
    believed = snapshot_and_expire(db, tag, _LIFECYCLE_FIELDS)

    def work() -> NfcTag:
        stale = _lock(db, tag, believed)
        if tag.piece_id is None:
            _reject_state(
                stale, tag, InvalidNfcTagTransition(f"Tag {tag.id} cannot be programmed without an assigned piece.")
            )
        if tag.status != NfcTagStatus.available:
            _reject_state(
                stale,
                tag,
                InvalidNfcTagTransition(
                    f"Tag {tag.id} cannot be programmed from status '{tag.status.value}'; "
                    "only 'available' tags (already assigned to a piece) can be programmed."
                ),
            )

        if _active_tag_for_piece(db, tag.piece_id, exclude_tag_id=tag.id) is not None:
            raise ActiveNfcTagAlreadyExists(f"Piece {tag.piece_id} already has an active NFC tag.")

        tag.status = NfcTagStatus.programmed
        tag.programmed_at = datetime.now(timezone.utc)
        db.flush()
        return tag

    return _run(
        db,
        work,
        known={
            _UQ_ONE_ACTIVE_PER_PIECE: lambda: ActiveNfcTagAlreadyExists(
                f"Piece {piece_id} already has an active NFC tag."
            )
        },
    )


def lock_nfc_tag(db: Session, tag: NfcTag) -> NfcTag:
    """Transition a programmed tag to locked.

    Only `programmed -> locked` is permitted (issue #70 point 4): `available`,
    `replaced`, and `retired` are all rejected, and there is no invented
    idempotency — `locked -> locked` raises the normal invalid-transition
    error like every other already-terminal-adjacent case in this service.
    Decided on the locked, reloaded persisted state, so a stale handle can
    no longer turn a `retired`/`replaced` tag into `locked`.
    """
    believed = snapshot_and_expire(db, tag, _LIFECYCLE_FIELDS)

    def work() -> NfcTag:
        stale = _lock(db, tag, believed)
        if tag.status != NfcTagStatus.programmed:
            _reject_state(
                stale,
                tag,
                InvalidNfcTagTransition(
                    f"Tag {tag.id} cannot be locked from status '{tag.status.value}'; "
                    "only 'programmed' tags can be locked."
                ),
            )

        tag.status = NfcTagStatus.locked
        tag.locked_at = datetime.now(timezone.utc)
        db.flush()
        return tag

    return _run(db, work)


def retire_nfc_tag(db: Session, tag: NfcTag) -> NfcTag:
    """Retire a tag removed from service without being superseded through
    the replacement lifecycle (approved 2026-09-18).

    Allowed from `available`, `programmed`, or `locked`. Rejected from
    `replaced` (a distinct terminal state: the tag stopped being active
    because another tag superseded it — never converted into `retired`
    later) and from `retired` itself (no idempotency). Decided on the locked,
    reloaded persisted state, so a stale handle can no longer convert
    `replaced` into `retired`.

    Preserves every existing field untouched — `physical_uid`, `piece_id`,
    `programmed_at`, `locked_at`, `notes` — and the row itself is never
    deleted; only `status` changes.
    """
    believed = snapshot_and_expire(db, tag, _LIFECYCLE_FIELDS)

    def work() -> NfcTag:
        stale = _lock(db, tag, believed)
        if tag.status not in _RETIREMENT_ALLOWED_SOURCES:
            _reject_state(
                stale,
                tag,
                InvalidNfcTagTransition(
                    f"Tag {tag.id} cannot be retired from status '{tag.status.value}'; "
                    "only 'available', 'programmed', or 'locked' tags can be retired."
                ),
            )

        tag.status = NfcTagStatus.retired
        db.flush()
        return tag

    return _run(db, work)


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

    Row locking: the current active tag is located with `SELECT ... FOR
    UPDATE`, then the replacement tag by id (always in that order), so two
    concurrent replacements for the same piece are serialized and an
    existing, pre-provisioned `available` replacement can't be claimed twice.
    Under READ COMMITTED the waiter's locked lookup returns nothing once the
    winner commits (the row no longer matches, and the winner's new active
    tag is not in that statement's snapshot); it therefore repeats the lookup
    with a fresh snapshot. Outcomes:

    * an active tag now exists -> `NfcTagLifecycleConflict` (a competitor
      changed the lifecycle; never a misleading not-found);
    * none -> `ActiveNfcTagNotFound` (the genuine case);
    * the replacement id matches no tag -> `NfcTagNotFound`.

    The partial unique index remains the final guard; a deadlock or lock
    timeout (SQLSTATE 40P01/55P03) is reported as `NfcTagLifecycleConflict`.
    """

    def work() -> NfcTagReplacementResult:
        active_tag = _active_tag_for_piece(db, piece_id, for_update=True)
        if active_tag is None:
            if _active_tag_for_piece(db, piece_id) is not None:
                raise NfcTagLifecycleConflict(
                    f"Piece {piece_id} changed NFC tag state concurrently; reload and retry."
                )
            raise ActiveNfcTagNotFound(f"Piece {piece_id} has no active NFC tag to replace.")

        replacement_tag = db.execute(
            select(NfcTag)
            .where(NfcTag.id == replacement_tag_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if replacement_tag is None:
            raise NfcTagNotFound(f"NFC tag {replacement_tag_id} does not exist.")

        piece = active_tag.piece
        active_tag.status = NfcTagStatus.replaced
        db.flush()

        assign_nfc_tag(db, replacement_tag, piece)
        program_nfc_tag(db, replacement_tag)

        return NfcTagReplacementResult(replacement_tag=replacement_tag, replaced_tag=active_tag)

    return _run(db, work)
