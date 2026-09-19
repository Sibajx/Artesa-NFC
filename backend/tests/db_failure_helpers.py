"""Real database failures for the global DB-error boundary tests
(tests/test_global_db_error_boundary.py, tests/test_global_db_error_uvicorn.py).

Import-safe: no routes, no app import, no side effects, so both the in-process
TestClient tests and the subprocess-only Uvicorn probe app can share it. Every
helper works inside the caller's Session and never commits; the caller (or the
`get_db` teardown) rolls everything back.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Artisan, Piece
from app.models.certificate import Certificate, CertificateStatus

_SAFE_LITERAL_RE = re.compile(r"[A-Za-z0-9_]+")


def insert_duplicate_token_hash(db: Session, token_hash: str) -> None:
    """Two active certificates (on two pieces) with the same ``token_hash``;
    the flush raises ``IntegrityError`` on ``certificate_token_hash_key`` whose
    PostgreSQL DETAIL is ``Key (token_hash)=(<token_hash>) already exists.``"""
    for n in (1, 2):
        suffix = uuid.uuid4().hex[:8]
        artisan = Artisan(slug=f"dbf-a{n}-{suffix}", full_name="Artisan")
        db.add(artisan)
        db.flush()
        piece = Piece(
            slug=f"dbf-p{n}-{suffix}", public_code=f"DBF-{n}-{suffix}", artisan_id=artisan.id, name="Piece"
        )
        db.add(piece)
        db.flush()
        db.add(Certificate(piece_id=piece.id, status=CertificateStatus.active, token_hash=token_hash))
    db.flush()


def pending_rollback_after_swallowed_failure(db: Session, token_hash: str) -> None:
    """A caller swallows a failed flush and keeps using the session:
    ``PendingRollbackError`` whose message embeds the original DETAIL."""
    try:
        insert_duplicate_token_hash(db, token_hash)
    except Exception:
        pass
    db.execute(text("SELECT 1"))
    raise AssertionError("the session should have been in a pending-rollback state")  # pragma: no cover


def statement_timeout_failure(db: Session, sql_literal: str) -> None:
    """``QueryCanceled`` (SQLSTATE 57014) -> ``OperationalError`` whose message
    embeds the SQL text, including ``sql_literal``."""
    assert _SAFE_LITERAL_RE.fullmatch(sql_literal)
    db.execute(text("SET LOCAL statement_timeout = 50"))
    db.execute(text(f"SELECT pg_sleep(2), '{sql_literal}' AS c"))


def undefined_column_failure(db: Session, column: str) -> None:
    """``UndefinedColumn`` (42703) -> ``ProgrammingError`` with statement text."""
    assert _SAFE_LITERAL_RE.fullmatch(column)
    db.execute(text(f"SELECT {column} FROM certificate"))


def invalid_text_representation_failure(db: Session) -> None:
    """``InvalidTextRepresentation`` (22P02) -> ``DataError``."""
    db.execute(text("SELECT 'not-a-number'::int"))


def raw_driver_unique_violation(db: Session, value: str) -> None:
    """A psycopg ``UniqueViolation`` (DETAIL contains ``value``) raised on the
    raw driver connection, i.e. NOT wrapped by SQLAlchemy. The temp table is
    created in the open transaction, so the pool's rollback removes it."""
    assert _SAFE_LITERAL_RE.fullmatch(value)
    raw = db.connection().connection.driver_connection
    raw.execute("CREATE TEMP TABLE dbf_raw_unique (v text UNIQUE)")
    raw.execute(f"INSERT INTO dbf_raw_unique VALUES ('{value}')")
    raw.execute(f"INSERT INTO dbf_raw_unique VALUES ('{value}')")

