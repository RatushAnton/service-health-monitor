"""Tests for the authenticated administration API."""

import pytest
from fastapi.testclient import TestClient

from healthmon.admin_api import app
from healthmon.registry import bootstrap_registry

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
        yield client, token, token_file


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
