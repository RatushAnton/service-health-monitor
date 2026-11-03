"""External heartbeat, conditional on recent monitoring activity."""

import argparse
import signal
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit
from uuid import UUID

import httpx


def validate_ping_url(url: str) -> bool:
    """Accept only a canonical Healthchecks.io UUID ping URL."""
    if not isinstance(url, str):
        return False

    try:
        parsed = urlsplit(url)
        check_id = UUID(parsed.path.removeprefix("/"))

        return (
            parsed.scheme == "https"
            and parsed.netloc == "hc-ping.com"
            and parsed.path == f"/{check_id}"
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def load_ping_url(path: str | Path) -> str:
    """Read the private ping URL without printing its value."""
    try:
        url = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("Cannot read heartbeat configuration") from exc

    if not validate_ping_url(url):
        raise ValueError("Invalid heartbeat configuration")

    return url


def worker_is_fresh(
    db_path: str | Path,
    max_age_seconds: int = 600,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Verify every enabled monitor has recent check history."""
    if max_age_seconds < 1:
        raise ValueError("Maximum check age must be positive")

    current = now if now is not None else datetime.now(UTC)
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"

    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        rows = conn.execute(
            """
            SELECT r.target_id,
                   (
                       SELECT c.checked_at
                       FROM checks AS c
                       WHERE c.target_id = r.target_id
                       ORDER BY c.id DESC
                       LIMIT 1
                   ) AS last_checked
            FROM monitor_registry AS r
            WHERE r.enabled = 1
              AND r.archived_at IS NULL
            ORDER BY r.target_id
            """
        ).fetchall()

    if not rows:
        return False, "No enabled monitors"

    for target_id, timestamp in rows:
        if timestamp is None:
            return False, f"No recorded check for {target_id}"

        try:
            checked = datetime.fromisoformat(timestamp)
            if checked.tzinfo is None:
                raise ValueError("Naive timestamp")
            age = (current - checked).total_seconds()
        except ValueError:
            return False, f"Invalid timestamp for {target_id}"

        if age < -60:
            return False, f"Future timestamp for {target_id}"

        if age > max_age_seconds:
            return False, f"Stale worker check for {target_id}"

    return True, f"{len(rows)} enabled monitors have recent checks"


def run_cycle(
    db_path: str,
    ping_url: str,
    max_age_seconds: int,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> int:
    """Send a heartbeat only if the monitoring worker is fresh."""
    try:
        fresh, reason = worker_is_fresh(db_path, max_age_seconds, now=now)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(
            f"Heartbeat withheld: database check failed ({type(exc).__name__})",
            flush=True,
        )
        return 1

    if not fresh:
        print(f"Heartbeat withheld: {reason}", flush=True)
        return 1

    if dry_run:
        print(f"DRY RUN: {reason}; no request sent", flush=True)
        return 0

    try:
        response = httpx.post(
            ping_url,
            timeout=5,
            follow_redirects=False,
            trust_env=False,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(
            f"Heartbeat request failed ({type(exc).__name__})",
            flush=True,
        )
        return 1

    print(f"Heartbeat submitted: {reason}", flush=True)
    return 0


def run_heartbeat(
    db_path: str,
    ping_url_file: str | Path,
    *,
    interval_seconds: int = 60,
    max_age_seconds: int = 600,
    once: bool = False,
    dry_run: bool = False,
    stop_event: Event | None = None,
) -> int:
    """Run heartbeat cycles until shutdown."""
    if interval_seconds < 1 or max_age_seconds < 1:
        raise ValueError("Intervals must be positive")

    ping_url = load_ping_url(ping_url_file)
    shutdown = stop_event if stop_event is not None else Event()

    while not shutdown.is_set():
        result = run_cycle(
            db_path,
            ping_url,
            max_age_seconds,
            dry_run=dry_run,
        )

        if once:
            return result

        shutdown.wait(interval_seconds)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="External monitoring-worker heartbeat")

    parser.add_argument(
        "--db",
        default="/var/lib/service-health-monitor/health.db",
    )
    parser.add_argument("--ping-url-file", required=True)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--max-age", type=int, default=600)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    shutdown = Event()

    if not args.once:
        signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
        signal.signal(signal.SIGINT, lambda *_: shutdown.set())

    try:
        return run_heartbeat(
            args.db,
            args.ping_url_file,
            interval_seconds=args.interval,
            max_age_seconds=args.max_age,
            once=args.once,
            dry_run=args.dry_run,
            stop_event=shutdown,
        )
    except ValueError as exc:
        print(f"Heartbeat configuration error: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
