"""Integration tests for SQLite persistence and event delivery state."""

import sqlite3
from pathlib import Path

import pytest

from healthmon import storage as storage_module
from healthmon.diagnosis import StateUpdate, TargetState
from healthmon.storage import (
    claim_pending_event,
    get_history,
    get_latest_results,
    get_target_state,
    init_db,
    mark_delivery_failed,
    mark_event_delivered,
    record_check_with_incident,
    save_result,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Provide an isolated SQLite database for each test."""
    path = tmp_path / "health.db"
    init_db(path)
    return path


def make_result(
    success: bool,
    minute: int,
    target_id: str = "local_demo",
) -> dict:
    """Build a synthetic health-check result."""
    return {
        "target_id": target_id,
        "check_type": "http",
        "checked_at": f"2026-11-01T12:{minute:02d}:00+00:00",
        "success": success,
        "latency_ms": 10.0,
        "status_code": 200 if success else None,
        "error_type": None if success else "HTTP_CONNECT_ERROR",
        "message": "HTTP 200 OK" if success else "Connection refused",
    }


def create_incident_cycle(db: Path) -> None:
    """Create one DOWN and one RECOVERED event."""
    for minute, success in enumerate([False, False, False, True], start=1):
        record_check_with_incident(
            db,
            make_result(success, minute),
            failure_threshold=3,
            recovery_threshold=1,
        )


def test_database_initialization(db: Path):
    """All required tables exist and initialization is repeatable."""
    init_db(db)

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {"checks", "target_state", "incident_events"} <= tables

    state = get_target_state(db, "unknown_target")
    assert state == TargetState(target_id="unknown_target")
    assert get_latest_results(db) == []


def test_incident_lifecycle_is_persisted(db: Path):
    """Checks, incident state and transitions survive SQLite writes."""
    results = [False, False, False, False, True, True]
    events = []

    for minute, success in enumerate(results, start=1):
        update = record_check_with_incident(
            db,
            make_result(success, minute),
            failure_threshold=3,
            recovery_threshold=1,
        )
        events.append(update.event)

    assert events == [None, None, "DOWN", None, "RECOVERED", None]

    with sqlite3.connect(db) as conn:
        check_count = conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]

        saved_events = conn.execute(
            """
            SELECT event_type, incident_started_at, delivered_at
            FROM incident_events
            ORDER BY id
            """
        ).fetchall()

    assert check_count == 6
    assert [row[0] for row in saved_events] == ["DOWN", "RECOVERED"]

    first_failure = "2026-11-01T12:01:00+00:00"
    assert all(row[1] == first_failure for row in saved_events)
    assert all(row[2] is None for row in saved_events)

    state = get_target_state(db, "local_demo")
    assert state.incident_open is False
    assert state.consecutive_failures == 0
    assert state.consecutive_successes == 2


def test_latest_results_and_history(db: Path):
    """Historical queries return the right target and newest result."""
    save_result(db, make_result(True, 1, "web"))
    save_result(db, make_result(False, 2, "dns"))
    save_result(db, make_result(False, 3, "web"))

    latest = get_latest_results(db)
    assert [row["target_id"] for row in latest] == ["dns", "web"]

    web_latest = next(row for row in latest if row["target_id"] == "web")
    assert web_latest["success"] == 0
    assert web_latest["checked_at"] == ("2026-11-01T12:03:00+00:00")

    history = get_history(db, "web", limit=1)
    assert len(history) == 1
    assert history[0]["success"] == 0
    assert get_history(db, "missing") == []


def test_failed_transaction_rolls_back_everything(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A failed event insert must also roll back the check and state."""

    def invalid_transition(state, result, failure_threshold, recovery_threshold):
        return StateUpdate(
            state=TargetState(target_id=result["target_id"]),
            event="INVALID_EVENT",
        )

    monkeypatch.setattr(
        storage_module,
        "evaluate_check",
        invalid_transition,
    )

    with pytest.raises(sqlite3.IntegrityError):
        record_check_with_incident(
            db,
            make_result(False, 1),
            failure_threshold=3,
            recovery_threshold=1,
        )

    with sqlite3.connect(db) as conn:
        for table in ("checks", "target_state", "incident_events"):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0


def test_existing_database_schema_migration(tmp_path: Path):
    """Old incident-event rows survive retry and lease migrations."""
    path = tmp_path / "legacy.db"

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                incident_started_at TEXT,
                error_type TEXT,
                message TEXT NOT NULL,
                delivered_at TEXT,
                delivery_attempts INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO incident_events (
                target_id, event_type, occurred_at, message
            )
            VALUES (?, ?, ?, ?)
            """,
            ("local_demo", "DOWN", "2026-11-01T12:00:00+00:00", "Down"),
        )

    init_db(path)
    init_db(path)

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(incident_events)")}
        count = conn.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0]

    assert {
        "last_delivery_error",
        "next_attempt_at",
        "lease_token",
        "lease_expires_at",
    } <= columns
    assert count == 1


def test_event_claims_are_exclusive(db: Path):
    """Two events can be claimed only once while leases are active."""
    create_incident_cycle(db)

    first = claim_pending_event(db)
    second = claim_pending_event(db)
    third = claim_pending_event(db)

    assert first is not None
    assert second is not None
    assert {first["id"], second["id"]} == {1, 2}
    assert first["lease_token"] != second["lease_token"]
    assert third is None

    with sqlite3.connect(db) as conn:
        attempts = conn.execute(
            """
            SELECT delivery_attempts
            FROM incident_events
            ORDER BY id
            """
        ).fetchall()

    assert attempts == [(1,), (1,)]


def test_expired_lease_rejects_stale_owner(db: Path):
    """An expired lease can be reclaimed without trusting the old owner."""
    create_incident_cycle(db)

    original = claim_pending_event(db)
    assert original is not None
    assert original["id"] == 1

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            UPDATE incident_events
            SET lease_expires_at = ?
            WHERE id = 1
            """,
            ("2000-01-01T00:00:00+00:00",),
        )

    reclaimed = claim_pending_event(db)
    assert reclaimed is not None
    assert reclaimed["id"] == 1
    assert reclaimed["lease_token"] != original["lease_token"]

    with pytest.raises(sqlite3.DatabaseError):
        mark_event_delivered(db, 1, original["lease_token"])

    mark_event_delivered(db, 1, reclaimed["lease_token"])

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """
            SELECT delivered_at, lease_token
            FROM incident_events
            WHERE id = 1
            """
        ).fetchone()

    assert row[0] is not None
    assert row[1] is None


