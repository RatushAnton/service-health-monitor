"""Tests for database-backed monitoring worker."""

import sqlite3

import pytest

from healthmon import cli, worker
from healthmon.registry import bootstrap_registry
from healthmon.storage import get_history, init_db

CONFIG = """
settings:
  timeout_seconds: 3
  failure_threshold: 3
  recovery_threshold: 1

targets:
  - id: demo_dns
    name: Demo DNS
    type: dns
    host: example.com

  - id: demo_tcp
    name: Demo TCP
    type: tcp
    host: 127.0.0.1
    port: 12345
"""


@pytest.fixture
def registry_db(tmp_path):
    config_path = tmp_path / "targets.yaml"
    config_path.write_text(CONFIG)

    db_path = tmp_path / "health.db"
    bootstrap_registry(db_path, config_path)

    return db_path


def test_database_worker_skips_disabled_targets(registry_db, monkeypatch):
    """Only enabled targets are probed."""
    with sqlite3.connect(registry_db) as connection:
        connection.execute(
            "UPDATE monitor_registry SET enabled = 0 WHERE target_id = 'demo_tcp'"
        )

    called = []

    def fake_probe(target, timeout):
        called.append(target["id"])

        return {
            "target_id": target["id"],
            "check_type": target["type"],
            "checked_at": "2026-11-02T08:00:00+00:00",
            "success": True,
            "latency_ms": 10.0,
            "status_code": None,
            "error_type": None,
            "message": "OK",
        }

    monkeypatch.setitem(cli.CHECKERS, "dns", fake_probe)
    monkeypatch.setitem(cli.CHECKERS, "tcp", fake_probe)

    result = worker.run_worker(
        "unused.yaml",
        str(registry_db),
        300,
        source="db",
        once=True,
    )

    assert result == 0
    assert called == ["demo_dns"]
    assert len(get_history(registry_db, "demo_dns")) == 1
    assert get_history(registry_db, "demo_tcp") == []


def test_database_worker_reloads_configuration(registry_db, monkeypatch):
    """Each worker cycle reads current enabled-state values."""
    cycles = []

    def fake_checks(config, db_path):
        cycles.append([target["id"] for target in config["targets"]])
        return 0

    monkeypatch.setattr(worker, "run_checks_config", fake_checks)

    class Shutdown:
        def __init__(self):
            self.wait_count = 0

        def is_set(self):
            return self.wait_count >= 2

        def wait(self, seconds):
            self.wait_count += 1

            if self.wait_count == 1:
                with sqlite3.connect(registry_db) as connection:
                    connection.execute(
                        "UPDATE monitor_registry SET enabled = 0 "
                        "WHERE target_id = 'demo_dns'"
                    )

    result = worker.run_worker(
        "unused.yaml",
        str(registry_db),
        300,
        source="db",
        stop_event=Shutdown(),
    )

    assert result == 0
    assert cycles == [
        ["demo_dns", "demo_tcp"],
        ["demo_tcp"],
    ]


def test_uninitialized_registry_returns_error(tmp_path):
    """Database mode must not silently use an empty registry."""
    db_path = tmp_path / "health.db"
    init_db(db_path)

    result = worker.run_worker(
        "unused.yaml",
        str(db_path),
        300,
        source="db",
        once=True,
    )

    assert result == 2


def test_all_disabled_monitors_are_allowed(registry_db, monkeypatch):
    """An empty enabled set is valid, unlike missing registry settings."""
    with sqlite3.connect(registry_db) as connection:
        connection.execute("UPDATE monitor_registry SET enabled = 0")

    def unexpected_probe(target, timeout):
        pytest.fail("Disabled monitor was probed")

    monkeypatch.setitem(cli.CHECKERS, "dns", unexpected_probe)
    monkeypatch.setitem(cli.CHECKERS, "tcp", unexpected_probe)

    result = worker.run_worker(
        "unused.yaml",
        str(registry_db),
        300,
        source="db",
        once=True,
    )

    assert result == 0
    assert get_history(registry_db, "demo_dns") == []
    assert get_history(registry_db, "demo_tcp") == []
