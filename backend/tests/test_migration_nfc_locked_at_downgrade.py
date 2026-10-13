"""PEND-035: downgrading 895974720462 used to fail with a CheckViolation when a
replaced/retired tag kept its locked_at. It now clears that history first.
Runs inside a transaction that is always rolled back (PostgreSQL DDL is
transactional)."""
import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.db.base import engine

_PATH = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
         / "895974720462_widen_nfc_tag_locked_at_invariant_for_.py")
_spec = importlib.util.spec_from_file_location("migration_895974720462", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


@pytest.fixture
def conn():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _tag(conn, status: str, locked: bool) -> uuid.UUID:
    tag_id = uuid.uuid4()
    conn.execute(text(
        "INSERT INTO nfc_tag (id, chip_model, status, locked_at) "
        "VALUES (:id, 'NTAG215', CAST(:s AS nfc_tag_status), CASE WHEN :l THEN now() END)"),
        {"id": tag_id, "s": status, "l": locked})
    return tag_id


def test_downgrade_with_historical_locked_at_succeeds_and_clears_it(conn):
    retired = _tag(conn, "retired", True)
    replaced = _tag(conn, "replaced", True)
    available = _tag(conn, "available", False)
    with Operations.context(MigrationContext.configure(conn)):
        migration.downgrade()
    rows = dict(conn.execute(text(
        "SELECT id, locked_at FROM nfc_tag WHERE id IN (:a, :b, :c)"),
        {"a": retired, "b": replaced, "c": available}).all())
    assert rows == {retired: None, replaced: None, available: None}
    # the narrow constraint is back: a retired tag with locked_at is refused again
    with pytest.raises(Exception, match="ck_nfc_tag_locked_at_matches_status"):
        _tag(conn, "retired", True)
