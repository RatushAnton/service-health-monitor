"""Unit tests for HTTP webhook notifications."""

import httpx
import pytest

from healthmon import alerting


def make_event() -> dict:
    """Build a sample incident notification."""
    return {
        "id": 42,
        "target_id": "local_demo",
        "event_type": "DOWN",
        "occurred_at": "2026-11-01T12:03:00+00:00",
        "incident_started_at": "2026-11-01T12:01:00+00:00",
        "error_type": "HTTP_CONNECT_ERROR",
        "message": "Connection refused",
    }


def test_webhook_sends_correct_json(monkeypatch):
    """Webhook requests contain the expected event information."""

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)

        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(alerting.httpx, "post", fake_post)

    alerting.send_webhook(
        "https://example.test/notify",
        make_event(),
        timeout_seconds=2.5,
    )

    assert captured["url"] == "https://example.test/notify"
    assert captured["json"] == {
        "event_id": 42,
        "target_id": "local_demo",
        "event_type": "DOWN",
        "occurred_at": "2026-11-01T12:03:00+00:00",
        "incident_started_at": "2026-11-01T12:01:00+00:00",
        "error_type": "HTTP_CONNECT_ERROR",
        "message": "Connection refused",
    }

    assert captured["headers"]["X-Healthmon-Event-ID"] == "42"
    assert captured["timeout"] == 2.5
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False


def test_webhook_rejects_http_errors(monkeypatch):
    """An HTTP 503 response must raise an HTTP error."""

    def fake_post(url, **kwargs):
        return httpx.Response(
            503,
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(alerting.httpx, "post", fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        alerting.send_webhook(
            "https://example.test/notify",
            make_event(),
        )
