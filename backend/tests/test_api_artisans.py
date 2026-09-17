import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_db
from app.db.base import SessionLocal
from app.db.seed import ARTISANS, seed
from app.main import app
from app.models.artisan import Artisan
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset, MediaAssetStatus, MediaRole, MediaType

client = TestClient(app)

APPROVED_ARTISAN_SLUGS_SORTED = sorted(f["slug"] for f in ARTISANS)

NOT_FOUND_BODY = {
    "error": {
        "code": "not_found",
        "message": "The requested resource does not exist.",
    }
}


def _run_seed() -> None:
    with SessionLocal() as session:
        with session.begin():
            seed(session)


@pytest.fixture(autouse=True)
def _ensure_seed():
    """The API reads from the real, committed database (not a rollback-scoped
    transaction), so the deterministic demo seed must actually be applied
    before each test, same as tests/test_seed.py does for its own suite."""
    _run_seed()
    yield


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _override_get_db(session):
    def _override():
        yield session

    return _override


# --- GET /api/v1/artisans -----------------------------------------------


def test_list_artisans_returns_200():
    response = client.get("/api/v1/artisans")
    assert response.status_code == 200


def test_list_artisans_returns_published_demo_artisans_in_deterministic_order():
    response = client.get("/api/v1/artisans")
    body = response.json()
    slugs = [a["slug"] for a in body["data"]]
    assert slugs == APPROVED_ARTISAN_SLUGS_SORTED
    assert body["meta"]["total"] == 3


def test_list_artisans_summary_has_only_documented_fields():
    response = client.get("/api/v1/artisans")
    for artisan in response.json()["data"]:
        assert set(artisan.keys()) == {"slug", "full_name", "artistic_name"}


def test_list_artisans_no_internal_fields_leak():
    response = client.get("/api/v1/artisans")
    raw = response.text
    assert "publication_status" not in raw
    assert "storage_path" not in raw
    for artisan in response.json()["data"]:
        for value in artisan.values():
            if isinstance(value, str):
                assert not _looks_like_uuid(value)


# --- GET /api/v1/artisans/{slug} -----------------------------------------


def test_get_artisan_detail_returns_200():
    response = client.get("/api/v1/artisans/artisan-demo-01")
    assert response.status_code == 200


def test_get_artisan_detail_matches_documented_shape():
    response = client.get("/api/v1/artisans/artisan-demo-01")
    body = response.json()
    assert set(body.keys()) == {
        "slug",
        "full_name",
        "artistic_name",
        "biography",
        "history",
        "location",
        "techniques",
        "languages",
        "public_contact",
        "media",
        "pieces",
    }
    assert set(body["location"].keys()) == {"locality", "municipality", "state", "country"}
    assert body["slug"] == "artisan-demo-01"
    assert body["languages"] == []


def test_get_artisan_detail_includes_correct_public_media():
    response = client.get("/api/v1/artisans/artisan-demo-01")
    body = response.json()
    assert len(body["media"]) == 1
    media = body["media"][0]
    assert set(media.keys()) == {"type", "role", "url", "alt_text", "position", "format"}
    assert media["type"] == "image"
    assert media["role"] == "portrait"
    assert media["url"] == "/media/demo/artisans/artisan-demo-01/portrait.jpg"
    assert media["format"] == {"width": None, "height": None, "mime_type": None}


def test_get_artisan_detail_includes_piece_summaries_with_cover_media():
    response = client.get("/api/v1/artisans/artisan-demo-01")
    body = response.json()
    piece_slugs = [p["slug"] for p in body["pieces"]]
    assert piece_slugs == sorted(piece_slugs)
    assert piece_slugs == ["figura-tallada-demo-01", "mascara-demo-01"]
    for piece in body["pieces"]:
        assert set(piece.keys()) == {
            "slug",
            "name",
            "public_code",
            "availability_status",
            "cover_media",
        }
        assert piece["cover_media"] is not None
        assert piece["cover_media"]["role"] == "hero"


def test_get_artisan_detail_no_internal_fields_leak():
    response = client.get("/api/v1/artisans/artisan-demo-01")
    raw = response.text
    for forbidden in ("publication_status", "storage_path", "created_at", "updated_at", '"id"'):
        assert forbidden not in raw


def test_unknown_artisan_slug_returns_documented_404():
    response = client.get("/api/v1/artisans/does-not-exist-at-all")
    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


# --- Publication filtering (rollback-scoped, seed.py untouched) ---------


def test_unpublished_artisan_excluded_from_list_and_matches_unknown_404(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    try:
        db_session.add(
            Artisan(
                slug="unpublished-demo-artisan",
                full_name="Unpublished Demo Artisan",
                publication_status=PublicationStatus.draft,
            )
        )
        db_session.flush()

        list_response = client.get("/api/v1/artisans")
        slugs = [a["slug"] for a in list_response.json()["data"]]
        assert "unpublished-demo-artisan" not in slugs

        unpublished_response = client.get("/api/v1/artisans/unpublished-demo-artisan")
        unknown_response = client.get("/api/v1/artisans/does-not-exist-at-all")

        assert unpublished_response.status_code == 404
        assert unknown_response.status_code == 404
        assert unpublished_response.status_code == unknown_response.status_code
        assert unpublished_response.json() == unknown_response.json() == NOT_FOUND_BODY
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_archived_media_excluded_from_detail(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    try:
        artisan = db_session.execute(
            select(Artisan).where(Artisan.slug == "artisan-demo-01")
        ).scalar_one()
        db_session.add(
            MediaAsset(
                artisan_id=artisan.id,
                media_type=MediaType.image,
                role=MediaRole.gallery,
                storage_path="demo/artisans/artisan-demo-01/archived.jpg",
                alt_text="Should not appear",
                status=MediaAssetStatus.archived,
            )
        )
        db_session.flush()

        response = client.get("/api/v1/artisans/artisan-demo-01")
        body = response.json()
        assert len(body["media"]) == 1
        assert all(
            m["url"] != "/media/demo/artisans/artisan-demo-01/archived.jpg" for m in body["media"]
        )
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_languages_hidden_when_not_public(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    try:
        artisan = db_session.execute(
            select(Artisan).where(Artisan.slug == "artesana-demo-02")
        ).scalar_one()
        artisan.languages = ["zapoteco"]
        artisan.languages_public = False
        db_session.flush()

        response = client.get("/api/v1/artisans/artesana-demo-02")
        assert response.json()["languages"] == []
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_languages_shown_when_public(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    try:
        artisan = db_session.execute(
            select(Artisan).where(Artisan.slug == "artesana-demo-02")
        ).scalar_one()
        artisan.languages = ["zapoteco"]
        artisan.languages_public = True
        db_session.flush()

        response = client.get("/api/v1/artisans/artesana-demo-02")
        assert response.json()["languages"] == ["zapoteco"]
    finally:
        app.dependency_overrides.pop(get_db, None)
