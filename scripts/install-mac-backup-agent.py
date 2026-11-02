
"""Generate the macOS LaunchAgent for off-device backup retrieval."""

import os
import plistlib
from pathlib import Path

LABEL = "com.ratush.service-health-monitor.backup-pull"


def main() -> None:
    home = Path.home()
    repository = Path(__file__).resolve().parents[1]

    launch_agents = home / "Library" / "LaunchAgents"
    logs = home / "Library" / "Logs" / "service-health-monitor"

    launch_agents.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)

    destination = launch_agents / f"{LABEL}.plist"

    if destination.exists():
        raise SystemExit(
            f"LaunchAgent already exists: {destination}"
        )

    program = repository / "scripts" / "pull-latest-pi-backup.sh"

    if not program.is_file():
        raise SystemExit(f"Backup script missing: {program}")

    configuration = {
        "Label": LABEL,
        "ProgramArguments": [
            str(program),
            "healthmon-backup-pi",
            str(home / "Documents" / "healthmon-backups"),
        ],
        "RunAtLoad": True,
        "StartInterval": 21600,
        "StandardOutPath": str(logs / "backup.log"),
        "StandardErrorPath": str(logs / "backup-error.log"),
    }

    with destination.open("xb") as file:
        plistlib.dump(configuration, file)

    os.chmod(destination, 0o600)

    print(f"LaunchAgent prepared: {destination}")
    print("Not activated. Review and load it separately.")


if __name__ == "__main__":
    main()
