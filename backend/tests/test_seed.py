import uuid

import pytest
from sqlalchemy import inspect, select

from app.db.base import SessionLocal, engine
from app.db.seed import (
    ARTISANS,
    PIECES,
    SeedCollisionError,
    _fixture_id,
    seed,
)
from app.models import Artisan, MediaAsset, Piece

APPROVED_ARTISAN_SLUGS = [f["slug"] for f in ARTISANS]
APPROVED_PIECE_SLUGS = [f["slug"] for f in PIECES]
DEMO_MEDIA_IDS = [_fixture_id(f"media:{f['key']}:portrait") for f in ARTISANS] + [
    _fixture_id(f"media:{f['key']}:hero") for f in PIECES
]


def _run_seed() -> dict:
    with SessionLocal() as session:
        with session.begin():
            return seed(session)


@pytest.fixture(autouse=True)
def _restore_fixtures_after_test():
    """Every test in this module leaves the approved demo fixtures intact
    (seeding is idempotent by design), but a couple of tests deliberately
    plant a colliding row first. Re-seed after each test so state never
    leaks between tests regardless of what ran."""
    yield
    _run_seed()


def test_first_run_creates_expected_fixtures():
    _run_seed()
    with SessionLocal() as session:
        artisans = session.execute(
            select(Artisan).where(Artisan.slug.in_(APPROVED_ARTISAN_SLUGS))
        ).scalars().all()
        pieces = session.execute(
            select(Piece).where(Piece.slug.in_(APPROVED_PIECE_SLUGS))
        ).scalars().all()

    assert len(artisans) == 3
    assert len(pieces) == 4
    assert {a.slug for a in artisans} == set(APPROVED_ARTISAN_SLUGS)
    assert {p.slug for p in pieces} == set(APPROVED_PIECE_SLUGS)


def test_second_run_creates_no_duplicates():
    _run_seed()
    _run_seed()
    with SessionLocal() as session:
        artisans = session.execute(
            select(Artisan).where(Artisan.slug.in_(APPROVED_ARTISAN_SLUGS))
        ).scalars().all()
        pieces = session.execute(
            select(Piece).where(Piece.slug.in_(APPROVED_PIECE_SLUGS))
        ).scalars().all()
        media = session.execute(
            select(MediaAsset).where(MediaAsset.id.in_(DEMO_MEDIA_IDS))
        ).scalars().all()

    assert len(artisans) == 3
    assert len(pieces) == 4
    assert len(media) == 7


def test_deterministic_uuids_stable_across_runs():
    _run_seed()
    with SessionLocal() as session:
        ids_run1 = {
            f["slug"]: session.execute(
                select(Artisan.id).where(Artisan.slug == f["slug"])
            ).scalar_one()
            for f in ARTISANS
        }

    _run_seed()
    with SessionLocal() as session:
        ids_run2 = {
            f["slug"]: session.execute(
                select(Artisan.id).where(Artisan.slug == f["slug"])
            ).scalar_one()
            for f in ARTISANS
        }

    assert ids_run1 == ids_run2
    for f in ARTISANS:
        assert ids_run1[f["slug"]] == _fixture_id(f["key"])


def test_unrelated_record_survives_seed_unchanged():
    unrelated_id = uuid.uuid4()
    with SessionLocal() as session:
        with session.begin():
            session.add(
                Artisan(
                    id=unrelated_id,
                    slug="unrelated-real-artisan",
                    full_name="Unrelated Real Artisan",
                )
            )

    try:
        _run_seed()
        with SessionLocal() as session:
            unrelated = session.get(Artisan, unrelated_id)
            assert unrelated is not None
            assert unrelated.slug == "unrelated-real-artisan"
            assert unrelated.full_name == "Unrelated Real Artisan"
    finally:
        with SessionLocal() as session:
            with session.begin():
                obj = session.get(Artisan, unrelated_id)
                if obj is not None:
                    session.delete(obj)


def test_piece_slug_collision_with_different_uuid_fails_and_rolls_back():
    _run_seed()
    fixture = PIECES[0]  # mascara-demo-01
    conflicting_id = uuid.uuid4()

    with SessionLocal() as session:
        with session.begin():
            legit = session.get(Piece, _fixture_id(fixture["key"]))
            artisan_id = legit.artisan_id
            session.delete(legit)
            session.flush()  # free up the slug before inserting the impostor
            session.add(
                Piece(
                    id=conflicting_id,
                    slug=fixture["slug"],
                    public_code="IMPOSTOR-SLUG-CODE",
                    artisan_id=artisan_id,
                    name="Impostor Piece",
                )
            )

    try:
        with pytest.raises(SeedCollisionError):
            _run_seed()

        # rollback verified: the impostor row is untouched, and no other
        # fixture from this failed run was partially committed.
        with SessionLocal() as session:
            impostor = session.get(Piece, conflicting_id)
            assert impostor is not None
            assert impostor.name == "Impostor Piece"
            assert session.get(Piece, _fixture_id(fixture["key"])) is None
    finally:
        with SessionLocal() as session:
            with session.begin():
                impostor = session.get(Piece, conflicting_id)
                if impostor is not None:
                    session.delete(impostor)


