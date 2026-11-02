"""Periodically execute monitoring checks in a container."""

import argparse
import signal
from threading import Event

from healthmon.cli import run_checks


def positive_interval(value: str) -> int:
    """Validate the monitoring interval."""
    try:
        interval = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Interval must be a positive integer") from exc

    if interval < 1:
        raise argparse.ArgumentTypeError("Interval must be a positive integer")

    return interval


def run_worker(
    config_path: str,
    db_path: str,
    interval_seconds: int,
    *,
    stop_event: Event | None = None,
    once: bool = False,
) -> int:
    """Run checks repeatedly until shutdown is requested."""

    if interval_seconds < 1:
        raise ValueError("Interval must be positive")

    shutdown = stop_event if stop_event is not None else Event()

    while not shutdown.is_set():
        result = run_checks(config_path, db_path)

        if once:
            return result

        if result not in (0, 1):
            print(
                f"Worker warning: check exited with code {result}",
                flush=True,
            )

        shutdown.wait(interval_seconds)

    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse worker arguments and install shutdown handlers."""

    parser = argparse.ArgumentParser(
        description="Service Health Monitor background worker"
    )

    parser.add_argument(
        "--config",
        default="/etc/healthmon/targets.yaml",
    )

    parser.add_argument(
        "--db",
        default="/var/lib/service-health-monitor/health.db",
    )

    parser.add_argument(
        "--interval",
        type=positive_interval,
        default=300,
        help="Seconds to wait after each check cycle",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute one check cycle and exit",
    )

    args = parser.parse_args(argv)
    shutdown = Event()

    if not args.once:
        signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
        signal.signal(signal.SIGINT, lambda *_: shutdown.set())

    return run_worker(
        args.config,
        args.db,
        args.interval,
        stop_event=shutdown,
        once=args.once,
    )


if __name__ == "__main__":
    raise SystemExit(main())
