"""Stale-state behavior of the lifecycle services on a single connection.

The "other transaction" is simulated with raw SQL that the ORM does not see,
so the ORM object in the session is genuinely stale while the service runs.
The real multi-connection versions live in test_certificate_concurrency.py
and test_nfc_tag_concurrency.py; this file additionally proves the
flush-ordering guarantee: a stale lifecycle value can never be written before
the row lock and reload (SQLAlchemy's `begin_nested()` flushes the whole
session when it opens its SAVEPOINT, so that ordering is not automatic).
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.db.base import engine
from app.models import Artisan, Piece
from app.models.certificate import Certificate, CertificateStatus
from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.services.certificates import (
    CertificateLifecycleConflict,
    InvalidCertificateTransition,
    activate_certificate,
    revoke_certificate,
)
from app.services.lifecycle import LifecycleConflict
from app.services.nfc_tags import (
    InvalidNfcTagTransition,
    NfcTagLifecycleConflict,
    assign_nfc_tag,
    lock_nfc_tag,
    program_nfc_tag,
    retire_nfc_tag,
)


def _piece(db, slug: str) -> Piece:
    artisan = Artisan(slug=f"artisan-{slug}", full_name="Artisan")
    db.add(artisan)
    db.flush()
    piece = Piece(slug=slug, public_code=f"PC-{slug}", artisan_id=artisan.id, name="Piece")
    db.add(piece)
    db.flush()
    return piece


def _tag(db, piece: Piece | None = None, *, program: bool = False, lock: bool = False) -> NfcTag:
    tag = NfcTag(chip_model="NTAG213", status=NfcTagStatus.available)
    db.add(tag)
    db.flush()
    if piece is not None:
        assign_nfc_tag(db, tag, piece)
    if program:
        program_nfc_tag(db, tag)
    if lock:
        lock_nfc_tag(db, tag)
    return tag


def _cert(db, piece: Piece, *, activate: bool = False) -> Certificate:
    cert = Certificate(piece_id=piece.id, status=CertificateStatus.draft)
    db.add(cert)
    db.flush()
    if activate:
        activate_certificate(db, cert)
    return cert


def _other_transaction(db, sql: str, **params) -> None:
    """A change the session's ORM objects know nothing about."""
    db.execute(text(sql), params)


def _row(db, table: str, row_id) -> dict:
    return dict(db.execute(text(f"SELECT * FROM {table} WHERE id = :id"), {"id": row_id}).mappings().one())


class _Statements:
    """Records every SQL statement the connection executes."""

    def __init__(self, connection) -> None:
        self.sql: list[str] = []
        self._connection = connection
        self._listener = lambda conn, cursor, statement, *rest: self.sql.append(statement)

    def __enter__(self) -> "_Statements":
        event.listen(self._connection, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self._connection, "before_cursor_execute", self._listener)

    def lifecycle_updates(self, table: str, column: str = "status") -> list[int]:
        """Indexes of UPDATEs whose SET clause writes `column`."""
        found = []
        for index, statement in enumerate(self.sql):
            match = re.match(rf"\s*UPDATE {table} SET (.*?) WHERE", statement, re.S)
            if match and re.search(rf"\b{column}\b\s*=", match.group(1)):
                found.append(index)
        return found

    def first_lock(self) -> int:
        return next(i for i, statement in enumerate(self.sql) if "FOR UPDATE" in statement)


# --- stale handle -> conflict, database untouched ------------------------------


@pytest.mark.parametrize(
    ("db_status", "operation"),
    [
        ("replaced", retire_nfc_tag),  # replaced -> retired
        ("retired", program_nfc_tag),  # retired -> programmed
        ("retired", lock_nfc_tag),  # retired -> locked
    ],
)
def test_stale_nfc_transition_conflicts_and_leaves_terminal_state(db_session, db_status, operation):
    piece = _piece(db_session, f"stale-{db_status}-{operation.__name__}")
    tag = _tag(db_session, piece, program=operation is not program_nfc_tag)
    if operation is program_nfc_tag:
        assert tag.status == NfcTagStatus.available
    before = _row(db_session, "nfc_tag", tag.id)

    # another transaction moves the tag to a terminal state; `tag` is now stale
    _other_transaction(
        db_session,
        f"UPDATE nfc_tag SET status = '{db_status}' WHERE id = :id",
        id=tag.id,
    )

    with pytest.raises(NfcTagLifecycleConflict) as excinfo:
        operation(db_session, tag)
    assert isinstance(excinfo.value, LifecycleConflict)

    after = _row(db_session, "nfc_tag", tag.id)
    assert after["status"] == db_status
    for column in ("piece_id", "programmed_at", "locked_at", "physical_uid"):
        assert after[column] == before[column]
    assert tag.status.value == db_status  # the caller's object was refreshed


