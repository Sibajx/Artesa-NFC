"""GET/HEAD /health (audit finding F-14, docs/OPERATIONS.md).

One endpoint, database-aware: 200 when the API can reach its database, 503 when
it cannot. The bodies are exact fixed strings and never carry anything about
the failure; both statuses are `Cache-Control: no-store`.
"""
from __future__ import annotations

import logging

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.db.session import check_database_connection
from app.main import app

OK_BODY = {"status": "ok", "database": "connected"}
DOWN_BODY = {"status": "unavailable", "database": "unavailable"}


def _fake_db_ok() -> bool:
    return True


def _fake_db_down() -> bool:
    return False


client = TestClient(app)


@pytest.fixture()
def db_ok():
    app.dependency_overrides[check_database_connection] = _fake_db_ok
    try:
        yield
    finally:
        app.dependency_overrides.pop(check_database_connection, None)


@pytest.fixture()
def db_down():
    app.dependency_overrides[check_database_connection] = _fake_db_down
    try:
        yield
    finally:
        app.dependency_overrides.pop(check_database_connection, None)


# --- GET ---------------------------------------------------------------------


def test_health_is_200_with_exact_body_when_database_connected(db_ok):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == OK_BODY


def test_health_is_503_with_exact_body_when_database_down(db_down):
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == DOWN_BODY


@pytest.mark.parametrize("fixture_name", ["db_ok", "db_down"])
def test_health_is_never_cacheable(request, fixture_name):
    request.getfixturevalue(fixture_name)
    response = client.get("/health")
    assert response.headers["cache-control"] == "no-store"


def test_health_against_the_real_test_database_is_200():
    # No override: the real check_database_connection against the test DB.
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == OK_BODY


# --- HEAD --------------------------------------------------------------------


def test_head_health_is_200_when_database_connected(db_ok):
    response = client.head("/health")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b""


def test_head_health_is_503_when_database_down(db_down):
    response = client.head("/health")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b""


def test_other_methods_on_health_stay_405(db_ok):
    assert client.post("/health").status_code == 405
    assert client.delete("/health").status_code == 405


# --- No connection details leak ---------------------------------------------------

CANARY_USER = "HealthCanaryUser"
CANARY_PASSWORD = "HealthCanaryPW"
CANARY_HOST = "health-canary-host.internal"


@pytest.mark.parametrize("method", ["get", "head"])
def test_a_real_connection_failure_reports_nothing_about_the_failure(monkeypatch, caplog, method):
    """The real check_database_connection, with the driver raising an error
    whose message carries a user, a password, a host and a URL: none of it may
    reach the response (status line, headers, body) or any log record."""

    def failing_connect(*args, **kwargs):
        raise psycopg.OperationalError(
            f"connection failed: postgresql://{CANARY_USER}:{CANARY_PASSWORD}@{CANARY_HOST}:5432/db "
            f"password authentication failed for user {CANARY_USER}"
        )

    monkeypatch.setattr(psycopg, "connect", failing_connect)
    with caplog.at_level(logging.DEBUG):
        response = getattr(client, method)("/health")

    assert response.status_code == 503
    if method == "get":
        assert response.json() == DOWN_BODY
    everything = response.text + str(dict(response.headers)) + caplog.text
    for canary in (CANARY_USER, CANARY_PASSWORD, CANARY_HOST, "postgresql://"):
        assert canary not in everything
