"""Tests for the background notification dispatcher."""

from pathlib import Path

import pytest
import yaml

from healthmon import notifier


def topic_file(tmp_path):
    path = tmp_path / "ntfy-url"
    path.write_text(
        "https://ntfy.sh/test_private_topic\n",
        encoding="utf-8",
    )
    return path


def test_one_shot_calls_existing_delivery_logic(tmp_path, monkeypatch):
    path = topic_file(tmp_path)
    calls = []

    def fake_send(db, webhook_url, limit, *, ntfy_url, quiet_when_empty):
        calls.append((db, webhook_url, limit, ntfy_url, quiet_when_empty))
        return 0

    monkeypatch.setattr(notifier, "send_notifications", fake_send)

    result = notifier.run_notifier(
        "health.db",
        path,
        once=True,
    )

    assert result == 0
    assert calls == [
        (
            "health.db",
            None,
            20,
            "https://ntfy.sh/test_private_topic",
            True,
        )
    ]


def test_one_shot_preserves_delivery_error(tmp_path, monkeypatch):
    path = topic_file(tmp_path)

    monkeypatch.setattr(
        notifier,
        "send_notifications",
        lambda *args, **kwargs: 4,
    )

    assert notifier.run_notifier("health.db", path, once=True) == 4


def test_dispatcher_retries_after_failed_cycle(tmp_path, monkeypatch):
    path = topic_file(tmp_path)
    results = iter([4, 0])
    calls = []

    def fake_send(*args, **kwargs):
        calls.append(True)
        return next(results)

    monkeypatch.setattr(notifier, "send_notifications", fake_send)

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

    shutdown = FakeShutdown()

    assert (
        notifier.run_notifier(
            "health.db",
            path,
            interval_seconds=60,
            stop_event=shutdown,
        )
        == 0
    )

    assert len(calls) == 2
    assert shutdown.waits == [60, 60]


def test_missing_topic_file_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Cannot read"):
        notifier.load_ntfy_url(tmp_path / "missing")


def test_invalid_topic_file_is_rejected(tmp_path):
    path = tmp_path / "ntfy-url"
    path.write_text(
        "http://ntfy.sh/insecure_topic",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid ntfy"):
        notifier.load_ntfy_url(path)


@pytest.mark.parametrize(
    ("interval", "limit"),
    [(0, 20), (60, 0)],
)
def test_invalid_runtime_settings(tmp_path, interval, limit):
    with pytest.raises(ValueError):
        notifier.run_notifier(
            "health.db",
            topic_file(tmp_path),
            interval_seconds=interval,
            limit=limit,
            once=True,
        )


def test_notifier_cli_one_shot(tmp_path, monkeypatch):
    path = topic_file(tmp_path)
    captured = {}

    def fake_run(db, secret, **kwargs):
        captured.update(db=db, secret=secret, **kwargs)
        return 0

    monkeypatch.setattr(notifier, "run_notifier", fake_run)

    assert (
        notifier.main(
            [
                "--db",
                "test.db",
                "--ntfy-url-file",
                str(path),
                "--interval",
                "15",
                "--limit",
                "7",
                "--once",
            ]
        )
        == 0
    )

    assert captured["db"] == "test.db"
    assert captured["secret"] == path
    assert captured["interval_seconds"] == 15
    assert captured["limit"] == 7
    assert captured["once"] is True


def test_configuration_error_hides_topic(tmp_path, capsys):
    path = tmp_path / "ntfy-url"
    path.write_text(
        "https://ntfy.sh/private_secret?invalid=1",
        encoding="utf-8",
    )

    assert (
        notifier.main(
            [
                "--ntfy-url-file",
                str(path),
                "--once",
            ]
        )
        == 2
    )

    output = capsys.readouterr().out
    assert "configuration error" in output
    assert "private_secret" not in output


def test_compose_notifier_is_opt_in():
    compose = yaml.safe_load(Path("compose.pi.ntfy.yaml").read_text(encoding="utf-8"))

    service = compose["services"]["notifier"]

    assert "notifications" in service["profiles"]
    assert service["image"] == "service-health-monitor:ntfy"
    assert service["read_only"] is True
    assert service["user"] == "10001:10001"
    assert "ports" not in service
    assert "build" not in service

    assert service["volumes"] == ["pi_data:/var/lib/service-health-monitor"]
    assert "healthmon_ntfy_url" in service["secrets"]

    assert (
        compose["secrets"]["healthmon_ntfy_url"]["file"] == "./deploy/pi/ntfy-url.txt"
    )
