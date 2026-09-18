from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.db.base import engine
from app.models import Artisan, Certificate, MediaAsset, Piece
from app.models.certificate import CertificateStatus
from app.models.media_asset import MediaRole, MediaType

EXPECTED_TABLES = {"artisan", "piece", "media_asset", "certificate", "alembic_version"}
DEFERRED_TABLES = {"nfc_tag", "audit_event"}


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


# --- Certificate (Sprint 4, issue #62) ------------------------------------


def _make_piece(db_session, slug: str) -> Piece:
    artisan = Artisan(slug=f"artisan-for-{slug}", full_name="Artisan")
    db_session.add(artisan)
    db_session.flush()
    piece = Piece(slug=slug, public_code=f"PC-{slug}", artisan_id=artisan.id, name="Piece")
    db_session.add(piece)
    db_session.flush()
    return piece


def test_certificate_status_enum_values_are_exactly_draft_active_revoked():
    assert {status.value for status in CertificateStatus} == {"draft", "active", "revoked"}


def test_certificate_has_no_raw_token_column():
    columns = set(Certificate.__table__.columns.keys())
    assert "token_hash" in columns
    assert "token" not in columns
    assert not hasattr(Certificate, "token")


def test_certificate_belongs_to_one_piece(db_session):
    piece = _make_piece(db_session, "piece-cert-belongs")
    cert = Certificate(piece_id=piece.id, status=CertificateStatus.draft)
    db_session.add(cert)
    db_session.flush()

    assert cert.piece_id == piece.id
    assert cert.piece.id == piece.id
    assert list(piece.certificates) == [cert]


def test_certificate_draft_permits_null_token_hash(db_session):
    piece = _make_piece(db_session, "piece-cert-draft-ok")
    cert = Certificate(piece_id=piece.id, status=CertificateStatus.draft, token_hash=None)
    db_session.add(cert)
    db_session.flush()

    assert cert.token_hash is None


def test_certificate_draft_rejects_non_null_token_hash(db_session):
    piece = _make_piece(db_session, "piece-cert-draft-bad")
    db_session.add(
        Certificate(piece_id=piece.id, status=CertificateStatus.draft, token_hash="draft-should-not-have-this")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_certificate_active_requires_token_hash(db_session):
    piece = _make_piece(db_session, "piece-cert-active-no-hash")
    db_session.add(Certificate(piece_id=piece.id, status=CertificateStatus.active, token_hash=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_certificate_revoked_requires_token_hash(db_session):
    piece = _make_piece(db_session, "piece-cert-revoked-no-hash")
    db_session.add(
        Certificate(
            piece_id=piece.id,
            status=CertificateStatus.revoked,
            token_hash=None,
            revoked_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_certificate_active_rejects_non_null_revoked_at(db_session):
    piece = _make_piece(db_session, "piece-cert-active-revoked-at")
    db_session.add(
        Certificate(
            piece_id=piece.id,
            status=CertificateStatus.active,
            token_hash="hash-active-with-revoked-at",
            revoked_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_certificate_revoked_requires_revoked_at(db_session):
    piece = _make_piece(db_session, "piece-cert-revoked-no-at")
    db_session.add(
        Certificate(
            piece_id=piece.id,
            status=CertificateStatus.revoked,
            token_hash="hash-revoked-missing-at",
            revoked_at=None,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_certificate_revoked_with_hash_and_revoked_at_succeeds(db_session):
    piece = _make_piece(db_session, "piece-cert-revoked-ok")
    cert = Certificate(
        piece_id=piece.id,
        status=CertificateStatus.revoked,
        token_hash="hash-revoked-ok",
        revoked_at=datetime.now(timezone.utc),
    )
    db_session.add(cert)
    db_session.flush()

    assert cert.status == CertificateStatus.revoked


def test_certificate_one_active_per_piece_enforced(db_session):
    piece = _make_piece(db_session, "piece-cert-one-active")
    db_session.add(Certificate(piece_id=piece.id, status=CertificateStatus.active, token_hash="hash-active-1"))
    db_session.flush()

    db_session.add(Certificate(piece_id=piece.id, status=CertificateStatus.active, token_hash="hash-active-2"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_certificate_history_allows_multiple_revoked_plus_one_active(db_session):
    piece = _make_piece(db_session, "piece-cert-history")
    db_session.add_all(
        [
            Certificate(
                piece_id=piece.id,
                status=CertificateStatus.revoked,
                token_hash="hash-history-1",
                revoked_at=datetime.now(timezone.utc),
            ),
            Certificate(
                piece_id=piece.id,
                status=CertificateStatus.revoked,
                token_hash="hash-history-2",
                revoked_at=datetime.now(timezone.utc),
            ),
            Certificate(piece_id=piece.id, status=CertificateStatus.active, token_hash="hash-history-3"),
        ]
    )
    db_session.flush()

    rows = db_session.execute(select(Certificate).where(Certificate.piece_id == piece.id)).scalars().all()
    assert len(rows) == 3
    assert sum(1 for r in rows if r.status == CertificateStatus.active) == 1
    assert sum(1 for r in rows if r.status == CertificateStatus.revoked) == 2


def test_certificate_active_certificates_for_different_pieces_allowed(db_session):
    piece_a = _make_piece(db_session, "piece-cert-multi-a")
    piece_b = _make_piece(db_session, "piece-cert-multi-b")
    db_session.add_all(
        [
            Certificate(piece_id=piece_a.id, status=CertificateStatus.active, token_hash="hash-multi-a"),
            Certificate(piece_id=piece_b.id, status=CertificateStatus.active, token_hash="hash-multi-b"),
        ]
    )
    db_session.flush()

    active_certs = db_session.execute(
        select(Certificate).where(Certificate.status == CertificateStatus.active)
    ).scalars().all()
    assert {c.piece_id for c in active_certs} >= {piece_a.id, piece_b.id}


def test_certificate_token_hash_unique_globally_across_pieces(db_session):
    piece_a = _make_piece(db_session, "piece-cert-dup-hash-a")
    piece_b = _make_piece(db_session, "piece-cert-dup-hash-b")
    db_session.add(Certificate(piece_id=piece_a.id, status=CertificateStatus.active, token_hash="dup-hash"))
    db_session.flush()

    db_session.add(
        Certificate(
            piece_id=piece_b.id,
            status=CertificateStatus.revoked,
            token_hash="dup-hash",
            revoked_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_certificate_piece_fk_restricts_piece_delete(db_session):
    piece = _make_piece(db_session, "piece-cert-fk-restrict")
    db_session.add(Certificate(piece_id=piece.id, status=CertificateStatus.draft))
    db_session.flush()

    db_session.delete(piece)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_public_schemas_expose_no_certificate_or_nfc_fields():
    from app.schemas.artisan import ArtisanPublic
    from app.schemas.piece import PiecePublic

    for schema in (ArtisanPublic, PiecePublic):
        field_names = set(schema.model_fields.keys())
        assert not any("certificate" in name or "nfc" in name for name in field_names)
