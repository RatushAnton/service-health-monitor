import sqlite3
from contextlib import closing
from pathlib import Path

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
    """Create the database and tables if needed."""

    path = Path(db_path)

    # Create the parent directory if necessary
    path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(CREATE_TABLE_SQL)
        connection.execute(CREATE_INDEX_SQL)


def save_result(db_path: str | Path, result: dict) -> None:
    """Insert a single health-check result."""

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
    """Return the most recent recorded check for each target."""

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
    """Return recent check history for one target."""

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
