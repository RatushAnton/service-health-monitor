"""SQLite storage and retrieval of health-check results."""

import sqlite3
from contextlib import closing
from pathlib import Path

from healthmon.diagnosis import TargetState

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
