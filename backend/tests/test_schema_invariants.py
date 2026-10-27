"""PEND-024: the invariants the application already enforces are also in the
database (revision e7c1f3a9d5b2)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Artisan, Piece
from app.models.certificate import Certificate, CertificateStatus

_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "e7c1f3a9d5b2_schema_invariants_pend_024.py"
_spec = importlib.util.spec_from_file_location("migration_e7c1f3a9d5b2", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


@pytest.mark.parametrize("table,name,_expr", migration.CHECKS)
def test_every_constraint_exists_and_is_validated(db_session, table, name, _expr):
    row = db_session.execute(text(
        "SELECT convalidated FROM pg_constraint WHERE conname = :n AND conrelid = CAST(:t AS regclass)"),
        {"n": name, "t": table}).one_or_none()
    assert row is not None and row.convalidated is True


def test_owner_verification_foreign_key_is_indexed(db_session):
    assert db_session.execute(text(
        "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_owner_verification_claim_id'")).scalar() == 1


def _artisan(db_session, slug="inv-artisan"):
    artisan = Artisan(slug=slug, full_name="Artisan")
    db_session.add(artisan)
    db_session.flush()
    return artisan


@pytest.mark.parametrize("year", [0, -5, 10000])
def test_a_piece_year_outside_1_to_9999_is_refused(db_session, year):
    artisan = _artisan(db_session)
    db_session.add(Piece(slug="inv-piece", public_code="PC-INV", artisan_id=artisan.id, name="P", creation_year=year))
    with pytest.raises(IntegrityError, match="ck_piece_creation_year"):
        db_session.flush()


def test_blank_slugs_are_refused(db_session):
    db_session.add(Artisan(slug="   ", full_name="Artisan"))
    with pytest.raises(IntegrityError, match="ck_artisan_slug_not_blank"):
        db_session.flush()


def test_a_certificate_version_below_one_is_refused(db_session):
    artisan = _artisan(db_session, "inv-artisan-2")
    piece = Piece(slug="inv-piece-2", public_code="PC-INV2", artisan_id=artisan.id, name="P")
    db_session.add(piece)
    db_session.flush()
    db_session.add(Certificate(piece_id=piece.id, status=CertificateStatus.draft, version=0))
    with pytest.raises(IntegrityError, match="ck_certificate_version"):
        db_session.flush()
