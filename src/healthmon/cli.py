"""CLI commands for checking services and viewing saved results."""

import argparse
import sqlite3
from pathlib import Path

import httpx

from healthmon.alerting import send_webhook
from healthmon.config import ConfigError, load_config
from healthmon.ntfy import NtfyDeliveryError, is_valid_ntfy_url, send_ntfy
from healthmon.probes import check_dns, check_http, check_tcp
from healthmon.storage import (
    claim_pending_event,
    get_history,
    get_latest_results,
    init_db,
    mark_delivery_failed,
    mark_event_delivered,
    record_check_with_incident,
)

CHECKERS = {
    "dns": check_dns,
    "tcp": check_tcp,
    "http": check_http,
}


def run_checks(config_path: str, db_path: str) -> int:
    """Load YAML and execute health checks."""
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    return run_checks_config(config, db_path)


def run_checks_config(config: dict, db_path: str) -> int:
    """Run health checks using an already-loaded configuration."""

    # Prepare the database before running checks.
    try:
        init_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print(f"Database initialization error: {exc}")
        return 3

    targets = config["targets"]
    settings = config["settings"]
    timeout = settings["timeout_seconds"]
    failure_threshold = settings["failure_threshold"]
    recovery_threshold = settings["recovery_threshold"]

    total = len(targets)
    passed = 0
    failed = 0

    print("\nService Health Monitor")
    print("-" * 70)

    for target in targets:
        checker = CHECKERS[target["type"]]
        result = checker(target, timeout)

        # Persist both healthy and failed results.
        try:
            update = record_check_with_incident(
                db_path,
                result,
                failure_threshold,
                recovery_threshold,
            )
        except (sqlite3.Error, OSError) as exc:
            print(f"Database write error: {exc}")
            return 3

        status = "UP" if result["success"] else "DOWN"

        if result["success"]:
            passed += 1
        else:
            failed += 1

        latency = result["latency_ms"]
        name = target["name"]
        message = result["message"]

        print(f"{name:<25} {status:<6} {latency:>9.2f} ms")

        if not result["success"]:
            print(f"  Reason: {message}")

        if update.event:
            print(f"  Incident event: {update.event}")

        elif result["success"] and update.state.incident_open:
            print(
                "  Recovery pending: "
                f"{update.state.consecutive_successes}/"
                f"{recovery_threshold} successful checks"
            )

        elif not result["success"] and not update.state.incident_open:
            print(
                "  Incident pending: "
                f"{update.state.consecutive_failures}/"
                f"{failure_threshold} failed checks"
            )

        elif update.state.incident_open:
            print("  Incident: OPEN")

    print("-" * 70)
    print(f"Total: {total} | Healthy: {passed} | Failed: {failed}")
    print(f"Results saved to: {db_path}\n")

    return 1 if failed else 0


def show_summary(db_path: str) -> int:
    """Print the latest saved result for each target."""

    try:
        rows = get_latest_results(db_path)
    except (sqlite3.Error, OSError) as exc:
        print(f"Database read error: {exc}")
        return 3

    if not rows:
        print("No monitoring results recorded yet.")
        return 0

    print("\nLast Recorded Service Health")
    print("-" * 85)

    for row in rows:
        status = "UP" if row["success"] else "DOWN"

        print(
            f"{row['target_id']:<22} "
            f"{status:<6} "
            f"{row['latency_ms']:>9.2f} ms  "
            f"{row['checked_at']}"
        )

    print("-" * 85)
    print(f"Targets with recorded results: {len(rows)}")
    print()

    return 0


def show_history(
    db_path: str,
    target_id: str,
    limit: int,
) -> int:
    """Print recent saved checks for one target."""

    if limit < 1:
        print("History limit must be a positive integer")
        return 2

    try:
        rows = get_history(db_path, target_id, limit)
    except (sqlite3.Error, OSError) as exc:
        print(f"Database read error: {exc}")
        return 3

    if not rows:
        print(f"No history found for target: {target_id}")
        return 0

    print(f"\nCheck history: {target_id}")
    print("-" * 70)

    for row in rows:
        status = "UP" if row["success"] else "DOWN"

        print(f"{row['checked_at']}  {status:<6} {row['latency_ms']:>9.2f} ms")

        if not row["success"]:
            print(f"  {row['error_type']}: {row['message']}")

    print("-" * 70)
    print(f"Showing {len(rows)} recorded checks\n")

    return 0


