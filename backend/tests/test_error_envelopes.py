"""API_CONTRACT.md section 10: the public error envelope must be produced
consistently for framework-raised errors (unmatched route, wrong method) and
for a genuinely unhandled exception, not just for the application's own
not_found()/validation paths already covered by test_api_artisans.py and
test_api_pieces.py."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app

client = TestClient(app)

# TestClient re-raises unhandled server exceptions by default so tests fail
# loudly on real bugs; here we need the actual HTTP response the public
# client would see, so server exceptions must be turned into responses
# instead of propagating into the test.
server_client = TestClient(app, raise_server_exceptions=False)

_LEAK_MARKERS = (
    "Traceback",
    "traceback",
    "RuntimeError",
    "hunter2",
    "/var/lib/secret",
    "password=",
    "SELECT ",
    "psycopg",
    "sqlalchemy",
)


def _assert_no_internal_leak(raw: str) -> None:
    for marker in _LEAK_MARKERS:
        assert marker not in raw


# --- A. Unmatched route ----------------------------------------------------


def test_unmatched_route_returns_public_404_envelope():
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404

    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]

    _assert_no_internal_leak(response.text)


# --- B. Wrong method on an existing route -----------------------------------


def test_wrong_method_returns_public_405_envelope():
    response = client.delete("/api/v1/pieces")
    assert response.status_code == 405

    body = response.json()
    assert body["error"]["code"] == "method_not_allowed"
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]

    _assert_no_internal_leak(response.text)


# --- C. Generic unhandled exception -----------------------------------------


def _broken_get_db():
    # Simulates an unexpected failure below the API layer (e.g. a database
    # driver error). The message intentionally looks like it could leak a
    # secret/path/SQL fragment, so the assertions below prove none of that
    # reaches the public response.
    raise RuntimeError("Simulated failure: password=hunter2 at /var/lib/secret.sql")
    yield  # pragma: no cover - unreachable, keeps this a generator


def test_unhandled_exception_returns_generic_public_500_envelope():
    app.dependency_overrides[get_db] = _broken_get_db
    try:
        response = server_client.get("/api/v1/artisans")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 500

    body = response.json()
    assert body == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
        }
    }

    _assert_no_internal_leak(response.text)
