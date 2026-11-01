import argparse

from healthmon.config import ConfigError, load_config
from healthmon.probes import check_dns, check_http, check_tcp

# Map YAML check types to Python functions
CHECKERS = {
    "dns": check_dns,
    "tcp": check_tcp,
    "http": check_http,
}


def run_checks(config_path: str) -> int:
    """Run all configured health checks and show a summary."""

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    targets = config["targets"]
    timeout = config["settings"]["timeout_seconds"]

    total = len(targets)
    passed = 0
    failed = 0

    print("\nService Health Monitor")
    print("-" * 70)

    for target in targets:
        # Select the appropriate probe
        checker = CHECKERS[target["type"]]

        # Execute the health check
        result = checker(target, timeout)

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
    print()

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

    args = parser.parse_args()

    if args.command == "check":
        return run_checks(args.config)

    return 2
