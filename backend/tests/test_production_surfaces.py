"""Interactive docs and the OpenAPI schema by environment (audit finding F-14,
docs/OPERATIONS.md): APP_ENV=production and APP_ENV=staging serve none of
/docs, /redoc, /openapi.json or /docs/oauth2-redirect; local and test keep them.

`app.main` builds the FastAPI app once, at import, from the settings of that
process, so each environment is exercised in a fresh interpreter (a subprocess
with an explicit, minimal environment) rather than by reloading modules inside
the pytest process. Nothing here touches a database: the URL below is a
production-grade dummy that is never connected to.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parents[1]

DOCS_PATHS = ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]
# Not a real credential and never dialled: only needs to pass the staging /
# production "no placeholder password" guard.
DUMMY_DATABASE_URL = "postgresql://surfaces_user:surfaces-not-a-secret@127.0.0.1:9/surfaces_test"
NOT_FOUND = {"error": {"code": "not_found", "message": "The requested resource does not exist."}}

_PROBE = r"""
import json
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
paths = %s
responses = {p: client.get(p) for p in paths}
print(json.dumps({
    "status": {p: r.status_code for p, r in responses.items()},
    "bodies": {p: r.text for p, r in responses.items() if r.status_code == 404},
    "route_paths": sorted({getattr(route, "path", "") for route in app.routes}),
    "docs_url": app.docs_url, "redoc_url": app.redoc_url, "openapi_url": app.openapi_url,
    "health": client.get("/health").status_code,
}))
""" % json.dumps(DOCS_PATHS)


def _probe(app_env: str) -> dict:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(BACKEND_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "APP_ENV": app_env,
        "DATABASE_URL": DUMMY_DATABASE_URL,
        "CORS_ALLOWED_ORIGINS": "https://artesanfc.com",
        "DEBUG": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("app_env", ["production", "staging"])
def test_production_and_staging_serve_no_docs_and_no_schema(app_env):
    probe = _probe(app_env)

    assert probe["status"] == {path: 404 for path in DOCS_PATHS}
    for path in DOCS_PATHS:
        assert json.loads(probe["bodies"][path]) == NOT_FOUND
    assert probe["docs_url"] is None
    assert probe["redoc_url"] is None
    assert probe["openapi_url"] is None
    # The routes are not registered at all (not merely hidden).
    assert not set(DOCS_PATHS) & set(probe["route_paths"])
    # ...and nothing else went away with them.
    assert "/health" in probe["route_paths"]
    assert "/api/v1/certificates/resolve" in probe["route_paths"]
    assert "/api/v1/pieces/{slug}" in probe["route_paths"]
    # 503 or 200 depending on the database; the point is that /health is still routed.
    assert probe["health"] in (200, 503)


@pytest.mark.parametrize("app_env", ["local", "test"])
def test_local_and_test_keep_the_docs(app_env):
    probe = _probe(app_env)

    assert probe["status"] == {path: 200 for path in DOCS_PATHS}
    assert set(DOCS_PATHS) <= set(probe["route_paths"])
    assert probe["docs_url"] == "/docs"
    assert probe["redoc_url"] == "/redoc"
    assert probe["openapi_url"] == "/openapi.json"


def test_this_pytest_process_is_APP_ENV_test_and_serves_the_docs():
    # The in-process app is the APP_ENV=test one; the suite relies on it.
    client = TestClient(app)
    for path in DOCS_PATHS:
        assert client.get(path).status_code == 200
    assert "/api/v1/artisans" in client.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize(
    "app_env,expected",
    [("local", True), ("test", True), ("staging", False), ("production", False)],
)
def test_docs_enabled_setting(monkeypatch, app_env, expected):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("DATABASE_URL", DUMMY_DATABASE_URL)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://artesanfc.com")
    monkeypatch.setenv("DEBUG", "false")
    assert Settings(_env_file=None).docs_enabled is expected
