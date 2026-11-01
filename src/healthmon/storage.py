"""SQLite storage and retrieval of health-check results."""

import sqlite3
from contextlib import closing
from pathlib import Path

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
    delivery_attempts INTEGER NOT NULL DEFAULT 0
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


def get_pending_events(
    db_path: str | Path,
    limit: int = 20,
) -> list[sqlite3.Row]:
    """Fetch undelivered incident events, oldest first."""

    path = Path(db_path)

    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")

    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row

        return connection.execute(
            """
            SELECT id, target_id, event_type,
                    occurred_at, incident_started_at,
                    error_type, message, delivery_attempts
            FROM incident_events
            WHERE delivered_at IS NULL
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def mark_delivery_attempt(
    db_path: str | Path,
    event_id: int,
) -> None:
    """Increment the delivery-attempt counter."""

    with closing(sqlite3.connect(db_path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE incident_events
            SET delivery_attempts = delivery_attempts + 1
            WHERE id = ? AND delivered_at IS NULL
            """,
            (event_id,),
        )

        if cursor.rowcount != 1:
            raise sqlite3.DatabaseError(f"Event {event_id} is not pending")


def mark_event_delivered(
    db_path: str | Path,
    event_id: int,
) -> None:
    """Record successful webhook delivery."""

    from datetime import UTC, datetime

    delivered_at = datetime.now(UTC).isoformat()

    with closing(sqlite3.connect(db_path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE incident_events
            SET delivered_at = ?
            WHERE id = ? AND delivered_at IS NULL
            """,
            (delivered_at, event_id),
        )

        if cursor.rowcount != 1:
            raise sqlite3.DatabaseError(f"Event {event_id} is not pending")
