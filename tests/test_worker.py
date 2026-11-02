"""Tests for the background monitoring worker."""

import pytest

from healthmon import worker


def test_worker_runs_one_cycle(monkeypatch):
    """One-shot mode executes exactly one check cycle."""
    calls = []

    def fake_check(config, db):
        calls.append((config, db))
        return 0

    monkeypatch.setattr(worker, "run_checks", fake_check)

    result = worker.run_worker(
        "targets.yaml",
        "health.db",
        interval_seconds=300,
        once=True,
    )

    assert result == 0
    assert calls == [("targets.yaml", "health.db")]


def test_worker_preserves_failed_check_exit_code(monkeypatch):
    """A DOWN target is reported as exit code 1 in one-shot mode."""
    monkeypatch.setattr(
        worker,
        "run_checks",
        lambda config, db: 1,
    )

    assert (
        worker.run_worker(
            "targets.yaml",
            "health.db",
            300,
            once=True,
        )
        == 1
    )


def test_worker_runs_repeatedly_until_stopped(monkeypatch):
    """Worker repeats checks and responds to shutdown."""

    calls = []

    class FakeShutdown:
        def __init__(self):
            self.stopped = False
            self.waits = []

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            self.waits.append(seconds)
            if len(self.waits) == 2:
                self.stopped = True

    monkeypatch.setattr(
        worker,
        "run_checks",
        lambda config, db: calls.append((config, db)) or 0,
    )

    shutdown = FakeShutdown()

    result = worker.run_worker(
        "targets.yaml",
        "health.db",
        interval_seconds=5,
        stop_event=shutdown,
    )

    assert result == 0
    assert len(calls) == 2
    assert shutdown.waits == [5, 5]


def test_invalid_worker_interval():
    """Nonpositive intervals are rejected."""

    with pytest.raises(ValueError, match="Interval must be positive"):
        worker.run_worker("targets.yaml", "health.db", 0)


def test_worker_cli_one_shot(monkeypatch):
    """The worker CLI accepts its documented options."""

    monkeypatch.setattr(
        worker,
        "run_checks",
        lambda config, db: 0,
    )

    assert (
        worker.main(
            [
                "--config",
                "targets.yaml",
                "--db",
                "health.db",
                "--interval",
                "60",
                "--once",
            ]
        )
        == 0
    )
