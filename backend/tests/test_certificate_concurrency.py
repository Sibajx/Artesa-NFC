"""Multi-connection PostgreSQL tests for the certificate lifecycle (audit
findings F-03, F-09). Every test uses separate Sessions/connections, forces
the race deterministically through real row/index locks (see
tests/concurrency_helpers.py) and asserts the final *persisted* state, not
just the raised error."""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.models.certificate import Certificate, CertificateStatus
from app.services.certificates import (
    ActiveCertificateAlreadyExists,
    ActiveCertificateNotFound,
    CertificateLifecycleConflict,
    CertificateServiceError,
    activate_certificate,
    hash_certificate_token,
    revoke_certificate,
    rotate_certificate,
)
from app.services.lifecycle import LifecycleConflict
from tests.concurrency_helpers import assert_no_exception_chain, world  # noqa: F401


def _assert_session_usable_and_commits_new_work(session, piece_id) -> None:
    """After a lost race the loser's outer transaction must be alive: it can
    query, do new work and commit it durably."""
    assert session.execute(text("select 1")).scalar_one() == 1
    session.add(Certificate(piece_id=piece_id, status=CertificateStatus.draft))
    session.commit()


# --- A. concurrent activate ---------------------------------------------------


def test_concurrent_activate_loser_gets_already_active_and_state_is_consistent(world):
    piece_id = world.piece()
    draft_a, draft_b = world.draft_certificate(piece_id), world.draft_certificate(piece_id)

    winner_session = world.session()
    winner = activate_certificate(winner_session, winner_session.get(Certificate, draft_a))  # uncommitted

    loser = world.worker(lambda s: activate_certificate(s, s.get(Certificate, draft_b))).start()
    loser.wait_until_blocked()  # blocked on the partial unique index
    winner_session.commit()
    loser.join()

    assert isinstance(loser.exc, ActiveCertificateAlreadyExists)
    assert isinstance(loser.exc, CertificateServiceError)
    assert_no_exception_chain(loser.exc)

    rows = world.certificates(piece_id)
    assert world.active_certificate_count(piece_id) == 1
    assert rows[draft_a].status == CertificateStatus.active
    assert rows[draft_a].token_hash == hash_certificate_token(winner.raw_token)
    assert rows[draft_a].issued_at is not None
    # the loser's row was rolled back to an untouched draft
    assert rows[draft_b].status == CertificateStatus.draft
    assert rows[draft_b].token_hash is None
    assert rows[draft_b].issued_at is None

    # D. the loser's outer session survived and can still commit work
    _assert_session_usable_and_commits_new_work(loser.session, piece_id)
    assert len(world.certificates(piece_id)) == 3


def test_loser_keeps_its_own_earlier_uncommitted_work_across_the_conflict(world):
    """The SAVEPOINT isolates the failed transition: work the loser did
    before it survives the rollback and commits with the outer transaction."""
    piece_id = world.piece()
    draft_a, draft_b = world.draft_certificate(piece_id), world.draft_certificate(piece_id)
    winner_session = world.session()
    activate_certificate(winner_session, winner_session.get(Certificate, draft_a))

    def loser_work(session):
        session.add(Certificate(piece_id=piece_id, status=CertificateStatus.draft))  # earlier work
        session.flush()
        return activate_certificate(session, session.get(Certificate, draft_b))

    loser = world.worker(loser_work).start()
    loser.wait_until_blocked()
    winner_session.commit()
    loser.join()

    assert isinstance(loser.exc, ActiveCertificateAlreadyExists)
    loser.session.commit()
    rows = world.certificates(piece_id)
    assert len(rows) == 3  # two drafts + the loser's earlier draft
    assert world.active_certificate_count(piece_id) == 1


# --- B. stale revoke vs rotate ------------------------------------------------


