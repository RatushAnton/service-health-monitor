"""Tests for the read-only monitoring API."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from healthmon.api import app
from healthmon.storage import init_db, record_check_with_incident


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Create an isolated database and API client."""
    db_path = tmp_path / "health.db"
    init_db(db_path)
    monkeypatch.setenv("HEALTHMON_DB_PATH", str(db_path))

    with TestClient(app) as client:
        yield client, db_path


def record_test_check(db_path, success=True):
    """Persist a synthetic check, without network requests."""
    return record_check_with_incident(
        db_path,
        {
            "target_id": "demo",
            "check_type": "http",
            "checked_at": datetime.now(UTC).isoformat(),
            "success": success,
            "latency_ms": 12.5,
            "status_code": 200 if success else None,
            "error_type": None if success else "HTTP_ERROR",
            "message": "OK" if success else "Unavailable",
        },
        failure_threshold=1,
        recovery_threshold=1,
    )


def test_health_endpoint(api_client):
    client, _ = api_client

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_empty_monitor_list(api_client):
    client, _ = api_client

    response = client.get("/api/monitors")

    assert response.status_code == 200
    assert response.json() == {"monitors": []}


def test_monitor_list_and_details(api_client):
    client, db_path = api_client
    record_test_check(db_path)

    response = client.get("/api/monitors")
    assert response.status_code == 200

    monitors = response.json()["monitors"]
    assert len(monitors) == 1
    assert monitors[0]["id"] == "demo"
    assert monitors[0]["status"] == "UP"

    detail = client.get("/api/monitors/demo")
    assert detail.status_code == 200
    assert detail.json()["latency_ms"] == 12.5

    missing = client.get("/api/monitors/missing")
    assert missing.status_code == 404


def test_history_and_limit_validation(api_client):
    client, db_path = api_client

    record_test_check(db_path, success=True)
    record_test_check(db_path, success=False)

    response = client.get(
        "/api/monitors/demo/history",
        params={"limit": 1},
    )

    assert response.status_code == 200
    assert len(response.json()["history"]) == 1
    assert response.json()["history"][0]["status"] == "DOWN"

    invalid = client.get(
        "/api/monitors/demo/history",
        params={"limit": 0},
    )
    assert invalid.status_code == 422


def test_incident_events(api_client):
    client, db_path = api_client

    record_test_check(db_path, success=False)
    record_test_check(db_path, success=True)

    response = client.get("/api/incidents")

    assert response.status_code == 200
    events = response.json()["incidents"]
    assert [event["event_type"] for event in events] == [
        "RECOVERED",
        "DOWN",
    ]


def test_missing_database_returns_503(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "HEALTHMON_DB_PATH",
        str(tmp_path / "missing.db"),
    )

    with TestClient(app) as client:
        response = client.get("/api/monitors")
        assert response.status_code == 503

        response = client.get("/api/health")
        assert response.status_code == 200
