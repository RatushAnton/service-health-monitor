"""Tests for the ntfy incident notification adapter."""

import httpx
import pytest

from healthmon import ntfy


def event(kind="DOWN"):
    return {
        "id": 42,
        "target_id": "home_gateway_dns",
        "event_type": kind,
        "occurred_at": "2026-11-03T09:00:00+00:00",
        "message": "DNS resolution failed",
    }


def test_ntfy_down_publishes_high_priority(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(ntfy.httpx, "post", fake_post)

    ntfy.send_ntfy("https://ntfy.sh/private_test_topic", event())

    assert captured["headers"]["X-Priority"] == "4"
    assert captured["headers"]["X-Title"].endswith("DOWN")
    assert b"home_gateway_dns" in captured["content"]
    assert b"Event ID: 42" in captured["content"]
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False


def test_ntfy_recovery_publishes_normal_priority(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(ntfy.httpx, "post", fake_post)

    ntfy.send_ntfy(
        "https://ntfy.sh/private_test_topic",
        event("RECOVERED"),
    )

    assert captured["headers"]["X-Priority"] == "3"
    assert captured["headers"]["X-Title"].endswith("RECOVERED")


def test_ntfy_http_error_does_not_expose_topic(monkeypatch):
    url = "https://ntfy.sh/private_test_topic"

    def fake_post(url, **kwargs):
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(ntfy.httpx, "post", fake_post)

    with pytest.raises(ntfy.NtfyDeliveryError) as exc:
        ntfy.send_ntfy(url, event())

    assert "HTTP 503" in str(exc.value)
    assert "private_test_topic" not in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://ntfy.sh/topic",
        "https://ntfy.sh/",
        "https://ntfy.sh/topic/extra",
        "https://user:password@ntfy.sh/topic",
        "https://ntfy.sh/topic?token=secret",
        "ftp://ntfy.sh/topic",
    ],
)
def test_ntfy_rejects_invalid_urls(url):
    assert not ntfy.is_valid_ntfy_url(url)
