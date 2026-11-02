"""Tests for the authenticated administration API."""

import pytest
from fastapi.testclient import TestClient

from healthmon.admin_api import app
from healthmon.registry import bootstrap_registry, read_registry_config
from healthmon.storage import get_history, save_result

EXAMPLE_CONFIG = """
settings:
  timeout_seconds: 3
  failure_threshold: 3
  recovery_threshold: 1

targets:
  - id: example_dns
    name: Example DNS
    type: dns
    host: example.com
"""


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def demo_tcp():
    return {
        "id": "new_tcp",
        "name": "New TCP",
        "type": "tcp",
        "host": "127.0.0.1",
        "port": 443,
    }


def test_create_tcp_monitor(admin_client):
    client, token, db = admin_client

    response = client.post(
        "/api/admin/monitors",
        headers=auth(token),
        json=demo_tcp(),
    )

    assert response.status_code == 201
    assert response.json()["enabled"] is True
    assert "new_tcp" in {target["id"] for target in read_registry_config(db)["targets"]}


def test_duplicate_id_returns_409(admin_client):
    client, token, _ = admin_client

    response = client.post(
        "/api/admin/monitors",
        headers=auth(token),
        json=demo_tcp(),
    )

    assert response.status_code == 201

    duplicate = client.post(
        "/api/admin/monitors",
        headers=auth(token),
        json=demo_tcp(),
    )

    assert duplicate.status_code == 409


def test_invalid_monitor_is_rejected(admin_client):
    client, token, _ = admin_client

    invalid = demo_tcp()
    invalid["port"] = "443"

    response = client.post(
        "/api/admin/monitors",
        headers=auth(token),
        json=invalid,
    )

    assert response.status_code == 422


def test_unauthorized_create_is_rejected(admin_client):
    client, _, _ = admin_client

    response = client.post(
        "/api/admin/monitors",
        json=demo_tcp(),
    )

    assert response.status_code == 401


def test_edit_monitor(admin_client):
    client, token, _ = admin_client

    client.post(
        "/api/admin/monitors",
        headers=auth(token),
        json=demo_tcp(),
    )

    edited = demo_tcp()
    edited["port"] = 8443

    response = client.put(
        "/api/admin/monitors/new_tcp",
        headers=auth(token),
        json=edited,
    )

    assert response.status_code == 200
    assert response.json()["port"] == 8443


def test_identity_cannot_change(admin_client):
    client, token, _ = admin_client

    edited = {
        "id": "renamed_dns",
        "name": "Example DNS",
        "type": "dns",
        "host": "example.com",
    }

    response = client.put(
        "/api/admin/monitors/example_dns",
        headers=auth(token),
        json=edited,
    )

    assert response.status_code == 409


def test_disable_and_reenable(admin_client):
    client, token, _ = admin_client

    disabled = client.patch(
        "/api/admin/monitors/example_dns/enabled",
        headers=auth(token),
        json={"enabled": False},
    )

    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    enabled = client.patch(
        "/api/admin/monitors/example_dns/enabled",
        headers=auth(token),
        json={"enabled": True},
    )

    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True


def test_archive_keeps_history(admin_client, monkeypatch):
    client, token, _ = admin_client
    import os
    from pathlib import Path

    db_path = Path(os.environ["HEALTHMON_DB_PATH"])

    save_result(
        db_path,
        {
            "target_id": "example_dns",
            "check_type": "dns",
            "checked_at": "2026-11-02T08:00:00+00:00",
            "success": True,
            "latency_ms": 5.0,
            "status_code": None,
            "error_type": None,
            "message": "OK",
        },
    )

    response = client.delete(
        "/api/admin/monitors/example_dns",
        headers=auth(token),
    )

    assert response.status_code == 204
    assert read_registry_config(db_path)["targets"] == []
    assert len(get_history(db_path, "example_dns")) == 1

    from fastapi.testclient import TestClient

    from healthmon.api import app as read_only_app

    with TestClient(read_only_app) as read_client:
        visible = read_client.get("/api/monitors")

    assert visible.status_code == 200
    assert visible.json()["monitors"] == []


def test_archived_id_cannot_be_reused(admin_client):
    client, token, _ = admin_client

    assert (
        client.delete(
            "/api/admin/monitors/example_dns",
            headers=auth(token),
        ).status_code
        == 204
    )

    response = client.post(
        "/api/admin/monitors",
        headers=auth(token),
        json={
            "id": "example_dns",
            "name": "Replacement",
            "type": "dns",
            "host": "example.org",
        },
    )

    assert response.status_code == 409


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    db = tmp_path / "health.db"
    config = tmp_path / "targets.yaml"
    config.write_text(EXAMPLE_CONFIG, encoding="utf-8")

    bootstrap_registry(db, config)

    token = "test-only-token-" + "x" * 48
    token_file = tmp_path / "admin-token.txt"
    token_file.write_text(token, encoding="utf-8")

    monkeypatch.setenv("HEALTHMON_DB_PATH", str(db))
    monkeypatch.setenv(
        "HEALTHMON_ADMIN_TOKEN_FILE",
        str(token_file),
    )

    with TestClient(app) as client:
        yield client, token, db


def test_missing_authorization_returns_401(admin_client):
    client, _, _ = admin_client

    response = client.get("/api/admin/monitors")

    assert response.status_code == 401


def test_wrong_authorization_returns_401(admin_client):
    client, _, _ = admin_client

    response = client.get(
        "/api/admin/monitors",
        headers={"Authorization": "Bearer incorrect-token"},
    )

    assert response.status_code == 401


def test_valid_authorization_returns_monitors(admin_client):
    client, token, _ = admin_client

    response = client.get(
        "/api/admin/monitors",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200

    monitors = response.json()["monitors"]
    assert len(monitors) == 1
    assert monitors[0]["id"] == "example_dns"
    assert monitors[0]["enabled"] is True


def test_missing_secret_fails_closed(
    admin_client,
    tmp_path,
    monkeypatch,
):
    client, token, _ = admin_client

    monkeypatch.setenv(
        "HEALTHMON_ADMIN_TOKEN_FILE",
        str(tmp_path / "missing-token.txt"),
    )

    response = client.get(
        "/api/admin/monitors",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503
