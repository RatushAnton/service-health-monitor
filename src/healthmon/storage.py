"""SQLite storage and retrieval of health-check results."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from healthmon.diagnosis import StateUpdate, TargetState, evaluate_check

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT NOT NULL,
    check_type TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    success INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    status_code INTEGER,
    error_type TEXT,
    message TEXT NOT NULL
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_checks_target_time
ON checks (target_id, checked_at);
"""

CREATE_TARGET_STATE_SQL = """
CREATE TABLE IF NOT EXISTS target_state (
    target_id TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_successes INTEGER NOT NULL DEFAULT 0,
    incident_open INTEGER NOT NULL DEFAULT 0,
    first_failure_at TEXT,
    incident_started_at TEXT
);
"""

CREATE_INCIDENT_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS incident_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('DOWN', 'RECOVERED')),
    occurred_at TEXT NOT NULL,
    incident_started_at TEXT,
    error_type TEXT,
    message TEXT NOT NULL,
    delivered_at TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_delivery_error TEXT,
    next_attempt_at TEXT,
    lease_token TEXT,
    lease_expires_at TEXT
);
"""

INSERT_RESULT_SQL = """
INSERT INTO checks (
    target_id,
    check_type,
    checked_at,
    success,
    latency_ms,
    status_code,
    error_type,
    message
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);
"""


def init_db(db_path: str | Path) -> None:
    """Initialize SQLite tables and indexes if missing."""

    path = Path(db_path)

    # Create the parent directory if necessary
    path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(CREATE_TABLE_SQL)
        connection.execute(CREATE_INDEX_SQL)
        connection.execute(CREATE_TARGET_STATE_SQL)
        connection.execute(CREATE_INCIDENT_EVENTS_SQL)

        # Upgrade databases created before retry scheduling existed.
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(incident_events)")
        }

        if "last_delivery_error" not in columns:
            connection.execute(
                "ALTER TABLE incident_events ADD COLUMN last_delivery_error TEXT"
            )

        if "next_attempt_at" not in columns:
            connection.execute(
                "ALTER TABLE incident_events ADD COLUMN next_attempt_at TEXT"
            )

        if "lease_token" not in columns:
            connection.execute(
                "ALTER TABLE incident_events ADD COLUMN lease_token TEXT"
            )

        if "lease_expires_at" not in columns:
            connection.execute(
                "ALTER TABLE incident_events ADD COLUMN lease_expires_at TEXT"
            )


def save_result(db_path: str | Path, result: dict) -> None:
    """Save one health-check result to SQLite."""

    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(
            INSERT_RESULT_SQL,
            (
                result["target_id"],
                result["check_type"],
                result["checked_at"],
                int(result["success"]),
                result["latency_ms"],
                result.get("status_code"),
                result.get("error_type"),
                result["message"],
            ),
        )


def get_latest_results(db_path: str | Path) -> list[sqlite3.Row]:
    """Fetch the latest saved check for each target."""

    path = Path(db_path)

    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")

    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute(
            """
            SELECT
                id,
                target_id,
                check_type,
                checked_at,
                success,
                latency_ms,
                error_type,
                message
            FROM checks
            WHERE id IN (
                SELECT MAX(id)
                FROM checks
                GROUP BY target_id
            )
            ORDER BY target_id
            """
        ).fetchall()

    return rows


def get_history(
    db_path: str | Path,
    target_id: str,
    limit: int = 20,
) -> list[sqlite3.Row]:
    """Fetch recent saved checks for one target."""

    path = Path(db_path)

    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")

    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute(
            """
            SELECT
                id,
                target_id,
                checked_at,
                success,
                latency_ms,
                error_type,
                message
            FROM checks
            WHERE target_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (target_id, limit),
        ).fetchall()

    return rows


def get_target_state(
    db_path: str | Path,
    target_id: str,
) -> TargetState:
    """Load saved state or return a new target state."""

    path = Path(db_path)

    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")

    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row

        row = connection.execute(
            """
            SELECT *
            FROM target_state
            WHERE target_id = ?
            """,
            (target_id,),
        ).fetchone()

    if row is None:
        return TargetState(target_id=target_id)

    return TargetState(
        target_id=row["target_id"],
        consecutive_failures=row["consecutive_failures"],
        consecutive_successes=row["consecutive_successes"],
        incident_open=bool(row["incident_open"]),
        first_failure_at=row["first_failure_at"],
        incident_started_at=row["incident_started_at"],
    )


