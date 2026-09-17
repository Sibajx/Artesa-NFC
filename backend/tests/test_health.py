from fastapi.testclient import TestClient

from app.db.session import check_database_connection
from app.main import app


def _fake_db_ok() -> bool:
    return True


def _fake_db_down() -> bool:
    return False


client = TestClient(app)


def test_health_reports_ok_when_database_connected():
    app.dependency_overrides[check_database_connection] = _fake_db_ok
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(check_database_connection, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


def test_health_reports_unavailable_when_database_down():
    app.dependency_overrides[check_database_connection] = _fake_db_down
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(check_database_connection, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "unavailable"