def test_failed_delivery_can_be_retried(db: Path):
    """Failure backoff persists and successful retry clears metadata."""
    create_incident_cycle(db)

    first = claim_pending_event(db)
    assert first is not None

    mark_delivery_failed(
        db,
        first["id"],
        first["lease_token"],
        "Connection refused",
        delay_seconds=60,
    )

    # Event 1 is delayed, so event 2 is claimed next.
    second = claim_pending_event(db)
    assert second is not None
    assert second["id"] == 2

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """
            SELECT last_delivery_error, next_attempt_at, lease_token
            FROM incident_events
            WHERE id = 1
            """
        ).fetchone()

    assert row[0] == "Connection refused"
    assert row[1] is not None
    assert row[2] is None

    # Make event 1 eligible again without waiting in a test.
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            UPDATE incident_events
            SET next_attempt_at = NULL
            WHERE id = 1
            """
        )

    retry = claim_pending_event(db)
    assert retry is not None
    assert retry["id"] == 1
    assert retry["delivery_attempts"] == 2

    mark_event_delivered(db, 1, retry["lease_token"])

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """
            SELECT delivered_at, last_delivery_error,
                   next_attempt_at, lease_token
            FROM incident_events
            WHERE id = 1
            """
        ).fetchone()

    assert row[0] is not None
    assert row[1:] == (None, None, None)
