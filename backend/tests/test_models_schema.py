from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.db.base import engine
from app.models import Artisan, Certificate, MediaAsset, NfcTag, Piece
from app.models.certificate import CertificateStatus
from app.models.media_asset import MediaRole, MediaType
from app.models.nfc_tag import NfcTagStatus

EXPECTED_TABLES = {"artisan", "piece", "media_asset", "certificate", "nfc_tag", "alembic_version"}
DEFERRED_TABLES = {"audit_event"}


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


# --- NfcTag (Sprint 4, issue #68) -----------------------------------------


def test_nfc_tag_status_enum_values_are_exactly_approved_set():
    assert {status.value for status in NfcTagStatus} == {
        "available",
        "programmed",
        "locked",
        "replaced",
        "retired",
    }


def test_nfc_tag_has_expected_columns_only():
    columns = set(NfcTag.__table__.columns.keys())
    assert columns == {
        "id",
        "piece_id",
        "chip_model",
        "frequency",
        "protocol",
        "physical_uid",
        "programmed_at",
        "locked_at",
        "status",
        "notes",
        "created_at",
        "updated_at",
    }


def test_nfc_tag_has_no_artisan_or_certificate_reference():
    columns = set(NfcTag.__table__.columns.keys())
    assert "artisan_id" not in columns
    assert "certificate_id" not in columns
    assert not hasattr(NfcTag, "artisan")
    assert not hasattr(NfcTag, "certificate")


def test_nfc_tag_belongs_to_optional_piece(db_session):
    piece = _make_piece(db_session, "piece-nfc-belongs")
    tag = NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.available)
    db_session.add(tag)
    db_session.flush()

    assert tag.piece_id == piece.id
    assert tag.piece.id == piece.id
    assert list(piece.nfc_tags) == [tag]


def test_nfc_tag_can_be_unassigned_from_any_piece(db_session):
    tag = NfcTag(chip_model="NTAG213", status=NfcTagStatus.available)
    db_session.add(tag)
    db_session.flush()

    assert tag.piece_id is None
    assert tag.piece is None


def test_nfc_tag_physical_uid_is_optional(db_session):
    tag = NfcTag(chip_model="NTAG213", status=NfcTagStatus.available, physical_uid=None)
    db_session.add(tag)
    db_session.flush()

    assert tag.physical_uid is None


