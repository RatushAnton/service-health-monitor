"""Send incident notifications to HTTP webhook endpoints."""

import httpx


def send_webhook(
    webhook_url: str,
    event: dict,
    timeout_seconds: float = 5.0,
) -> None:
    """POST an incident event as JSON."""

    payload = {
        "event_id": event["id"],
        "target_id": event["target_id"],
        "event_type": event["event_type"],
        "occurred_at": event["occurred_at"],
        "incident_started_at": event["incident_started_at"],
        "error_type": event["error_type"],
        "message": event["message"],
    }

    response = httpx.post(
        webhook_url,
        json=payload,
        headers={
            "X-Healthmon-Event-ID": str(event["id"]),
        },
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    )

    response.raise_for_status()
