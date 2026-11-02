"""Tests for the SQLite-backed monitor registry."""

import sqlite3

import pytest

from healthmon.config import ConfigError
from healthmon.registry import (
    RegistryNotInitialized,
    bootstrap_registry,
    list_registry_monitors,
    read_registry_config,
)
from healthmon.storage import get_history, init_db, save_result

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
    record_type: A

  - id: demo_tcp
    name: Demo TCP
    type: tcp
    host: 1.1.1.1
    port: 443
"""


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "targets.yaml"
    path.write_text(CONFIG)
    return path


def test_bootstrap_and_preserve_history(tmp_path, config_file):
    db = tmp_path / "health.db"
    init_db(db)

    save_result(
        db,
        {
            "target_id": "legacy",
            "check_type": "dns",
            "checked_at": "2026-11-01T12:00:00+00:00",
            "success": True,
            "latency_ms": 10.0,
            "status_code": None,
            "error_type": None,
            "message": "OK",
        },
    )

    assert bootstrap_registry(db, config_file) == 2

    monitors = list_registry_monitors(db)
    assert [m["id"] for m in monitors] == ["demo_dns", "demo_tcp"]

    assert len(get_history(db, "legacy")) == 1


def test_import_is_idempotent(tmp_path, config_file):
    db = tmp_path / "health.db"

    assert bootstrap_registry(db, config_file) == 2

    config_file.write_text(CONFIG.replace("demo_dns", "changed_dns"))

    assert bootstrap_registry(db, config_file) == 0

    monitors = list_registry_monitors(db)
    assert monitors[0]["id"] == "demo_dns"


def test_disabled_targets_are_excluded(tmp_path, config_file):
    db = tmp_path / "health.db"
    bootstrap_registry(db, config_file)

    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            UPDATE monitor_registry
            SET enabled = 0
            WHERE target_id = 'demo_dns'
            """
        )

    monitors = list_registry_monitors(db)
    assert monitors[0]["enabled"] is False

    config = read_registry_config(db)
    assert len(config["targets"]) == 1
    assert config["targets"][0]["id"] == "demo_tcp"
    assert config["settings"]["failure_threshold"] == 3


def test_invalid_yaml_does_not_initialize(tmp_path):
    db = tmp_path / "health.db"
    config_file = tmp_path / "invalid.yaml"
    config_file.write_text("settings: {}\ntargets: []\n")

    with pytest.raises(ConfigError):
        bootstrap_registry(db, config_file)

    assert not db.exists()


def test_uninitialized_registry_is_rejected(tmp_path):
    db = tmp_path / "health.db"
    init_db(db)

    with pytest.raises(RegistryNotInitialized):
        read_registry_config(db)
