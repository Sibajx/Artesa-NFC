import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.db.base import engine
from app.models import Artisan, MediaAsset, Piece
from app.models.media_asset import MediaRole, MediaType

EXPECTED_TABLES = {"artisan", "piece", "media_asset", "alembic_version"}
DEFERRED_TABLES = {"certificate", "nfc_tag", "audit_event"}


def test_migration_head_creates_only_expected_tables():
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert EXPECTED_TABLES <= tables
    assert not (DEFERRED_TABLES & tables)


def test_artisan_slug_is_unique(db_session):
    db_session.add(Artisan(slug="dup-slug", full_name="Artisan A"))
    db_session.flush()

    db_session.add(Artisan(slug="dup-slug", full_name="Artisan B"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_piece_public_code_is_unique(db_session):
    artisan = Artisan(slug="artisan-unique-code", full_name="Artisan")
    db_session.add(artisan)
    db_session.flush()

    db_session.add(Piece(slug="piece-a", public_code="DUP-CODE", artisan_id=artisan.id, name="Piece A"))
    db_session.flush()

    db_session.add(Piece(slug="piece-b", public_code="DUP-CODE", artisan_id=artisan.id, name="Piece B"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_piece_requires_artisan_and_restricts_artisan_delete(db_session):
    artisan = Artisan(slug="artisan-restrict", full_name="Artisan Restrict")
    db_session.add(artisan)
    db_session.flush()

    db_session.add(Piece(slug="piece-restrict", public_code="PC-RESTRICT", artisan_id=artisan.id, name="Piece"))
    db_session.flush()

    db_session.delete(artisan)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_media_asset_cannot_have_both_owners(db_session):
    artisan = Artisan(slug="artisan-dual-owner", full_name="Artisan")
    db_session.add(artisan)
    db_session.flush()
    piece = Piece(slug="piece-dual-owner", public_code="PC-DUAL", artisan_id=artisan.id, name="Piece")
    db_session.add(piece)
    db_session.flush()

    db_session.add(
        MediaAsset(
            artisan_id=artisan.id,
            piece_id=piece.id,
            media_type=MediaType.image,
            role=MediaRole.gallery,
            storage_path="s3://bucket/key.jpg",
            alt_text="alt",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_media_asset_requires_alt_text_for_image(db_session):
    db_session.add(
        MediaAsset(
            media_type=MediaType.image,
            role=MediaRole.gallery,
            storage_path="s3://bucket/key.jpg",
            alt_text=None,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_media_asset_piece_id_set_null_when_piece_deleted(db_session):
    artisan = Artisan(slug="artisan-set-null", full_name="Artisan")
    db_session.add(artisan)
    db_session.flush()
    piece = Piece(slug="piece-set-null", public_code="PC-SETNULL", artisan_id=artisan.id, name="Piece")
    db_session.add(piece)
    db_session.flush()

    media = MediaAsset(
        piece_id=piece.id,
        media_type=MediaType.video,
        role=MediaRole.gallery,
        storage_path="s3://bucket/video.mp4",
    )
    db_session.add(media)
    db_session.flush()

    db_session.delete(piece)
    db_session.flush()
    db_session.refresh(media)

    assert media.piece_id is None
