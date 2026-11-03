"""Background ntfy notification dispatcher."""

import argparse
import signal
from pathlib import Path
from threading import Event

from healthmon.cli import send_notifications
from healthmon.ntfy import is_valid_ntfy_url


def positive_integer(value: str) -> int:
    """Validate positive CLI integer arguments."""
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Value must be a positive integer") from exc

    if result < 1:
        raise argparse.ArgumentTypeError("Value must be a positive integer")

    return result


def load_ntfy_url(path: str | Path) -> str:
    """Read and validate an ntfy destination without exposing it."""
    try:
        url = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("Cannot read ntfy URL configuration") from exc

    if not is_valid_ntfy_url(url):
        raise ValueError("Invalid ntfy URL configuration")

    return url


def run_notifier(
    db_path: str,
    ntfy_url_file: str | Path,
    interval_seconds: int = 60,
    limit: int = 20,
    *,
    stop_event: Event | None = None,
    once: bool = False,
) -> int:
    """Poll SQLite for pending notifications until stopped."""
    if interval_seconds < 1:
        raise ValueError("Interval must be positive")

    if limit < 1:
        raise ValueError("Limit must be positive")

    # Load once at startup; restart to apply secret changes.
    ntfy_url = load_ntfy_url(ntfy_url_file)
    shutdown = stop_event if stop_event is not None else Event()

    if not once:
        print(
            f"ntfy dispatcher started: interval={interval_seconds}s "
            f"batch_limit={limit}",
            flush=True,
        )

    while not shutdown.is_set():
        result = send_notifications(
            db_path,
            None,
            limit,
            ntfy_url=ntfy_url,
            quiet_when_empty=True,
        )

        if once:
            return result

        if result != 0:
            print(
                f"Notification cycle failed: exit_code={result}",
                flush=True,
            )

        # Interruptible sleep supports graceful container shutdown.
        shutdown.wait(interval_seconds)

    print("ntfy dispatcher stopped.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the notification dispatcher."""
    parser = argparse.ArgumentParser(
        description="Service Health Monitor ntfy dispatcher"
    )

    parser.add_argument(
        "--db",
        default="/var/lib/service-health-monitor/health.db",
    )
    parser.add_argument(
        "--ntfy-url-file",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--interval",
        type=positive_integer,
        default=60,
    )
    parser.add_argument(
        "--limit",
        type=positive_integer,
        default=20,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute one notification cycle and exit",
    )

    args = parser.parse_args(argv)
    shutdown = Event()

    if not args.once:
        signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
        signal.signal(signal.SIGINT, lambda *_: shutdown.set())

    try:
        return run_notifier(
            args.db,
            args.ntfy_url_file,
            interval_seconds=args.interval,
            limit=args.limit,
            stop_event=shutdown,
            once=args.once,
        )
    except ValueError as exc:
        print(f"Notifier configuration error: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