def send_notifications(
    db_path: str,
    webhook_url: str | None,
    limit: int,
    *,
    ntfy_url: str | None = None,
    quiet_when_empty: bool = False,
) -> int:
    """Claim and deliver queued notifications safely."""

    if limit < 1:
        print("Notification limit must be positive")
        return 2

    if (webhook_url is None) == (ntfy_url is None):
        print("Specify exactly one notification destination")
        return 2

    if ntfy_url is not None:
        if not is_valid_ntfy_url(ntfy_url):
            print("Invalid ntfy topic URL")
            return 2
    elif not webhook_url.startswith(("http://", "https://")):
        print("Webhook URL must use HTTP or HTTPS")
        return 2

    try:
        if not Path(db_path).is_file():
            raise FileNotFoundError(f"Database not found: {db_path}")

        init_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print(f"Database initialization error: {exc}")
        return 3

    delivered = 0

    for _ in range(limit):
        try:
            event = claim_pending_event(
                db_path,
                lease_seconds=60,
            )
        except (sqlite3.Error, OSError) as exc:
            print(f"Database claim error: {exc}")
            return 3

        if event is None:
            break

        event_id = event["id"]
        token = event["lease_token"]

        try:
            if ntfy_url is not None:
                send_ntfy(ntfy_url, event)
            else:
                send_webhook(webhook_url, event)

        except (httpx.HTTPError, httpx.InvalidURL, NtfyDeliveryError) as exc:
            attempt = event["delivery_attempts"]
            delay = min(
                60 * (2 ** min(attempt - 1, 6)),
                3600,
            )

            try:
                mark_delivery_failed(
                    db_path,
                    event_id,
                    token,
                    str(exc),
                    delay,
                )
            except (sqlite3.Error, OSError) as db_exc:
                print(f"Database write error: {db_exc}")
                return 3

            channel = "ntfy" if ntfy_url is not None else "Webhook"
            print(f"{channel} delivery failed: event {event_id}")
            print(f"  Reason: {exc}")
            print(f"  Attempt: {attempt}")
            print(f"  Next retry in: {delay} seconds")
            return 4

        try:
            mark_event_delivered(
                db_path,
                event_id,
                token,
            )
        except (sqlite3.Error, OSError) as exc:
            print(f"Database write error: {exc}")
            return 3

        delivered += 1
        print(f"Delivered event {event_id}: {event['target_id']} {event['event_type']}")

    if delivered == 0:
        if not quiet_when_empty:
            print("No incident notifications ready for delivery")
    else:
        print(f"Notifications delivered: {delivered}")

    return 0


def main() -> int:
    """Parse CLI arguments and run the selected command."""

    parser = argparse.ArgumentParser(
        description="Home Network and Service Health Monitor"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    check_parser = subparsers.add_parser(
        "check",
        help="Run health checks for all configured targets",
    )

    check_parser.add_argument(
        "--config",
        default="config/targets.yaml",
        help="Path to the YAML configuration file",
    )

    check_parser.add_argument(
        "--db",
        default="data/health.db",
        help="Path to the SQLite database",
    )

    summary_parser = subparsers.add_parser(
        "summary",
        help="Show the last recorded state of each target",
    )

    summary_parser.add_argument(
        "--db",
        default="data/health.db",
        help="Path to the SQLite database",
    )

    history_parser = subparsers.add_parser(
        "history",
        help="Show historical checks for one target",
    )

    history_parser.add_argument(
        "--target",
        required=True,
        help="Target ID to display",
    )

    history_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of records to show",
    )

    history_parser.add_argument(
        "--db",
        default="data/health.db",
        help="Path to the SQLite database",
    )

    notify_parser = subparsers.add_parser(
        "notify",
        help="Send pending incident events to a webhook",
    )

    notify_parser.add_argument(
        "--db",
        default="data/health.db",
        help="Path to the SQLite database",
    )

    destination = notify_parser.add_mutually_exclusive_group(required=True)
    destination.add_argument(
        "--webhook-url",
        help="Generic HTTP webhook destination",
    )
    destination.add_argument(
        "--ntfy-url-file",
        type=Path,
        help="Private file containing the HTTPS ntfy topic URL",
    )

    notify_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of events to deliver",
    )

    args = parser.parse_args()

    if args.command == "check":
        return run_checks(args.config, args.db)

    if args.command == "summary":
        return show_summary(args.db)

    if args.command == "history":
        return show_history(
            args.db,
            args.target,
            args.limit,
        )

    if args.command == "notify":
        ntfy_url = None
        if args.ntfy_url_file is not None:
            try:
                ntfy_url = args.ntfy_url_file.read_text(encoding="utf-8").strip()
            except OSError:
                print("Cannot read ntfy URL file")
                return 2

        return send_notifications(
            args.db,
            args.webhook_url,
            args.limit,
            ntfy_url=ntfy_url,
        )
    return 2
