"""Revision b6f2d8a4c1e9: the single photo each visit already had is copied into
visit_photo (and back on downgrade). Exercised on the test database inside a
transaction that is always rolled back."""
import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.base import engine

_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "b6f2d8a4c1e9_visit_photos.py"
_spec = importlib.util.spec_from_file_location("migration_b6f2d8a4c1e9", _PATH)
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


def _visit(conn, photo: tuple[str, int, int] | None):
    vid = uuid.uuid4()
    conn.execute(text(
        "INSERT INTO visit (id, visited_on, kind, summary, consent_to_publish, photo_path, photo_width, photo_height, recorded_by) "
        "VALUES (:id, current_date, 'otro', 'Un resumen de la visita.', false, :p, :w, :h, 'sol@example.com')"),
        {"id": vid, "p": photo[0] if photo else None, "w": photo[1] if photo else None, "h": photo[2] if photo else None})
    return vid


def test_each_existing_photo_is_copied_once_and_visits_without_one_get_nothing(conn):
    with_photo = _visit(conn, ("privado/visitas/x/a.jpg", 1600, 1200))
    without = _visit(conn, None)
    conn.execute(text(migration.BACKFILL_SQL))
    rows = conn.execute(text("SELECT visit_id, path, width, height, created_by FROM visit_photo")).all()
    assert [(r.visit_id, r.path, r.width, r.height, r.created_by) for r in rows if r.visit_id in (with_photo, without)] == [
        (with_photo, "privado/visitas/x/a.jpg", 1600, 1200, "sol@example.com")]


def test_downgrade_returns_the_oldest_photo_to_the_old_columns(conn):
    vid = _visit(conn, None)
    for i, name in enumerate(("first.jpg", "second.jpg")):
        conn.execute(text("INSERT INTO visit_photo (id, visit_id, path, width, height, created_by, created_at) "
                          "VALUES (gen_random_uuid(), :v, :p, 10, 10, 'x', now() + (:i || ' seconds')::interval)"),
                     {"v": vid, "p": f"privado/visitas/x/{name}", "i": i})
    conn.execute(text(migration.RESTORE_SQL))
    assert conn.execute(text("SELECT photo_path FROM visit WHERE id = :v"), {"v": vid}).scalar_one().endswith("first.jpg")
