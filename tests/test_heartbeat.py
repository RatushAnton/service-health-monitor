"""Unit tests for external heartbeat monitoring."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from healthmon import heartbeat

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
URL = "https://hc-ping.com/123e4567-e89b-42d3-a456-426614174000"


def make_db(tmp_path, age=60, enabled=True):
    db = tmp_path / "health.db"

    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE monitor_registry (
                target_id TEXT PRIMARY KEY,
                enabled INTEGER,
                archived_at TEXT
            );
            CREATE TABLE checks (
                id INTEGER PRIMARY KEY,
                target_id TEXT,
                checked_at TEXT
            );
        """)

        conn.execute(
            "INSERT INTO monitor_registry VALUES (?, ?, NULL)",
            ("home_gateway_dns", int(enabled)),
        )

        conn.execute(
            "INSERT INTO checks (target_id, checked_at) VALUES (?, ?)",
            (
                "home_gateway_dns",
                (NOW - timedelta(seconds=age)).isoformat(),
            ),
        )

    return db


def test_fresh_worker_is_healthy(tmp_path):
    db = make_db(tmp_path)

    fresh, _ = heartbeat.worker_is_fresh(db, max_age_seconds=600, now=NOW)

    assert fresh is True


def test_stale_worker_is_unhealthy(tmp_path):
    db = make_db(tmp_path, age=900)

    fresh, reason = heartbeat.worker_is_fresh(db, max_age_seconds=600, now=NOW)

    assert fresh is False
    assert "Stale" in reason


def test_no_enabled_monitors_is_unhealthy(tmp_path):
    db = make_db(tmp_path, enabled=False)

    fresh, _ = heartbeat.worker_is_fresh(db, now=NOW)

    assert fresh is False


def test_future_timestamp_is_rejected(tmp_path):
    db = make_db(tmp_path, age=-120)

    fresh, reason = heartbeat.worker_is_fresh(db, now=NOW)

    assert fresh is False
    assert "Future" in reason


def test_fresh_worker_sends_ping(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    captured = []

    def fake_post(url, **kwargs):
        captured.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(heartbeat.httpx, "post", fake_post)

    assert heartbeat.run_cycle(str(db), URL, 600, now=NOW) == 0

    assert captured[0][0] == URL
    assert captured[0][1]["follow_redirects"] is False
    assert captured[0][1]["trust_env"] is False


def test_stale_worker_never_sends_ping(tmp_path, monkeypatch):
    db = make_db(tmp_path, age=900)

    def forbidden(*args, **kwargs):
        raise AssertionError("No heartbeat should be sent")

    monkeypatch.setattr(heartbeat.httpx, "post", forbidden)

    assert heartbeat.run_cycle(str(db), URL, 600, now=NOW) == 1


def test_dry_run_never_sends_ping(tmp_path, monkeypatch):
    db = make_db(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Dry run must not send requests")

    monkeypatch.setattr(heartbeat.httpx, "post", forbidden)

    assert heartbeat.run_cycle(str(db), URL, 600, dry_run=True, now=NOW) == 0


def test_http_failure_does_not_leak_url(tmp_path, monkeypatch, capsys):
    db = make_db(tmp_path)

    def fake_failure(url, **kwargs):
        raise httpx.ConnectError(
            "Secret URL in exception",
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(heartbeat.httpx, "post", fake_failure)

    assert heartbeat.run_cycle(str(db), URL, 600, now=NOW) == 1

    assert URL not in capsys.readouterr().out


@pytest.mark.parametrize(
    "url",
    [
        "http://hc-ping.com/123e4567-e89b-42d3-a456-426614174000",
        "https://example.com/123e4567-e89b-42d3-a456-426614174000",
        "https://hc-ping.com/not-a-uuid",
        URL + "/fail",
        URL + "?token=secret",
    ],
)
def test_invalid_ping_url_rejected(url):
    assert not heartbeat.validate_ping_url(url)


def test_optional_compose_is_isolated():
    compose = yaml.safe_load(
        Path("compose.pi.heartbeat.yaml").read_text(encoding="utf-8")
    )
    service = compose["services"]["heartbeat"]

    assert "heartbeat" in service["profiles"]
    assert service["read_only"] is True
    assert service["user"] == "10001:10001"
    assert "ports" not in service
    assert "build" not in service
    assert "healthchecks_ping_url" in service["secrets"]
    assert service["volumes"] == ["pi_data:/var/lib/service-health-monitor:ro"]
