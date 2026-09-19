"""Multi-connection PostgreSQL tests for the NFC tag lifecycle (audit findings
F-03, F-09 and the terminal-state escapes found while reproducing them).
Separate Sessions/connections, deterministic races through real row/index
locks (tests/concurrency_helpers.py), final *persisted* state asserted."""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import event, select, text

from app.models import Piece
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.services.lifecycle import LifecycleConflict
from app.services.nfc_tags import (
    ActiveNfcTagAlreadyExists,
    ActiveNfcTagNotFound,
    InvalidNfcTagTransition,
    NfcTagLifecycleConflict,
    NfcTagNotFound,
    NfcTagServiceError,
    assign_nfc_tag,
    lock_nfc_tag,
    program_nfc_tag,
    replace_nfc_tag,
    retire_nfc_tag,
)
from tests.concurrency_helpers import assert_no_exception_chain, world  # noqa: F401


def _session_alive(session) -> bool:
    return session.execute(text("select 1")).scalar_one() == 1


# --- A. concurrent program ----------------------------------------------------


def test_concurrent_program_loser_gets_already_active_and_state_is_consistent(world):
    piece_id = world.piece()
    tag_a, tag_b = world.tag(piece_id), world.tag(piece_id)

    winner_session = world.session()
    program_nfc_tag(winner_session, winner_session.get(NfcTag, tag_a))  # uncommitted

    loser = world.worker(lambda s: program_nfc_tag(s, s.get(NfcTag, tag_b))).start()
    loser.wait_until_blocked()  # blocked on the partial unique index
    winner_session.commit()
    loser.join()

    assert isinstance(loser.exc, ActiveNfcTagAlreadyExists)
    assert isinstance(loser.exc, NfcTagServiceError)
    assert_no_exception_chain(loser.exc)

    tags = world.tags(tag_a, tag_b)
    assert world.active_tag_count(piece_id) == 1
    assert tags[tag_a].status == NfcTagStatus.programmed
    assert tags[tag_a].programmed_at is not None
    assert tags[tag_b].status == NfcTagStatus.available
    assert tags[tag_b].programmed_at is None
    assert tags[tag_b].piece_id == piece_id  # assignment untouched by the rolled-back program

    # the loser's session survived and can still finish other work
    assert _session_alive(loser.session)
    retire_nfc_tag(loser.session, loser.session.get(NfcTag, tag_b))
    loser.session.commit()
    assert world.tags(tag_b)[tag_b].status == NfcTagStatus.retired


# --- B. stale retire vs replace -----------------------------------------------


def test_stale_retire_blocked_behind_replace_conflicts_and_keeps_replaced(world):
    piece_id = world.piece()
    old_id = world.active_tag(piece_id, locked=True)
    new_id = world.tag()
    before = world.tags(old_id)[old_id]

    stale_session = world.session()
    stale_tag = stale_session.get(NfcTag, old_id)  # caller holds 'locked'
    assert stale_tag.status == NfcTagStatus.locked

    replacer = world.session()
    replace_nfc_tag(replacer, piece_id, new_id)  # holds the row locks, uncommitted

    stale = world.worker(lambda s: retire_nfc_tag(s, stale_tag), session=stale_session).start()
    stale.wait_until_blocked()
    replacer.commit()
    stale.join()

    assert isinstance(stale.exc, NfcTagLifecycleConflict)
    assert isinstance(stale.exc, LifecycleConflict)
    assert isinstance(stale.exc, NfcTagServiceError)
    assert_no_exception_chain(stale.exc)

    tags = world.tags(old_id, new_id)
    assert tags[old_id].status == NfcTagStatus.replaced  # NOT converted to retired
    assert tags[old_id].locked_at == before.locked_at and tags[old_id].locked_at is not None
    assert tags[old_id].programmed_at == before.programmed_at
    assert tags[old_id].physical_uid == before.physical_uid
    assert tags[new_id].status == NfcTagStatus.programmed
    assert world.active_tag_count(piece_id) == 1
    assert _session_alive(stale.session)


