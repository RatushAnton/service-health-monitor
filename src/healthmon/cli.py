"""CLI commands for checking services and viewing saved results."""

import argparse
import sqlite3

from healthmon.config import ConfigError, load_config
from healthmon.probes import check_dns, check_http, check_tcp
from healthmon.storage import get_history, get_latest_results, init_db, save_result

CHECKERS = {
    "dns": check_dns,
    "tcp": check_tcp,
    "http": check_http,
}


def run_checks(config_path: str, db_path: str) -> int:
    """Run all probes, save results, and print their status."""

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    # Prepare the database before running checks.
    try:
        init_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print(f"Database initialization error: {exc}")
        return 3

    targets = config["targets"]
    timeout = config["settings"]["timeout_seconds"]

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
            save_result(db_path, result)
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

    return 2
