"""Automated tests for the healthmon command-line interface."""

import sqlite3
import sys
from pathlib import Path

import httpx
import pytest

from healthmon import cli
from healthmon.storage import (
    init_db,
    record_check_with_incident,
    save_result,
)


def make_result(success: bool, minute: int) -> dict:
    """Build a fake HTTP probe result."""
    return {
        "target_id": "local_demo",
        "check_type": "http",
        "checked_at": f"2026-11-01T12:{minute:02d}:00+00:00",
        "success": success,
        "latency_ms": 10.0,
        "status_code": 200 if success else None,
        "error_type": None if success else "HTTP_CONNECT_ERROR",
        "message": "HTTP 200 OK" if success else "Connection refused",
    }


def invoke(monkeypatch, *arguments) -> int:
    """Run the CLI as though arguments came from the terminal."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["healthmon", *[str(arg) for arg in arguments]],
    )

    return cli.main()


def seed_down_event(db: Path) -> None:
    """Create a confirmed DOWN incident without network access."""
    init_db(db)

    for minute in (1, 2, 3):
        record_check_with_incident(
            db,
            make_result(False, minute),
            failure_threshold=3,
            recovery_threshold=1,
        )


def test_check_command_detects_outage_and_recovery(
    tmp_path,
    monkeypatch,
    capsys,
):
    """CLI probes persist incidents across multiple executions."""

    config = tmp_path / "targets.yaml"
    db = tmp_path / "health.db"

    config.write_text(
        """
settings:
  timeout_seconds: 3
  failure_threshold: 3
  recovery_threshold: 1

targets:
  - id: local_demo
    name: Local Demo
    type: http
    url: http://127.0.0.1:18765/
    expected_status: 200
""",
        encoding="utf-8",
    )

    counter = {"number": 0}

    def fake_http_check(target, timeout):
        assert timeout == 3
        counter["number"] += 1
        number = counter["number"]
        return make_result(number == 4, number)

    monkeypatch.setitem(
        cli.CHECKERS,
        "http",
        fake_http_check,
    )

    outputs = []

    for expected_code in (1, 1, 1, 0):
        code = invoke(
            monkeypatch,
            "check",
            "--config",
            config,
            "--db",
            db,
        )

        assert code == expected_code
        outputs.append(capsys.readouterr().out)

    assert "Incident pending: 1/3" in outputs[0]
    assert "Incident pending: 2/3" in outputs[1]
    assert "Incident event: DOWN" in outputs[2]
    assert "Incident event: RECOVERED" in outputs[3]

    with sqlite3.connect(db) as conn:
        checks = conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]

        events = conn.execute(
            """
            SELECT event_type
            FROM incident_events
            ORDER BY id
            """
        ).fetchall()

    assert checks == 4
    assert events == [("DOWN",), ("RECOVERED",)]


def test_notify_success_marks_event_delivered(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Successful notification is recorded and not resent."""

    db = tmp_path / "health.db"
    seed_down_event(db)

    sent_events = []

    def fake_send(webhook_url, event):
        sent_events.append(event["event_type"])

    monkeypatch.setattr(cli, "send_webhook", fake_send)

    arguments = (
        "notify",
        "--db",
        db,
        "--webhook-url",
        "https://example.test/notify",
    )

    assert invoke(monkeypatch, *arguments) == 0
    output = capsys.readouterr().out

    assert "Delivered event 1" in output
    assert sent_events == ["DOWN"]

    # No notification should be delivered twice.
    assert invoke(monkeypatch, *arguments) == 0
    output = capsys.readouterr().out

    assert "No incident notifications ready" in output
    assert sent_events == ["DOWN"]

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """
            SELECT delivery_attempts, delivered_at, lease_token
            FROM incident_events
            WHERE id = 1
            """
        ).fetchone()

    assert row[0] == 1
    assert row[1] is not None
    assert row[2] is None