def test_stale_retire_after_committed_replace_conflicts_without_blocking(world):
    piece_id = world.piece()
    old_id = world.active_tag(piece_id)
    new_id = world.tag()

    stale_session = world.session()
    stale_tag = stale_session.get(NfcTag, old_id)
    replacer = world.session()
    replace_nfc_tag(replacer, piece_id, new_id)
    replacer.commit()

    with pytest.raises(NfcTagLifecycleConflict) as excinfo:
        retire_nfc_tag(stale_session, stale_tag)
    assert_no_exception_chain(excinfo.value)

    assert stale_tag.status == NfcTagStatus.replaced  # caller's object refreshed
    assert world.tags(old_id)[old_id].status == NfcTagStatus.replaced
    assert world.active_tag_count(piece_id) == 1
    assert _session_alive(stale_session)


# --- C. concurrent replace ----------------------------------------------------


def test_concurrent_replace_loser_gets_conflict_not_not_found(world):
    piece_id = world.piece()
    old_id = world.active_tag(piece_id, locked=True)
    repl_1, repl_2 = world.tag(), world.tag()

    winner_session = world.session()
    replace_nfc_tag(winner_session, piece_id, repl_1)  # holds FOR UPDATE, uncommitted

    loser = world.worker(lambda s: replace_nfc_tag(s, piece_id, repl_2)).start()
    loser.wait_until_blocked()
    winner_session.commit()
    loser.join()

    assert isinstance(loser.exc, NfcTagLifecycleConflict)
    assert not isinstance(loser.exc, ActiveNfcTagNotFound)
    assert_no_exception_chain(loser.exc)

    tags = world.tags(old_id, repl_1, repl_2)
    assert world.active_tag_count(piece_id) == 1
    assert tags[old_id].status == NfcTagStatus.replaced
    assert tags[old_id].locked_at is not None  # lock history preserved
    assert tags[repl_1].status == NfcTagStatus.programmed
    assert tags[repl_1].piece_id == piece_id
    # the loser's replacement was neither assigned nor programmed
    assert tags[repl_2].status == NfcTagStatus.available
    assert tags[repl_2].piece_id is None
    assert tags[repl_2].programmed_at is None

    assert _session_alive(loser.session)


def test_replace_with_genuinely_no_active_tag_stays_not_found(world):
    piece_id = world.piece()
    replacement = world.tag()

    session = world.session()
    with pytest.raises(ActiveNfcTagNotFound):
        replace_nfc_tag(session, piece_id, replacement)

    assert world.tags(replacement)[replacement].status == NfcTagStatus.available
    assert _session_alive(session)


def test_replace_with_unknown_replacement_is_a_domain_not_found(world):
    piece_id = world.piece()
    old_id = world.active_tag(piece_id)

    session = world.session()
    with pytest.raises(NfcTagNotFound) as excinfo:
        replace_nfc_tag(session, piece_id, uuid.uuid4())
    assert_no_exception_chain(excinfo.value)

    assert world.tags(old_id)[old_id].status == NfcTagStatus.programmed
    assert world.active_tag_count(piece_id) == 1
    assert _session_alive(session)


# --- D. stale lock / program / assign against changed state -------------------


def test_stale_lock_behind_retire_conflicts_and_keeps_retired(world):
    piece_id = world.piece()
    tag_id = world.active_tag(piece_id)

    stale_session = world.session()
    stale_tag = stale_session.get(NfcTag, tag_id)  # 'programmed'
    retirer = world.session()
    retire_nfc_tag(retirer, retirer.get(NfcTag, tag_id))  # uncommitted

    stale = world.worker(lambda s: lock_nfc_tag(s, stale_tag), session=stale_session).start()
    stale.wait_until_blocked()
    retirer.commit()
    stale.join()

    assert isinstance(stale.exc, NfcTagLifecycleConflict)
    assert_no_exception_chain(stale.exc)
    row = world.tags(tag_id)[tag_id]
    assert row.status == NfcTagStatus.retired  # retired -> locked prevented
    assert row.locked_at is None
    assert row.programmed_at is not None
    assert world.active_tag_count(piece_id) == 0
    assert _session_alive(stale.session)