def test_piece_public_code_collision_with_different_uuid_fails_and_rolls_back():
    _run_seed()
    fixture = PIECES[1]  # figura-tallada-demo-01
    conflicting_id = uuid.uuid4()

    with SessionLocal() as session:
        with session.begin():
            legit = session.get(Piece, _fixture_id(fixture["key"]))
            artisan_id = legit.artisan_id
            session.delete(legit)
            session.flush()  # free up the public_code before inserting the impostor
            session.add(
                Piece(
                    id=conflicting_id,
                    slug="impostor-slug-for-public-code-test",
                    public_code=fixture["public_code"],
                    artisan_id=artisan_id,
                    name="Impostor Piece 2",
                )
            )

    try:
        with pytest.raises(SeedCollisionError):
            _run_seed()

        with SessionLocal() as session:
            impostor = session.get(Piece, conflicting_id)
            assert impostor is not None
            assert session.get(Piece, _fixture_id(fixture["key"])) is None
    finally:
        with SessionLocal() as session:
            with session.begin():
                impostor = session.get(Piece, conflicting_id)
                if impostor is not None:
                    session.delete(impostor)


def test_three_approved_artisans_exist():
    _run_seed()
    with SessionLocal() as session:
        artisans = session.execute(
            select(Artisan).where(Artisan.slug.in_(APPROVED_ARTISAN_SLUGS))
        ).scalars().all()
    assert len(artisans) == 3


def test_four_approved_pieces_exist():
    _run_seed()
    with SessionLocal() as session:
        pieces = session.execute(
            select(Piece).where(Piece.slug.in_(APPROVED_PIECE_SLUGS))
        ).scalars().all()
    assert len(pieces) == 4


def test_seven_approved_media_assets_exist():
    _run_seed()
    with SessionLocal() as session:
        media = session.execute(
            select(MediaAsset).where(MediaAsset.id.in_(DEMO_MEDIA_IDS))
        ).scalars().all()
    assert len(media) == 7


def test_relationships_are_correct():
    _run_seed()
    with SessionLocal() as session:
        artisans_by_slug = {
            a.slug: a
            for a in session.execute(
                select(Artisan).where(Artisan.slug.in_(APPROVED_ARTISAN_SLUGS))
            ).scalars()
        }
        pieces_by_slug = {
            p.slug: p
            for p in session.execute(
                select(Piece).where(Piece.slug.in_(APPROVED_PIECE_SLUGS))
            ).scalars()
        }

        for fixture in PIECES:
            piece = pieces_by_slug[fixture["slug"]]
            assert piece.artisan_id == artisans_by_slug[fixture["artisan_key"]].id

        for fixture in ARTISANS:
            portrait = session.get(
                MediaAsset, _fixture_id(f"media:{fixture['key']}:portrait")
            )
            assert portrait is not None
            assert portrait.artisan_id == artisans_by_slug[fixture["slug"]].id
            assert portrait.piece_id is None

        for fixture in PIECES:
            hero = session.get(MediaAsset, _fixture_id(f"media:{fixture['key']}:hero"))
            assert hero is not None
            assert hero.piece_id == pieces_by_slug[fixture["slug"]].id
            assert hero.artisan_id is None


def test_all_image_media_have_alt_text():
    _run_seed()
    with SessionLocal() as session:
        media = session.execute(
            select(MediaAsset).where(MediaAsset.id.in_(DEMO_MEDIA_IDS))
        ).scalars().all()
    assert len(media) == 7
    for asset in media:
        assert asset.media_type.value == "image"
        assert asset.alt_text is not None
        assert asset.alt_text.strip() != ""


def test_no_certificate_nfc_tag_audit_event_data_or_models_introduced():
    _run_seed()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert not ({"certificate", "nfc_tag", "audit_event"} & tables)

    import app.models as models_module

    assert not hasattr(models_module, "Certificate")
    assert not hasattr(models_module, "NfcTag")
    assert not hasattr(models_module, "AuditEvent")
