"""Revision c4d1a7e2f9b3 (#134): the legacy tables are dropped only when they
exist and are all empty. Exercised on the test database inside a transaction
that is always rolled back."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from app.db.base import engine

_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "c4d1a7e2f9b3_drop_empty_legacy_tables_issue_134.py"
_spec = importlib.util.spec_from_file_location("migration_c4d1a7e2f9b3", _PATH)
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


def _drop(conn):
    conn.execute(text(migration.DROP_SQL))


def _legacy_present(conn):
    return sorted(set(inspect(conn).get_table_names()) & set(migration.LEGACY_TABLES))


def _create_legacy(conn, names):
    for name in names:
        conn.execute(text(f'CREATE TABLE "{name}" (id integer PRIMARY KEY)'))


def test_head_schema_has_no_legacy_tables_and_the_drop_is_a_no_op(conn):
    _drop(conn)
    assert _legacy_present(conn) == []


def test_empty_legacy_tables_are_dropped_and_current_tables_kept(conn):
    _create_legacy(conn, migration.LEGACY_TABLES)
    # A foreign key between two legacy tables must not block the single DROP.
    conn.execute(text('ALTER TABLE "prendas" ADD COLUMN artesana_id integer REFERENCES "artesanas" (id)'))

    _drop(conn)

    assert _legacy_present(conn) == []
    assert {"artisan", "piece", "certificate", "alembic_version"} <= set(inspect(conn).get_table_names())


def test_only_the_tables_that_exist_are_dropped(conn):
    _create_legacy(conn, ["usuarios", "tags_nfc"])

    _drop(conn)

    assert _legacy_present(conn) == []


def test_a_legacy_table_with_rows_stops_the_migration_without_dropping_anything(conn):
    _create_legacy(conn, migration.LEGACY_TABLES)
    conn.execute(text('INSERT INTO "transacciones" (id) VALUES (1)'))

    savepoint = conn.begin_nested()
    with pytest.raises(DBAPIError, match="not empty.*transacciones"):
        _drop(conn)
    savepoint.rollback()

    assert _legacy_present(conn) == sorted(migration.LEGACY_TABLES)