def test_stale_program_behind_retire_conflicts_and_keeps_retired(world):
    piece_id = world.piece()
    tag_id = world.tag(piece_id)  # available + assigned

    stale_session = world.session()
    stale_tag = stale_session.get(NfcTag, tag_id)
    retirer = world.session()
    retire_nfc_tag(retirer, retirer.get(NfcTag, tag_id))

    stale = world.worker(lambda s: program_nfc_tag(s, stale_tag), session=stale_session).start()
    stale.wait_until_blocked()
    retirer.commit()
    stale.join()

    assert isinstance(stale.exc, NfcTagLifecycleConflict)
    row = world.tags(tag_id)[tag_id]
    assert row.status == NfcTagStatus.retired  # retired -> programmed prevented
    assert row.programmed_at is None
    assert world.active_tag_count(piece_id) == 0
    assert _session_alive(stale.session)


def test_stale_lock_of_replaced_tag_conflicts_and_active_tag_is_untouched(world):
    piece_id = world.piece()
    old_id = world.active_tag(piece_id)
    new_id = world.tag()

    stale_session = world.session()
    stale_tag = stale_session.get(NfcTag, old_id)
    replacer = world.session()
    replace_nfc_tag(replacer, piece_id, new_id)
    replacer.commit()

    with pytest.raises(NfcTagLifecycleConflict):
        lock_nfc_tag(stale_session, stale_tag)

    tags = world.tags(old_id, new_id)
    assert tags[old_id].status == NfcTagStatus.replaced and tags[old_id].locked_at is None
    assert tags[new_id].status == NfcTagStatus.programmed
    assert world.active_tag_count(piece_id) == 1
    assert _session_alive(stale_session)


def test_concurrent_assign_of_one_tag_has_one_winner(world):
    piece_1, piece_2 = world.piece(), world.piece()
    tag_id = world.tag()

    winner_session = world.session()
    assign_nfc_tag(winner_session, winner_session.get(NfcTag, tag_id), winner_session.get(Piece, piece_1))

    def loser_work(session):
        return assign_nfc_tag(session, session.get(NfcTag, tag_id), session.get(Piece, piece_2))

    loser = world.worker(loser_work).start()
    loser.wait_until_blocked()  # blocked on the tag's row lock
    winner_session.commit()
    loser.join()

    assert isinstance(loser.exc, NfcTagLifecycleConflict)  # no last-writer-wins
    assert_no_exception_chain(loser.exc)
    row = world.tags(tag_id)[tag_id]
    assert row.piece_id == piece_1
    assert row.status == NfcTagStatus.available
    assert _session_alive(loser.session)


def test_stale_assign_cannot_move_a_programmed_tag_to_another_piece(world):
    piece_1, piece_2 = world.piece(), world.piece()
    tag_id = world.tag()

    stale_session = world.session()
    stale_tag = stale_session.get(NfcTag, tag_id)  # believes: available, unassigned
    other = world.session()
    other_tag = other.get(NfcTag, tag_id)
    assign_nfc_tag(other, other_tag, other.get(Piece, piece_1))
    program_nfc_tag(other, other_tag)
    other.commit()

    with pytest.raises(NfcTagLifecycleConflict):
        assign_nfc_tag(stale_session, stale_tag, stale_session.get(Piece, piece_2))

    row = world.tags(tag_id)[tag_id]
    assert row.piece_id == piece_1
    assert row.status == NfcTagStatus.programmed
    assert world.active_tag_count(piece_1) == 1 and world.active_tag_count(piece_2) == 0
    assert _session_alive(stale_session)


