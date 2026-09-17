import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_db
from app.db.base import SessionLocal
from app.db.seed import ARTISANS, PIECES, seed
from app.main import app
from app.models.artisan import Artisan
from app.models.enums import PublicationStatus
from app.models.media_asset import MediaAsset, MediaAssetStatus, MediaRole, MediaType
from app.models.piece import Piece

client = TestClient(app)

APPROVED_PIECE_SLUGS_SORTED = sorted(f["slug"] for f in PIECES)

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
    before each test, same as tests/test_api_artisans.py does for its own
    suite."""
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


def _artisan_key_for(piece_slug: str) -> str:
    return next(f["artisan_key"] for f in PIECES if f["slug"] == piece_slug)


def _artisan_slug_for(piece_slug: str) -> str:
    artisan_key = _artisan_key_for(piece_slug)
    return next(f["slug"] for f in ARTISANS if f["key"] == artisan_key)


# --- GET /api/v1/pieces --------------------------------------------------


def test_list_pieces_returns_200():
    response = client.get("/api/v1/pieces")
    assert response.status_code == 200


def test_list_pieces_returns_published_demo_pieces_in_deterministic_order():
    response = client.get("/api/v1/pieces")
    body = response.json()
    slugs = [p["slug"] for p in body["data"]]
    assert slugs == APPROVED_PIECE_SLUGS_SORTED
    assert body["meta"]["total"] == 4


def test_list_pieces_summary_has_only_documented_fields():
    response = client.get("/api/v1/pieces")
    for piece in response.json()["data"]:
        assert set(piece.keys()) == {
            "slug",
            "name",
            "public_code",
            "availability_status",
            "cover_media",
        }


def test_list_pieces_no_internal_fields_leak():
    response = client.get("/api/v1/pieces")
    raw = response.text
    assert "publication_status" not in raw
    assert "storage_path" not in raw
    assert "artisan_id" not in raw
    for piece in response.json()["data"]:
        for value in piece.values():
            if isinstance(value, str):
                assert not _looks_like_uuid(value)


def test_list_pieces_cover_media_matches_documented_shape():
    response = client.get("/api/v1/pieces")
    body = response.json()
    mascara = next(p for p in body["data"] if p["slug"] == "mascara-demo-01")
    assert mascara["cover_media"] is not None
    assert set(mascara["cover_media"].keys()) == {
        "type",
        "role",
        "url",
        "alt_text",
        "position",
        "format",
    }
    assert mascara["cover_media"]["role"] == "hero"
    assert mascara["cover_media"]["url"] == "/media/demo/pieces/mascara-demo-01/hero.jpg"


# --- GET /api/v1/pieces query filters -------------------------------------


def test_list_pieces_filtered_by_artisan_slug():
    artisan_slug = _artisan_slug_for("mascara-demo-01")
    response = client.get("/api/v1/pieces", params={"artisan": artisan_slug})
    body = response.json()
    slugs = {p["slug"] for p in body["data"]}
    assert slugs == {"mascara-demo-01", "figura-tallada-demo-01"}
    assert body["meta"]["total"] == 2


def test_list_pieces_filtered_by_availability_status():
    response = client.get("/api/v1/pieces", params={"availability_status": "available"})
    body = response.json()
    slugs = {p["slug"] for p in body["data"]}
    assert slugs == set(APPROVED_PIECE_SLUGS_SORTED)


def test_list_pieces_combined_filters():
    artisan_slug = _artisan_slug_for("mascara-demo-01")
    response = client.get(
        "/api/v1/pieces",
        params={"artisan": artisan_slug, "availability_status": "available"},
    )
    body = response.json()
    slugs = {p["slug"] for p in body["data"]}
    assert slugs == {"mascara-demo-01", "figura-tallada-demo-01"}


def test_list_pieces_combined_filters_zero_results():
    artisan_slug = _artisan_slug_for("mascara-demo-01")
    response = client.get(
        "/api/v1/pieces",
        params={"artisan": artisan_slug, "availability_status": "reserved"},
    )
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


def test_list_pieces_unknown_artisan_filter_returns_zero_results():
    response = client.get("/api/v1/pieces", params={"artisan": "does-not-exist-at-all"})
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


def test_list_pieces_invalid_availability_status_returns_documented_validation_error():
    response = client.get("/api/v1/pieces", params={"availability_status": "not-a-real-status"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "details" in body["error"]
    fields = {d["field"] for d in body["error"]["details"]}
    assert "availability_status" in fields


def test_list_pieces_artisan_filter_cannot_reveal_piece_under_unpublished_artisan(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    try:
        artisan = db_session.execute(
            select(Artisan).where(Artisan.slug == _artisan_slug_for("mascara-demo-01"))
        ).scalar_one()
        artisan.publication_status = PublicationStatus.draft
        db_session.flush()

        response = client.get("/api/v1/pieces", params={"artisan": artisan.slug})
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0
    finally:
        app.dependency_overrides.pop(get_db, None)


# --- GET /api/v1/pieces/{slug} --------------------------------------------


def test_get_piece_detail_returns_200():
    response = client.get("/api/v1/pieces/mascara-demo-01")
    assert response.status_code == 200


def test_get_piece_detail_matches_documented_shape():
    response = client.get("/api/v1/pieces/mascara-demo-01")
    body = response.json()
    assert set(body.keys()) == {
        "slug",
        "public_code",
        "name",
        "description",
        "history",
        "materials",
        "technique",
        "origin",
        "creation_year",
        "creation_date",
        "dimensions",
        "visual_theme",
        "availability_status",
        "artisan",
        "media",
    }
    assert body["slug"] == "mascara-demo-01"
    assert body["public_code"] == "DEMO-MASCARA-01"
    assert body["dimensions"] is None
    assert body["history"] is None
    assert body["materials"] == []


def test_get_piece_detail_artisan_embed_is_correct():
    response = client.get("/api/v1/pieces/mascara-demo-01")
    body = response.json()
    assert set(body["artisan"].keys()) == {"slug", "full_name", "artistic_name"}
    assert body["artisan"]["slug"] == "artisan-demo-01"
    assert body["artisan"]["full_name"] == "Artesano Demo Uno"


def test_get_piece_detail_media_output_is_correct():
    response = client.get("/api/v1/pieces/mascara-demo-01")
    body = response.json()
    assert len(body["media"]) == 1
    media = body["media"][0]
    assert set(media.keys()) == {"type", "role", "url", "alt_text", "position", "format"}
    assert media["type"] == "image"
    assert media["role"] == "hero"
    assert media["url"] == "/media/demo/pieces/mascara-demo-01/hero.jpg"


def test_get_piece_detail_no_internal_fields_leak():
    response = client.get("/api/v1/pieces/mascara-demo-01")
    raw = response.text
    for forbidden in (
        "publication_status",
        "storage_path",
        "created_at",
        "updated_at",
        "artisan_id",
        '"id"',
        "has_certificate",
        "certificate_id",
        "certificate_status",
        "has_nfc",
        "nfc_tag",
    ):
        assert forbidden not in raw


def test_unknown_piece_slug_returns_documented_404():
    response = client.get("/api/v1/pieces/does-not-exist-at-all")
    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


# --- Publication filtering (rollback-scoped, seed.py untouched) ---------


def test_unpublished_piece_excluded_from_list_and_matches_unknown_404(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    try:
        artisan = db_session.execute(
            select(Artisan).where(Artisan.slug == "artisan-demo-01")
        ).scalar_one()
        db_session.add(
            Piece(
                slug="unpublished-demo-piece",
                public_code="DEMO-UNPUBLISHED-01",
                artisan_id=artisan.id,
                name="Unpublished Demo Piece",
                publication_status=PublicationStatus.draft,
            )
        )
        db_session.flush()

        list_response = client.get("/api/v1/pieces")
        slugs = [p["slug"] for p in list_response.json()["data"]]
        assert "unpublished-demo-piece" not in slugs

        unpublished_response = client.get("/api/v1/pieces/unpublished-demo-piece")
        unknown_response = client.get("/api/v1/pieces/does-not-exist-at-all")

        assert unpublished_response.status_code == 404
        assert unknown_response.status_code == 404
        assert unpublished_response.json() == unknown_response.json() == NOT_FOUND_BODY
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_piece_under_unpublished_artisan_excluded_from_list_and_matches_unknown_404(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    try:
        artisan = db_session.execute(
            select(Artisan).where(Artisan.slug == "artisan-demo-01")
        ).scalar_one()
        artisan.publication_status = PublicationStatus.draft
        db_session.flush()

        list_response = client.get("/api/v1/pieces")
        slugs = [p["slug"] for p in list_response.json()["data"]]
        assert "mascara-demo-01" not in slugs
        assert "figura-tallada-demo-01" not in slugs

        orphaned_response = client.get("/api/v1/pieces/mascara-demo-01")
        unknown_response = client.get("/api/v1/pieces/does-not-exist-at-all")

        assert orphaned_response.status_code == 404
        assert unknown_response.status_code == 404
        assert orphaned_response.json() == unknown_response.json() == NOT_FOUND_BODY
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_archived_media_excluded_from_piece_detail(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    try:
        piece = db_session.execute(
            select(Piece).where(Piece.slug == "mascara-demo-01")
        ).scalar_one()
        db_session.add(
            MediaAsset(
                piece_id=piece.id,
                media_type=MediaType.image,
                role=MediaRole.gallery,
                storage_path="demo/pieces/mascara-demo-01/archived.jpg",
                alt_text="Should not appear",
                status=MediaAssetStatus.archived,
            )
        )
        db_session.flush()

        response = client.get("/api/v1/pieces/mascara-demo-01")
        body = response.json()
        assert len(body["media"]) == 1
        assert all(
            m["url"] != "/media/demo/pieces/mascara-demo-01/archived.jpg" for m in body["media"]
        )
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_piece_detail_dimensions_typed_when_present(db_session):
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    try:
        piece = db_session.execute(
            select(Piece).where(Piece.slug == "mascara-demo-01")
        ).scalar_one()
        piece.dimensions = {"height": 30, "width": 20, "depth": 15, "unit": "cm"}
        db_session.flush()

        response = client.get("/api/v1/pieces/mascara-demo-01")
        body = response.json()
        assert body["dimensions"] == {"height": 30, "width": 20, "depth": 15, "unit": "cm"}
    finally:
        app.dependency_overrides.pop(get_db, None)