def test_stale_assign_conflicts_and_does_not_move_a_programmed_tag(db_session):
    piece_1, piece_2 = _piece(db_session, "stale-assign-1"), _piece(db_session, "stale-assign-2")
    tag = _tag(db_session)  # believes: available, unassigned
    _other_transaction(
        db_session,
        "UPDATE nfc_tag SET piece_id = :p, status = 'programmed', programmed_at = now() WHERE id = :id",
        p=piece_1.id,
        id=tag.id,
    )

    with pytest.raises(NfcTagLifecycleConflict):
        assign_nfc_tag(db_session, tag, piece_2)

    row = _row(db_session, "nfc_tag", tag.id)
    assert row["piece_id"] == piece_1.id and row["status"] == "programmed"


def test_stale_certificate_revoke_conflicts_and_keeps_revoked_at(db_session):
    piece = _piece(db_session, "stale-cert-revoke")
    cert = _cert(db_session, piece, activate=True)
    _other_transaction(
        db_session,
        "UPDATE certificate SET status = 'revoked', revoked_at = now() - interval '1 hour' WHERE id = :id",
        id=cert.id,
    )
    before = _row(db_session, "certificate", cert.id)

    with pytest.raises(CertificateLifecycleConflict):
        revoke_certificate(db_session, cert)

    assert _row(db_session, "certificate", cert.id)["revoked_at"] == before["revoked_at"]


def test_stale_draft_activation_over_active_row_conflicts_not_misleading_already_active(db_session):
    piece = _piece(db_session, "stale-cert-activate-active")
    cert = _cert(db_session, piece)  # believes: draft
    _other_transaction(
        db_session,
        "UPDATE certificate SET status = 'active', token_hash = 'issued-elsewhere', issued_at = now() WHERE id = :id",
        id=cert.id,
    )

    with pytest.raises(CertificateLifecycleConflict):
        activate_certificate(db_session, cert)

    assert _row(db_session, "certificate", cert.id)["token_hash"] == "issued-elsewhere"


def test_stale_draft_activation_cannot_resurrect_a_revoked_certificate(db_session):
    piece = _piece(db_session, "stale-cert-resurrect")
    cert = _cert(db_session, piece)
    _other_transaction(
        db_session,
        "UPDATE certificate SET status = 'revoked', token_hash = 'old-hash', issued_at = now(), "
        "revoked_at = now() WHERE id = :id",
        id=cert.id,
    )

    with pytest.raises(CertificateLifecycleConflict):
        activate_certificate(db_session, cert)

    row = _row(db_session, "certificate", cert.id)
    assert row["status"] == "revoked" and row["token_hash"] == "old-hash"


def test_row_deleted_underneath_the_caller_is_a_conflict(db_session):
    piece = _piece(db_session, "stale-deleted")
    tag = _tag(db_session, piece, program=True)
    _other_transaction(db_session, "DELETE FROM nfc_tag WHERE id = :id", id=tag.id)

    with pytest.raises(NfcTagLifecycleConflict, match="no longer exists"):
        retire_nfc_tag(db_session, tag)


def test_current_handle_with_wrong_state_is_a_plain_invalid_transition(db_session):
    piece = _piece(db_session, "current-invalid")
    tag = _tag(db_session, piece, program=True)
    retire_nfc_tag(db_session, tag)
    with pytest.raises(InvalidNfcTagTransition) as nfc_excinfo:
        lock_nfc_tag(db_session, tag)
    assert not isinstance(nfc_excinfo.value, LifecycleConflict)

    cert = _cert(db_session, piece, activate=True)
    revoke_certificate(db_session, cert)
    with pytest.raises(InvalidCertificateTransition) as cert_excinfo:
        revoke_certificate(db_session, cert)
    assert not isinstance(cert_excinfo.value, LifecycleConflict)


