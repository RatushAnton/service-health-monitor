"""Publish incident notifications to an ntfy topic."""

import re
from urllib.parse import urlsplit

import httpx


class NtfyDeliveryError(RuntimeError):
    """A failed ntfy delivery, with sensitive URL details removed."""


def is_valid_ntfy_url(url: str) -> bool:
    """Require an HTTPS endpoint with exactly one ntfy topic."""
    if not isinstance(url, str) or len(url) > 2048:
        return False

    try:
        parsed = urlsplit(url)
        _ = parsed.port  # Reject invalid ports.
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and re.fullmatch(r"/[A-Za-z0-9_-]{1,64}", parsed.path) is not None
        )
    except ValueError:
        return False


def send_ntfy(
    topic_url: str,
    event: dict,
    timeout_seconds: float = 5.0,
) -> None:
    """Send an incident using ntfy's plain-text publishing API."""
    if not is_valid_ntfy_url(topic_url):
        raise ValueError("Invalid ntfy topic URL")

    event_type = event["event_type"]
    if event_type not in ("DOWN", "RECOVERED"):
        raise ValueError("Unsupported incident event type")

    down = event_type == "DOWN"

    message = "\n".join(
        [
            f"Target: {event['target_id']}",
            f"Event: {event_type}",
            f"Time: {event['occurred_at']}",
            f"Details: {event['message']}",
            f"Event ID: {event['id']}",
        ]
    )

    headers = {
        "X-Title": f"Service Health Monitor - {event_type}",
        "X-Priority": "4" if down else "3",
        "X-Tags": ("rotating_light,warning" if down else "white_check_mark"),
        "Content-Type": "text/plain; charset=utf-8",
    }

    try:
        response = httpx.post(
            topic_url,
            content=message.encode("utf-8"),
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        raise NtfyDeliveryError(
            f"ntfy returned HTTP {exc.response.status_code}"
        ) from None

    except httpx.RequestError as exc:
        raise NtfyDeliveryError(f"ntfy request failed: {type(exc).__name__}") from None