def test_nfc_tag_physical_uid_unique_when_present(db_session):
    db_session.add(NfcTag(chip_model="NTAG213", status=NfcTagStatus.available, physical_uid="dup-uid"))
    db_session.flush()

    db_session.add(NfcTag(chip_model="NTAG213", status=NfcTagStatus.available, physical_uid="dup-uid"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_nfc_tag_multiple_null_physical_uids_allowed(db_session):
    db_session.add_all(
        [
            NfcTag(chip_model="NTAG213", status=NfcTagStatus.available, physical_uid=None),
            NfcTag(chip_model="NTAG213", status=NfcTagStatus.available, physical_uid=None),
        ]
    )
    db_session.flush()  # must not raise: NULL != NULL for a nullable-safe unique constraint


def test_nfc_tag_programmed_or_locked_requires_piece(db_session):
    db_session.add(NfcTag(chip_model="NTAG213", status=NfcTagStatus.programmed, piece_id=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_nfc_tag_locked_requires_piece(db_session):
    db_session.add(NfcTag(chip_model="NTAG213", status=NfcTagStatus.locked, piece_id=None))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_nfc_tag_available_status_permits_no_piece(db_session):
    tag = NfcTag(chip_model="NTAG213", status=NfcTagStatus.available, piece_id=None)
    db_session.add(tag)
    db_session.flush()

    assert tag.status == NfcTagStatus.available


def test_nfc_tag_locked_at_requires_locked_status(db_session):
    piece = _make_piece(db_session, "piece-nfc-locked-at-mismatch")
    db_session.add(
        NfcTag(
            piece_id=piece.id,
            chip_model="NTAG213",
            status=NfcTagStatus.programmed,
            locked_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_nfc_tag_locked_with_locked_at_succeeds(db_session):
    piece = _make_piece(db_session, "piece-nfc-locked-ok")
    tag = NfcTag(
        piece_id=piece.id,
        chip_model="NTAG213",
        status=NfcTagStatus.locked,
        locked_at=datetime.now(timezone.utc),
    )
    db_session.add(tag)
    db_session.flush()

    assert tag.status == NfcTagStatus.locked
    assert tag.locked_at is not None


def test_nfc_tag_available_with_locked_at_rejected(db_session):
    db_session.add(
        NfcTag(
            chip_model="NTAG213",
            status=NfcTagStatus.available,
            locked_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_nfc_tag_replaced_preserves_locked_at(db_session):
    """Issue #70: locked_at is historical metadata that must survive a
    locked tag moving to replaced, not just a marker of the current state.
    """
    piece = _make_piece(db_session, "piece-nfc-replaced-preserves-locked-at")
    tag = NfcTag(
        piece_id=piece.id,
        chip_model="NTAG213",
        status=NfcTagStatus.replaced,
        locked_at=datetime.now(timezone.utc),
    )
    db_session.add(tag)
    db_session.flush()

    assert tag.status == NfcTagStatus.replaced
    assert tag.locked_at is not None


def test_nfc_tag_retired_preserves_locked_at(db_session):
    """Issue #70: same as replaced above, for retirement."""
    piece = _make_piece(db_session, "piece-nfc-retired-preserves-locked-at")
    tag = NfcTag(
        piece_id=piece.id,
        chip_model="NTAG213",
        status=NfcTagStatus.retired,
        locked_at=datetime.now(timezone.utc),
    )
    db_session.add(tag)
    db_session.flush()

    assert tag.status == NfcTagStatus.retired
    assert tag.locked_at is not None


def test_nfc_tag_one_active_per_piece_enforced(db_session):
    piece = _make_piece(db_session, "piece-nfc-one-active")
    db_session.add(NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.programmed))
    db_session.flush()

    db_session.add(NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.locked))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_nfc_tag_history_allows_multiple_replaced_or_retired_plus_one_active(db_session):
    piece = _make_piece(db_session, "piece-nfc-history")
    db_session.add_all(
        [
            NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.retired),
            NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.replaced),
            NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.locked),
        ]
    )
    db_session.flush()

    rows = db_session.execute(select(NfcTag).where(NfcTag.piece_id == piece.id)).scalars().all()
    assert len(rows) == 3
    assert sum(1 for r in rows if r.status == NfcTagStatus.locked) == 1
    assert sum(1 for r in rows if r.status in (NfcTagStatus.retired, NfcTagStatus.replaced)) == 2


def test_nfc_tag_active_tags_for_different_pieces_allowed(db_session):
    piece_a = _make_piece(db_session, "piece-nfc-multi-a")
    piece_b = _make_piece(db_session, "piece-nfc-multi-b")
    db_session.add_all(
        [
            NfcTag(piece_id=piece_a.id, chip_model="NTAG213", status=NfcTagStatus.programmed),
            NfcTag(piece_id=piece_b.id, chip_model="NTAG213", status=NfcTagStatus.programmed),
        ]
    )
    db_session.flush()

    active_tags = db_session.execute(
        select(NfcTag).where(NfcTag.status.in_([NfcTagStatus.programmed, NfcTagStatus.locked]))
    ).scalars().all()
    assert {t.piece_id for t in active_tags} >= {piece_a.id, piece_b.id}


def test_nfc_tag_piece_fk_restricts_piece_delete(db_session):
    piece = _make_piece(db_session, "piece-nfc-fk-restrict")
    db_session.add(NfcTag(piece_id=piece.id, chip_model="NTAG213", status=NfcTagStatus.retired))
    db_session.flush()

    db_session.delete(piece)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_public_schemas_expose_no_physical_uid_field():
    from app.schemas.artisan import ArtisanPublic
    from app.schemas.piece import PiecePublic

    for schema in (ArtisanPublic, PiecePublic):
        assert "physical_uid" not in schema.model_fields


def test_certificate_resolve_schemas_expose_no_nfc_fields():
    from app.schemas.certificate import CertificateResolveAuthentic, CertificateResolveUnavailable

    for schema in (CertificateResolveAuthentic, CertificateResolveUnavailable):
        field_names = set(schema.model_fields.keys())
        assert not any("nfc" in name or "physical_uid" in name for name in field_names)