# --- flush ordering: a stale lifecycle value is never persisted ---------------


def test_dirty_stale_lifecycle_value_is_discarded_and_never_flushed(db_session, db_connection):
    """The caller (wrongly) edited a lifecycle field on a stale object AND
    made a legitimate edit to another field. begin_nested() would flush both
    when opening its SAVEPOINT; the service must have discarded the stale
    lifecycle edit first, so only the legitimate one is ever written."""
    piece = _piece(db_session, "flush-order-conflict")
    tag = _tag(db_session, piece)  # available, assigned
    _other_transaction(db_session, "UPDATE nfc_tag SET status = 'retired' WHERE id = :id", id=tag.id)

    tag.status = NfcTagStatus.programmed  # stale lifecycle edit (must never reach the DB)
    tag.notes = "legitimate caller edit"

    with _Statements(db_connection) as statements:
        with pytest.raises(NfcTagLifecycleConflict):
            program_nfc_tag(db_session, tag)

    assert statements.lifecycle_updates("nfc_tag") == []  # no UPDATE ever wrote `status`
    row = _row(db_session, "nfc_tag", tag.id)
    assert row["status"] == "retired"  # newer persisted state intact
    assert row["programmed_at"] is None
    assert row["notes"] == "legitimate caller edit"  # legitimate edit still persisted


def test_lock_is_taken_before_any_lifecycle_write_on_success(db_session, db_connection):
    piece = _piece(db_session, "flush-order-success")
    tag = _tag(db_session, piece, program=True)
    tag.notes = "edit made before the transition"

    with _Statements(db_connection) as statements:
        retire_nfc_tag(db_session, tag)

    lifecycle_writes = statements.lifecycle_updates("nfc_tag")
    assert lifecycle_writes, "expected the retirement UPDATE"
    assert statements.first_lock() < min(lifecycle_writes)
    row = _row(db_session, "nfc_tag", tag.id)
    assert row["status"] == "retired" and row["notes"] == "edit made before the transition"


def test_legitimate_metadata_edit_is_preserved_by_activation(db_session, db_connection):
    piece = _piece(db_session, "preserve-metadata")
    cert = _cert(db_session, piece)
    cert.authenticity_metadata = {"notes": "hand finished"}  # unflushed, non-lifecycle

    with _Statements(db_connection) as statements:
        result = activate_certificate(db_session, cert)

    assert statements.first_lock() < min(statements.lifecycle_updates("certificate"))
    row = _row(db_session, "certificate", cert.id)
    assert row["status"] == "active" and row["authenticity_metadata"] == {"notes": "hand finished"}
    assert result.certificate is cert


def test_legitimate_metadata_edit_survives_a_stale_conflict(db_session):
    piece = _piece(db_session, "preserve-metadata-conflict")
    cert = _cert(db_session, piece, activate=True)
    _other_transaction(
        db_session,
        "UPDATE certificate SET status = 'revoked', revoked_at = now() WHERE id = :id",
        id=cert.id,
    )
    cert.authenticity_metadata = {"notes": "kept"}

    with pytest.raises(CertificateLifecycleConflict):
        revoke_certificate(db_session, cert)

    row = _row(db_session, "certificate", cert.id)
    assert row["status"] == "revoked"
    assert row["authenticity_metadata"] == {"notes": "kept"}


# --- object contract -----------------------------------------------------------


def test_pending_certificate_is_activated_without_a_prior_flush(db_session):
    piece = _piece(db_session, "pending-activate")
    cert = Certificate(piece_id=piece.id, status=CertificateStatus.draft)
    db_session.add(cert)  # deliberately not flushed

    result = activate_certificate(db_session, cert)

    row = _row(db_session, "certificate", cert.id)
    assert row["status"] == "active" and row["token_hash"] is not None
    assert result.raw_token


def test_object_from_another_session_is_rejected(db_session):
    piece = _piece(db_session, "foreign-session")
    tag = _tag(db_session, piece, program=True)
    with Session(bind=engine) as other:
        with pytest.raises(ValueError, match="attached to the Session"):
            retire_nfc_tag(other, tag)
    assert _row(db_session, "nfc_tag", tag.id)["status"] == "programmed"
