"""PEND-029: optional keyset pagination on GET /artisans and GET /pieces.
Without ``limit`` the whole set comes back exactly as before."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.base import SessionLocal
from app.db.seed import ARTISANS, PIECES, seed
from app.main import app

client = TestClient(app)
ARTISAN_SLUGS = sorted(a["slug"] for a in ARTISANS)
PIECE_SLUGS = sorted(p["slug"] for p in PIECES)


@pytest.fixture(autouse=True)
def _ensure_seed():
    with SessionLocal() as session:
        with session.begin():
            seed(session)


def _walk(path: str, limit: int, **params) -> list[dict]:
    pages, cursor = [], None
    while True:
        query = {"limit": limit, **params, **({"cursor": cursor} if cursor else {})}
        body = client.get(path, params=query).json()
        pages.append(body)
        cursor = body["meta"]["next_cursor"]
        if cursor is None:
            return pages


@pytest.mark.parametrize("path,slugs", [("/api/v1/artisans", ARTISAN_SLUGS), ("/api/v1/pieces", PIECE_SLUGS)])
def test_walking_the_pages_returns_everything_once_in_order(path, slugs):
    pages = _walk(path, 2)
    assert [item["slug"] for page in pages for item in page["data"]] == slugs
    assert all(len(page["data"]) <= 2 for page in pages)
    assert all(page["meta"]["total"] == len(slugs) for page in pages)
    assert pages[-1]["meta"]["next_cursor"] is None


@pytest.mark.parametrize("path,count", [("/api/v1/artisans", len(ARTISAN_SLUGS)), ("/api/v1/pieces", len(PIECE_SLUGS))])
def test_without_limit_everything_comes_back_and_there_is_no_next_page(path, count):
    body = client.get(path).json()
    assert len(body["data"]) == count
    assert body["meta"] == {"total": count, "next_cursor": None}


def test_a_page_that_ends_exactly_at_the_last_item_has_no_next_cursor():
    body = client.get("/api/v1/artisans", params={"limit": len(ARTISAN_SLUGS)}).json()
    assert body["meta"]["next_cursor"] is None


def test_filters_keep_working_with_pages():
    key = PIECES[0]["artisan_key"]
    artisan = next(a["slug"] for a in ARTISANS if a["key"] == key)
    expected = sorted(p["slug"] for p in PIECES if p["artisan_key"] == key)
    pages = _walk("/api/v1/pieces", 1, artisan=artisan)
    assert [i["slug"] for page in pages for i in page["data"]] == expected
    assert pages[0]["meta"]["total"] == len(expected)


@pytest.mark.parametrize("params", [{"cursor": "***"}, {"cursor": "/w"}])
def test_a_bad_cursor_is_a_400_with_the_envelope(params):
    r = client.get("/api/v1/artisans", params=params)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_cursor"


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_limit_out_of_range_is_a_422(limit):
    assert client.get("/api/v1/pieces", params={"limit": limit}).status_code == 422