def test_stale_revoke_blocked_behind_rotation_conflicts_and_keeps_revoked_at(world):
    piece_id = world.piece()
    cert_id, _ = world.active_certificate(piece_id)

    stale_session = world.session()
    stale_cert = stale_session.get(Certificate, cert_id)  # caller now holds 'active'
    assert stale_cert.status == CertificateStatus.active

    rotator = world.session()
    rotation = rotate_certificate(rotator, piece_id)  # holds the row lock, uncommitted

    stale = world.worker(lambda s: revoke_certificate(s, stale_cert), session=stale_session).start()
    stale.wait_until_blocked()
    rotator.commit()
    stale.join()
    revoked_at_from_rotation = rotation.revoked_certificate.revoked_at

    assert isinstance(stale.exc, CertificateLifecycleConflict)
    assert isinstance(stale.exc, LifecycleConflict)
    assert isinstance(stale.exc, CertificateServiceError)
    assert_no_exception_chain(stale.exc)

    rows = world.certificates(piece_id)
    assert world.active_certificate_count(piece_id) == 1
    assert rows[cert_id].status == CertificateStatus.revoked
    assert rows[cert_id].revoked_at == revoked_at_from_rotation  # not overwritten by the stale revoke
    assert rows[cert_id].token_hash is not None  # history preserved
    assert rows[rotation.certificate.id].status == CertificateStatus.active
    assert rows[rotation.certificate.id].token_hash == hash_certificate_token(rotation.raw_token)
    assert _session_alive(stale.session)


def test_stale_revoke_without_blocking_conflicts_after_committed_rotation(world):
    piece_id = world.piece()
    cert_id, _ = world.active_certificate(piece_id)

    stale_session = world.session()
    stale_cert = stale_session.get(Certificate, cert_id)

    rotator = world.session()
    rotation = rotate_certificate(rotator, piece_id)
    rotator.commit()
    revoked_at = world.certificates(piece_id)[cert_id].revoked_at

    with pytest.raises(CertificateLifecycleConflict) as excinfo:
        revoke_certificate(stale_session, stale_cert)
    assert_no_exception_chain(excinfo.value)

    # the caller's stale object was refreshed to the persisted state
    assert stale_cert.status == CertificateStatus.revoked
    assert world.certificates(piece_id)[cert_id].revoked_at == revoked_at
    assert world.active_certificate_count(piece_id) == 1
    assert world.certificates(piece_id)[rotation.certificate.id].status == CertificateStatus.active
    assert _session_alive(stale_session)


def _session_alive(session) -> bool:
    return session.execute(text("select 1")).scalar_one() == 1


# --- C. concurrent rotate -----------------------------------------------------


def test_concurrent_rotate_loser_gets_conflict_not_not_found(world):
    piece_id = world.piece()
    original_id, _ = world.active_certificate(piece_id)

    winner_session = world.session()
    winner = rotate_certificate(winner_session, piece_id)  # holds FOR UPDATE, uncommitted

    loser = world.worker(lambda s: rotate_certificate(s, piece_id)).start()
    loser.wait_until_blocked()  # blocked on the winner's FOR UPDATE
    winner_session.commit()
    loser.join()

    assert isinstance(loser.exc, CertificateLifecycleConflict)
    assert not isinstance(loser.exc, ActiveCertificateNotFound)
    assert_no_exception_chain(loser.exc)

    rows = world.certificates(piece_id)
    assert len(rows) == 2  # the loser created nothing
    assert world.active_certificate_count(piece_id) == 1
    assert rows[original_id].status == CertificateStatus.revoked
    assert rows[winner.certificate.id].status == CertificateStatus.active
    # the winner's freshly issued token was not rotated away by the loser
    assert rows[winner.certificate.id].token_hash == hash_certificate_token(winner.raw_token)
    assert rows[winner.certificate.id].revoked_at is None

    _assert_session_usable_and_commits_new_work(loser.session, piece_id)


def test_rotate_with_genuinely_no_active_certificate_stays_not_found(world):
    piece_id = world.piece()
    draft_id = world.draft_certificate(piece_id)

    session = world.session()
    with pytest.raises(ActiveCertificateNotFound):
        rotate_certificate(session, piece_id)

    assert world.certificates(piece_id)[draft_id].status == CertificateStatus.draft
    assert _session_alive(session)


def test_rotate_lock_timeout_is_reported_as_conflict_via_sqlstate(world):
    """SQLSTATE 55P03 (lock_not_available) -> lifecycle conflict. The timeout
    is set by the test on its own transaction only; the service configures no
    lock_timeout."""
    piece_id = world.piece()
    cert_id, _ = world.active_certificate(piece_id)

    holder = world.session()
    holder.execute(select(Certificate).where(Certificate.id == cert_id).with_for_update())

    def contender(session):
        session.execute(text("SET LOCAL lock_timeout = '300ms'"))
        return rotate_certificate(session, piece_id)

    worker = world.worker(contender).start().join()

    assert isinstance(worker.exc, CertificateLifecycleConflict)
    assert_no_exception_chain(worker.exc)
    holder.rollback()
    assert world.certificates(piece_id)[cert_id].status == CertificateStatus.active
    assert world.active_certificate_count(piece_id) == 1
    assert _session_alive(worker.session)