def test_current_handle_with_wrong_state_is_still_a_plain_invalid_transition(world):
    """Stale -> conflict; a caller that already knew the state gets the
    ordinary invalid-transition error (not every rejection is a race)."""
    piece_id = world.piece()
    tag_id = world.active_tag(piece_id)
    retirer = world.session()
    retire_nfc_tag(retirer, retirer.get(NfcTag, tag_id))
    retirer.commit()

    session = world.session()
    fresh_tag = session.get(NfcTag, tag_id)  # loaded after the change: current
    with pytest.raises(InvalidNfcTagTransition) as excinfo:
        lock_nfc_tag(session, fresh_tag)
    assert not isinstance(excinfo.value, LifecycleConflict)
    assert world.tags(tag_id)[tag_id].status == NfcTagStatus.retired


# --- lock contention mapped by SQLSTATE ---------------------------------------


def test_replace_lock_timeout_is_reported_as_conflict_via_sqlstate(world):
    """SQLSTATE 55P03 -> conflict. The timeout is set by the test on its own
    transaction only; the service configures no lock_timeout."""
    piece_id = world.piece()
    old_id = world.active_tag(piece_id)
    new_id = world.tag()

    holder = world.session()
    holder.execute(select(NfcTag).where(NfcTag.id == old_id).with_for_update())

    def contender(session):
        session.execute(text("SET LOCAL lock_timeout = '300ms'"))
        return replace_nfc_tag(session, piece_id, new_id)

    worker = world.worker(contender).start().join()

    assert isinstance(worker.exc, NfcTagLifecycleConflict)
    assert_no_exception_chain(worker.exc)
    holder.rollback()
    tags = world.tags(old_id, new_id)
    assert tags[old_id].status == NfcTagStatus.programmed
    assert tags[new_id].status == NfcTagStatus.available and tags[new_id].piece_id is None
    assert _session_alive(worker.session)


def test_replace_deadlock_is_reported_as_conflict_via_sqlstate(world):
    """SQLSTATE 40P01 -> conflict. Two replacements lock (own active tag,
    then replacement) in opposite orders: replace(P1, T2) vs replace(P2, T1).
    A barrier holds each session after its first lock until both have it, so
    the deadlock is guaranteed; PostgreSQL aborts exactly one victim. The
    survivor then fails its own (invalid) request; nothing is modified."""
    piece_1, piece_2 = world.piece(), world.piece()
    tag_1, tag_2 = world.active_tag(piece_1), world.active_tag(piece_2)

    barrier = threading.Barrier(2, timeout=15)

    def gate(session):
        # Pause each session right after its first FOR UPDATE (the active-tag
        # lookup) and before it requests the replacement row.
        connection = session.connection()
        fired = []

        @event.listens_for(connection, "before_cursor_execute")
        def _pause(conn, cursor, statement, parameters, context, executemany):
            if "FOR UPDATE" in statement and "nfc_tag.id = " in statement and not fired:
                fired.append(True)
                barrier.wait()

        return connection

    def replace_via_gate(piece, replacement):
        def run(session):
            gate(session)
            return replace_nfc_tag(session, piece, replacement)

        return run

    first = world.worker(replace_via_gate(piece_1, tag_2)).start()
    second = world.worker(replace_via_gate(piece_2, tag_1)).start()
    first.join()
    second.join()

    outcomes = {type(first.exc), type(second.exc)}
    assert outcomes == {NfcTagLifecycleConflict, InvalidNfcTagTransition}, outcomes
    for worker in (first, second):
        assert_no_exception_chain(worker.exc)
        assert _session_alive(worker.session)

    tags = world.tags(tag_1, tag_2)
    assert tags[tag_1].status == NfcTagStatus.programmed
    assert tags[tag_2].status == NfcTagStatus.programmed
    assert world.active_tag_count(piece_1) == 1 and world.active_tag_count(piece_2) == 1