def save_target_state(
    db_path: str | Path,
    state: TargetState,
) -> None:
    """Insert or update the saved state of one target."""

    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO target_state (
                target_id,
                consecutive_failures,
                consecutive_successes,
                incident_open,
                first_failure_at,
                incident_started_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                consecutive_failures=excluded.consecutive_failures,
                consecutive_successes=excluded.consecutive_successes,
                incident_open=excluded.incident_open,
                first_failure_at=excluded.first_failure_at,
                incident_started_at=excluded.incident_started_at
            """,
            (
                state.target_id,
                state.consecutive_failures,
                state.consecutive_successes,
                int(state.incident_open),
                state.first_failure_at,
                state.incident_started_at,
            ),
        )


def record_check_with_incident(
    db_path: str | Path,
    result: dict,
    failure_threshold: int,
    recovery_threshold: int,
) -> StateUpdate:
    """Save a check and its incident state in one transaction."""

    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row

        with connection:
            # Lock writes before loading the current state.
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
                "SELECT * FROM target_state WHERE target_id = ?",
                (result["target_id"],),
            ).fetchone()

            if row is None:
                state = TargetState(target_id=result["target_id"])
            else:
                state = TargetState(
                    target_id=row["target_id"],
                    consecutive_failures=row["consecutive_failures"],
                    consecutive_successes=row["consecutive_successes"],
                    incident_open=bool(row["incident_open"]),
                    first_failure_at=row["first_failure_at"],
                    incident_started_at=row["incident_started_at"],
                )

            # Calculate the incident transition.
            update = evaluate_check(
                state,
                result,
                failure_threshold,
                recovery_threshold,
            )

            # Save the raw probe result.
            connection.execute(
                INSERT_RESULT_SQL,
                (
                    result["target_id"],
                    result["check_type"],
                    result["checked_at"],
                    int(result["success"]),
                    result["latency_ms"],
                    result.get("status_code"),
                    result.get("error_type"),
                    result["message"],
                ),
            )

            # Save the updated target state.
            new_state = update.state

            connection.execute(
                """
                INSERT INTO target_state (
                    target_id,
                    consecutive_failures,
                    consecutive_successes,
                    incident_open,
                    first_failure_at,
                    incident_started_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_id) DO UPDATE SET
                    consecutive_failures=excluded.consecutive_failures,
                    consecutive_successes=excluded.consecutive_successes,
                    incident_open=excluded.incident_open,
                    first_failure_at=excluded.first_failure_at,
                    incident_started_at=excluded.incident_started_at
                """,
                (
                    new_state.target_id,
                    new_state.consecutive_failures,
                    new_state.consecutive_successes,
                    int(new_state.incident_open),
                    new_state.first_failure_at,
                    new_state.incident_started_at,
                ),
            )

            # Store only actual incident transitions.
            if update.event is not None:
                incident_started_at = (
                    new_state.incident_started_at
                    if update.event == "DOWN"
                    else state.incident_started_at
                )

                connection.execute(
                    """
                    INSERT INTO incident_events (
                        target_id,
                        event_type,
                        occurred_at,
                        incident_started_at,
                        error_type,
                        message
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result["target_id"],
                        update.event,
                        result["checked_at"],
                        incident_started_at,
                        result.get("error_type"),
                        result["message"],
                    ),
                )

    return update


def claim_pending_event(
    db_path: str | Path,
    lease_seconds: int = 60,
) -> dict | None:
    """Atomically reserve one event for delivery."""

    if lease_seconds < 1:
        raise ValueError("Lease duration must be positive")

    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")

    with closing(sqlite3.connect(path, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row

        with connection:
            # Only one SQLite writer can claim at a time.
            connection.execute("BEGIN IMMEDIATE")
            now = datetime.now(UTC)

            row = connection.execute(
                """
                SELECT id, target_id, event_type,
                        occurred_at, incident_started_at,
                        error_type, message, delivery_attempts
                FROM incident_events
                WHERE delivered_at IS NULL
                    AND (
                        next_attempt_at IS NULL
                        OR next_attempt_at <= ?
                    )
                    AND (
                        lease_expires_at IS NULL
                        OR lease_expires_at <= ?
                    )
                ORDER BY id ASC
                LIMIT 1
                """,
                (now.isoformat(), now.isoformat()),
            ).fetchone()

            if row is None:
                return None

            token = uuid4().hex
            expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()

            cursor = connection.execute(
                """
                UPDATE incident_events
                SET lease_token = ?,
                    lease_expires_at = ?,
                    delivery_attempts = delivery_attempts + 1
                WHERE id = ? AND delivered_at IS NULL
                """,
                (token, expires_at, row["id"]),
            )

            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError("Could not claim incident event")

    event = dict(row)
    event["lease_token"] = token
    event["delivery_attempts"] += 1

    return event


def mark_event_delivered(
    db_path: str | Path,
    event_id: int,
    lease_token: str,
) -> None:
    """Complete delivery only for the current lease owner."""

    delivered_at = datetime.now(UTC).isoformat()

    with closing(sqlite3.connect(db_path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE incident_events
            SET delivered_at = ?,
                last_delivery_error = NULL,
                next_attempt_at = NULL,
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE id = ?
                AND lease_token = ?
                AND delivered_at IS NULL
            """,
            (delivered_at, event_id, lease_token),
        )

        if cursor.rowcount != 1:
            raise sqlite3.DatabaseError(f"Event {event_id}: lease no longer owned")


def mark_delivery_failed(
    db_path: str | Path,
    event_id: int,
    lease_token: str,
    error: str,
    delay_seconds: int,
) -> None:
    """Release a failed event and schedule its retry."""

    if delay_seconds < 1:
        raise ValueError("Retry delay must be positive")

    next_attempt_at = (datetime.now(UTC) + timedelta(seconds=delay_seconds)).isoformat()

    with closing(sqlite3.connect(db_path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE incident_events
            SET last_delivery_error = ?,
                next_attempt_at = ?,
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE id = ?
                AND lease_token = ?
                AND delivered_at IS NULL
            """,
            (error[:500], next_attempt_at, event_id, lease_token),
        )

        if cursor.rowcount != 1:
            raise sqlite3.DatabaseError(f"Event {event_id}: lease no longer owned")
