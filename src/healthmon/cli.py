import argparse
import sqlite3

from healthmon.config import ConfigError, load_config
from healthmon.probes import check_dns, check_http, check_tcp
from healthmon.storage import init_db, save_result

CHECKERS = {
    "dns": check_dns,
    "tcp": check_tcp,
    "http": check_http,
}


def run_checks(config_path: str, db_path: str) -> int:
    """Run health checks, store results, and show a summary."""

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


def main() -> int:
    """Application entry point."""

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

    args = parser.parse_args()

    if args.command == "check":
        return run_checks(args.config, args.db)

    return 2
