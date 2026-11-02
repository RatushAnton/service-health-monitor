"""SQLite-backed monitoring target registry."""

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from healthmon.config import ConfigError, load_config
from healthmon.storage import init_db


class RegistryNotInitialized(RuntimeError):
    """Raised when monitor settings have not been imported."""

class MonitorNotFound(LookupError):
    """Monitor does not exist or has been archived."""

class MonitorConflict(ValueError):
    """Monitor ID or immutable identity conflicts."""


def bootstrap_registry(
    db_path: str | Path,
    config_path: str | Path,
) -> int:
    """Import YAML configuration exactly once.

    Returns the number of inserted targets. Existing registry
    configuration is never overwritten by subsequent imports.
    """
    config = load_config(config_path)
    init_db(db_path)

    with closing(sqlite3.connect(db_path, timeout=10)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")

        initialized = connection.execute(
            """
            SELECT 1
            FROM registry_settings
            WHERE singleton = 1
            """
        ).fetchone()

        if initialized:
            return 0

        existing_count = connection.execute(
            "SELECT COUNT(*) FROM monitor_registry"
        ).fetchone()[0]

        if existing_count:
            raise sqlite3.DatabaseError("Registry contains targets but has no settings")

        connection.execute(
            """
            INSERT INTO registry_settings (
                singleton, settings_json
            )
            VALUES (1, ?)
            """,
            (json.dumps(config["settings"]),),
        )

        created_at = datetime.now(UTC).isoformat()

        connection.executemany(
            """
            INSERT INTO monitor_registry (
                target_id, target_json, enabled, created_at
            )
            VALUES (?, ?, 1, ?)
            """,
            [
                (
                    target["id"],
                    json.dumps(target),
                    created_at,
                )
                for target in config["targets"]
            ],
        )

    return len(config["targets"])


def list_registry_monitors(db_path: str | Path) -> list[dict]:
    """Return all configured monitors, including disabled ones."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute(
            """
            SELECT target_json, enabled
            FROM monitor_registry
            WHERE archived_at IS NULL
            ORDER BY target_id
            """
        ).fetchall()

    return [
        {
            **json.loads(row["target_json"]),
            "enabled": bool(row["enabled"]),
        }
        for row in rows
    ]


def read_registry_config(db_path: str | Path) -> dict:
    """Build the worker's configuration from enabled monitors."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row

        settings = connection.execute(
            """
            SELECT settings_json
            FROM registry_settings
            WHERE singleton = 1
            """
        ).fetchone()

        if settings is None:
            raise RegistryNotInitialized("Monitor registry has not been initialized")

        rows = connection.execute(
            """
            SELECT target_json
            FROM monitor_registry
            WHERE enabled = 1 AND archived_at IS NULL
            ORDER BY target_id
            """
        ).fetchall()

    return {
        "settings": json.loads(settings["settings_json"]),
        "targets": [json.loads(row["target_json"]) for row in rows],
    }


def main(argv: list[str] | None = None) -> int:
    """Import monitor configuration from the command line."""
    parser = argparse.ArgumentParser(
        description="Initialize the SQLite monitor registry"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--db", required=True)

    args = parser.parse_args(argv)

    try:
        count = bootstrap_registry(args.db, args.config)
    except (ConfigError, sqlite3.Error, OSError) as exc:
        print(f"Registry initialization failed: {exc}")
        return 2

    print(f"Registry initialized: {count} target(s) imported")
    return 0


def require_initialized(connection: sqlite3.Connection) -> None:
    """Reject writes before the registry has been imported."""
    initialized = connection.execute(
        "SELECT 1 FROM registry_settings WHERE singleton = 1"
    ).fetchone()

    if initialized is None:
        raise RegistryNotInitialized(
            "Monitor registry has not been initialized"
        )


def create_monitor(db_path: str | Path, target: dict) -> dict:
    """Insert a new monitor without reusing historical IDs."""
    with closing(sqlite3.connect(db_path, timeout=10)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        require_initialized(conn)

        existing = conn.execute(
            "SELECT 1 FROM monitor_registry WHERE target_id = ?",
            (target["id"],),
        ).fetchone()

        if existing:
            raise MonitorConflict("Monitor ID already exists")

        conn.execute(
            """
            INSERT INTO monitor_registry
                (target_id, target_json, enabled, created_at)
            VALUES (?, ?, 1, ?)
            """,
            (
                target["id"],
                json.dumps(target),
                datetime.now(UTC).isoformat(),
            ),
        )

    return {**target, "enabled": True}


def update_monitor(
    db_path: str | Path,
    target_id: str,
    target: dict,
) -> dict:
    """Update an active monitor while preserving its identity."""
    with closing(sqlite3.connect(db_path, timeout=10)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        require_initialized(conn)

        row = conn.execute(
            """
            SELECT target_json, enabled
            FROM monitor_registry
            WHERE target_id = ? AND archived_at IS NULL
            """,
            (target_id,),
        ).fetchone()

        if row is None:
            raise MonitorNotFound(target_id)

        previous = json.loads(row[0])

        if target["id"] != target_id:
            raise MonitorConflict("Monitor ID cannot be changed")

        if target["type"] != previous["type"]:
            raise MonitorConflict("Monitor type cannot be changed")

        if previous != target:
            conn.execute(
                """
                UPDATE monitor_registry
                SET target_json = ?
                WHERE target_id = ? AND archived_at IS NULL
                """,
                (json.dumps(target), target_id),
            )

            # Start incident evaluation afresh after configuration edits.
            conn.execute(
                "DELETE FROM target_state WHERE target_id = ?",
                (target_id,),
            )

        enabled = bool(row[1])

    return {**target, "enabled": enabled}


def set_monitor_enabled(
    db_path: str | Path,
    target_id: str,
    enabled: bool,
) -> dict:
    """Enable or pause a monitor without deleting its history."""
    with closing(sqlite3.connect(db_path, timeout=10)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        require_initialized(conn)

        row = conn.execute(
            """
            SELECT target_json, enabled
            FROM monitor_registry
            WHERE target_id = ? AND archived_at IS NULL
            """,
            (target_id,),
        ).fetchone()

        if row is None:
            raise MonitorNotFound(target_id)

        if bool(row[1]) != enabled:
            conn.execute(
                """
                UPDATE monitor_registry
                SET enabled = ?
                WHERE target_id = ? AND archived_at IS NULL
                """,
                (int(enabled), target_id),
            )

            conn.execute(
                "DELETE FROM target_state WHERE target_id = ?",
                (target_id,),
            )

        target = json.loads(row[0])

    return {**target, "enabled": enabled}


def archive_monitor(db_path: str | Path, target_id: str) -> None:
    """Archive a monitor but retain checks and incident history."""
    with closing(sqlite3.connect(db_path, timeout=10)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        require_initialized(conn)

        cursor = conn.execute(
            """
            UPDATE monitor_registry
            SET enabled = 0, archived_at = ?
            WHERE target_id = ? AND archived_at IS NULL
            """,
            (datetime.now(UTC).isoformat(), target_id),
        )

        if cursor.rowcount != 1:
            raise MonitorNotFound(target_id)

        conn.execute(
            "DELETE FROM target_state WHERE target_id = ?",
            (target_id,),
        )


if __name__ == "__main__":
    raise SystemExit(main())