def test_notify_failure_schedules_retry(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Webhook failures retain the event and schedule a retry."""

    db = tmp_path / "health.db"
    seed_down_event(db)

    def fake_failure(webhook_url, event):
        raise httpx.ConnectError(
            "Connection refused",
            request=httpx.Request("POST", webhook_url),
        )

    monkeypatch.setattr(
        cli,
        "send_webhook",
        fake_failure,
    )

    arguments = (
        "notify",
        "--db",
        db,
        "--webhook-url",
        "https://example.test/notify",
    )

    assert invoke(monkeypatch, *arguments) == 4
    output = capsys.readouterr().out

    assert "Webhook delivery failed" in output
    assert "Next retry in: 60 seconds" in output

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """
            SELECT delivery_attempts,
                   delivered_at,
                   last_delivery_error,
                   next_attempt_at,
                   lease_token
            FROM incident_events
            WHERE id = 1
            """
        ).fetchone()

    assert row[0] == 1
    assert row[1] is None
    assert "Connection refused" in row[2]
    assert row[3] is not None
    assert row[4] is None

    # Immediate rerun must not bypass retry scheduling.
    assert invoke(monkeypatch, *arguments) == 0
    assert "No incident notifications ready" in (capsys.readouterr().out)


@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    [
        ("invalid_url", 2),
        ("invalid_limit", 2),
        ("missing_database", 3),
    ],
)
def test_notify_argument_validation(
    tmp_path,
    monkeypatch,
    scenario,
    expected_code,
):
    """Invalid CLI inputs return documented exit codes."""

    arguments = [
        "notify",
        "--db",
        str(tmp_path / "missing.db"),
        "--webhook-url",
        "https://example.test/notify",
    ]

    if scenario == "invalid_url":
        arguments[-1] = "ftp://example.test/notify"

    elif scenario == "invalid_limit":
        arguments.extend(["--limit", "0"])

    assert invoke(monkeypatch, *arguments) == expected_code


def test_summary_and_history_commands(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Reporting commands show previously recorded check results."""

    db = tmp_path / "health.db"
    init_db(db)

    save_result(db, make_result(True, 1))
    save_result(db, make_result(False, 2))

    assert (
        invoke(
            monkeypatch,
            "summary",
            "--db",
            db,
        )
        == 0
    )

    summary = capsys.readouterr().out

    assert "Last Recorded Service Health" in summary
    assert "local_demo" in summary
    assert "DOWN" in summary

    assert (
        invoke(
            monkeypatch,
            "history",
            "--db",
            db,
            "--target",
            "local_demo",
            "--limit",
            "10",
        )
        == 0
    )

    history = capsys.readouterr().out

    assert "Check history: local_demo" in history
    assert "HTTP_CONNECT_ERROR" in history
    assert "Showing 2 recorded checks" in history


def test_check_invalid_configuration(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Invalid monitoring YAML must return exit code 2."""

    config = tmp_path / "invalid.yaml"
    config.write_text(
        "settings: invalid\n",
        encoding="utf-8",
    )

    code = invoke(
        monkeypatch,
        "check",
        "--config",
        config,
        "--db",
        tmp_path / "health.db",
    )

    assert code == 2
    assert "Configuration error:" in capsys.readouterr().out


def test_notify_ntfy_success_marks_event_delivered(tmp_path, monkeypatch):
    db = tmp_path / "health.db"
    seed_down_event(db)

    destination = tmp_path / "ntfy-url"
    destination.write_text(
        "https://ntfy.sh/test_private_topic\n",
        encoding="utf-8",
    )

    sent = []

    def fake_send(url, event):
        sent.append((url, event["event_type"]))

    monkeypatch.setattr(cli, "send_ntfy", fake_send)

    args = (
        "notify",
        "--db",
        db,
        "--ntfy-url-file",
        destination,
    )

    assert invoke(monkeypatch, *args) == 0
    assert sent == [("https://ntfy.sh/test_private_topic", "DOWN")]

    # Delivered events must not be sent again.
    assert invoke(monkeypatch, *args) == 0
    assert len(sent) == 1

    with sqlite3.connect(db) as conn:
        delivered = conn.execute(
            "SELECT delivered_at FROM incident_events WHERE id = 1"
        ).fetchone()[0]

    assert delivered is not None


def test_notify_ntfy_failure_schedules_retry(tmp_path, monkeypatch):
    from healthmon.ntfy import NtfyDeliveryError

    db = tmp_path / "health.db"
    seed_down_event(db)

    destination = tmp_path / "ntfy-url"
    destination.write_text(
        "https://ntfy.sh/test_private_topic\n",
        encoding="utf-8",
    )

    def fake_failure(url, event):
        raise NtfyDeliveryError("ntfy returned HTTP 503")

    monkeypatch.setattr(cli, "send_ntfy", fake_failure)

    args = (
        "notify",
        "--db",
        db,
        "--ntfy-url-file",
        destination,
    )

    assert invoke(monkeypatch, *args) == 4

    with sqlite3.connect(db) as conn:
        attempts, delivered, retry_at = conn.execute(
            """
            SELECT delivery_attempts, delivered_at, next_attempt_at
            FROM incident_events WHERE id = 1
            """
        ).fetchone()

    assert attempts == 1
    assert delivered is None
    assert retry_at is not None


def test_notify_ntfy_rejects_insecure_topic(tmp_path, monkeypatch):
    destination = tmp_path / "ntfy-url"
    destination.write_text(
        "http://ntfy.sh/insecure_topic\n",
        encoding="utf-8",
    )

    assert (
        invoke(
            monkeypatch,
            "notify",
            "--db",
            tmp_path / "missing.db",
            "--ntfy-url-file",
            destination,
        )
        == 2
    )
