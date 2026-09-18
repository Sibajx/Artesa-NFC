"""Focused tests for the CORS allowlist configured in app/main.py.

Uses GET /api/v1/artisans as the exercised route: it's the same public,
GET-only endpoint the frontend integration needs, so these tests verify
the real browser-facing behavior rather than a synthetic route. The
response body/status of that endpoint is covered by test_api_artisans.py;
these tests only assert on CORS headers.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)

ALLOWED_ORIGIN = get_settings().cors_allowed_origins_list[0]
DISALLOWED_ORIGIN = "https://evil.example"


def test_allowed_origin_get_receives_matching_cors_header():
    response = client.get("/api/v1/artisans", headers={"Origin": ALLOWED_ORIGIN})
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_disallowed_origin_get_still_responds_without_cors_header():
    response = client.get("/api/v1/artisans", headers={"Origin": DISALLOWED_ORIGIN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_request_without_origin_header_is_unaffected():
    response = client.get("/api/v1/artisans")
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_allowed_origin_preflight_options_grants_get():
    response = client.options(
        "/api/v1/artisans",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "GET" in response.headers["access-control-allow-methods"]


def test_disallowed_origin_preflight_options_rejected():
    response = client.options(
        "/api/v1/artisans",
        headers={
            "Origin": DISALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_allowed_origin_preflight_for_disallowed_method_rejected():
    # Origin and method are validated independently by CORSMiddleware: an
    # allowed origin still gets Access-Control-Allow-Origin echoed back, but
    # the overall preflight fails (400) and Access-Control-Allow-Methods
    # never lists POST - which is what stops a browser from proceeding.
    response = client.options(
        "/api/v1/artisans",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400
    assert "POST" not in response.headers["access-control-allow-methods"]


def test_no_credentials_header_is_ever_sent():
    response = client.get("/api/v1/artisans", headers={"Origin": ALLOWED_ORIGIN})
    assert "access-control-allow-credentials" not in response.headers
