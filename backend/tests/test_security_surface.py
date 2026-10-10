"""Security surface of the API: who may call what (audit 2026-10-10).

Two guards that outlive the audit: every administrative route refuses a call
without the Cloudflare Access token, and the list of public routes that change
state is explicit, so a new one cannot appear unnoticed.
"""
from __future__ import annotations

import uuid

from app.main import app
from tests.test_admin_api import client, verifier  # noqa: F401 (fixtures: Access verifier configured)

SOME_ID = str(uuid.uuid4())
WRITE = ("post", "put", "patch", "delete")


def _operations():
    for path, item in app.openapi()["paths"].items():
        for method in item:
            if method in ("get", *WRITE):
                yield path, method


def _fill(path: str) -> str:
    out, depth = [], 0
    for ch in path:
        if ch == "{":
            depth += 1
            out.append(SOME_ID if "owner" not in "".join(out[-8:]) else "artisans")
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def test_every_administrative_route_refuses_a_call_without_the_token(client):
    checked = 0
    for path, method in _operations():
        if not path.startswith("/api/admin"):
            continue
        response = getattr(client, method)(_fill(path))
        assert response.status_code == 401, (method.upper(), path, response.status_code)
        checked += 1
    assert checked > 60  # the whole administrative surface, not a sample


# Public routes that change state. Each one is gated by a secret the caller holds
# (token, card key, code) and has its own limits (see SECURITY.md); a new entry
# here is a decision, not an accident.
PUBLIC_WRITES = {
    ("post", "/api/v1/artisan-authorizations/decision"),
    ("post", "/api/v1/artisan-authorizations/resolve"),
    ("post", "/api/v1/certificates/claim"),
    ("post", "/api/v1/certificates/pin-reset/confirm"),
    ("post", "/api/v1/certificates/pin-reset/request"),
    ("post", "/api/v1/certificates/resolve"),
    ("post", "/api/v1/certificates/unlock"),
    ("post", "/api/v1/design-reviews/decision"),
    ("post", "/api/v1/design-reviews/resolve"),
}


def test_the_public_routes_that_change_state_are_the_known_ones():
    found = {(m, p) for p, m in _operations() if p.startswith("/api/v1") and m in WRITE}
    assert found == PUBLIC_WRITES, sorted(found ^ PUBLIC_WRITES)



def test_every_response_carries_the_baseline_security_headers(client):
    expected = {"x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "x-frame-options": "DENY",
                "strict-transport-security": "max-age=31536000; includeSubDomains"}
    for path in ("/api/v1/artisans", "/api/v1/hero", "/api/v1/nope", "/api/admin/v1/me", "/media/hero/x/y.mp4",
                 "/media/../etc/passwd"):
        headers = client.get(path).headers
        for name, value in expected.items():
            assert headers.get(name) == value, (path, name)
    # A header the app sets itself is not overwritten.
    assert client.get("/media/hero/x/y.mp4").headers["x-content-type-options"] == "nosniff"
