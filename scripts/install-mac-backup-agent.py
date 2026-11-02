"""Install or update an isolated macOS backup LaunchAgent runtime."""

import argparse
import os
import plistlib
import shutil
import sys
import tempfile
import venv
from pathlib import Path
from uuid import uuid4

LABEL = "com.ratush.service-health-monitor.backup-pull"
SCHEDULE_SECONDS = 21600

BUNDLED_FILES = (
    "scripts/pull-latest-pi-backup.sh",
    "scripts/pull-pi-backup.sh",
    "src/healthmon/backup.py",
    "src/healthmon/__init__.py",
)


def read_existing_plist(path: Path) -> dict:
    """Read and validate an existing installation."""
    with path.open("rb") as stream:
        configuration = plistlib.load(stream)

    if configuration.get("Label") != LABEL:
        raise ValueError("Existing LaunchAgent has an unexpected label")

    arguments = configuration.get("ProgramArguments")

    if (
        not isinstance(arguments, list)
        or len(arguments) != 3
        or not all(isinstance(arg, str) for arg in arguments)
        or Path(arguments[0]).name != "pull-latest-pi-backup.sh"
    ):
        raise ValueError("Unrecognized LaunchAgent command; refusing update")

    return configuration


def write_plist_atomically(path: Path, configuration: dict) -> None:
    """Publish the plist without exposing a partially written file."""
    fd, name = tempfile.mkstemp(
        prefix=".healthmon-agent-",
        suffix=".plist",
        dir=path.parent,
    )
    temporary = Path(name)

    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(configuration, stream)

        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def switch_release(link: Path, target: str) -> None:
    """Atomically point the current symlink at a release."""
    temporary = link.parent / f".current-{uuid4().hex}"

    try:
        os.symlink(target, temporary)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def install(
    repository: Path,
    home: Path,
    *,
    update: bool = False,
) -> tuple[Path, Path]:
    """Prepare a self-contained runtime without activating launchd."""
    repository = Path(repository)
    home = Path(home)

    runtime = home / "Library/Application Support/service-health-monitor"
    releases = runtime / "releases"
    current = runtime / "current"
    backups = runtime / "backups"

    launch_agents = home / "Library/LaunchAgents"
    logs = home / "Library/Logs/service-health-monitor"
    plist = launch_agents / f"{LABEL}.plist"

    if update and not plist.is_file():
        raise ValueError("--update requires an existing LaunchAgent")

    if not update and plist.exists():
        raise FileExistsError("LaunchAgent already exists. Use --update to upgrade it.")

    if current.is_symlink():
        previous_release = os.readlink(current)
    elif current.exists():
        raise ValueError("Runtime current path is not a symlink")
    else:
        previous_release = None

    existing = read_existing_plist(plist) if update else None

    for relative_path in BUNDLED_FILES:
        if not (repository / relative_path).is_file():
            raise FileNotFoundError(f"Required source file missing: {relative_path}")

    for directory in (
        runtime,
        releases,
        backups,
        launch_agents,
        logs,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    os.chmod(runtime, 0o700)
    os.chmod(releases, 0o700)
    os.chmod(backups, 0o700)
    os.chmod(logs, 0o700)

    release = releases / uuid4().hex
    release.mkdir(mode=0o700)

    switched = False

    try:
        for relative_path in BUNDLED_FILES:
            source = repository / relative_path
            destination = release / relative_path

            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            shutil.copy2(source, destination)

            mode = 0o700 if relative_path.endswith(".sh") else 0o600
            os.chmod(destination, mode)

        # The backup module uses only Python's standard library.
        # No package downloads or pip installation are required.
        venv.EnvBuilder(with_pip=False).create(release / ".venv")

        python = release / ".venv/bin/python"

        if not python.is_file():
            raise RuntimeError("Virtual environment creation failed")

        program = current / "scripts/pull-latest-pi-backup.sh"

        if existing is None:
            configuration = {
                "Label": LABEL,
                "ProgramArguments": [
                    str(program),
                    "healthmon-backup-pi",
                    str(backups),
                ],
                "RunAtLoad": True,
                "StartInterval": SCHEDULE_SECONDS,
                "StandardOutPath": str(logs / "backup.log"),
                "StandardErrorPath": str(logs / "backup-error.log"),
            }
        else:
            # Preserve the user's existing SSH alias, backup path,
            # scheduling interval, and logging configuration.
            configuration = existing.copy()
            arguments = existing["ProgramArguments"].copy()
            arguments[0] = str(program)
            configuration["ProgramArguments"] = arguments

        environment = configuration.get("EnvironmentVariables", {}).copy()
        environment["PYTHONPATH"] = str(current / "src")
        configuration["EnvironmentVariables"] = environment

        target = str(Path("releases") / release.name)

        switch_release(current, target)
        switched = True

        write_plist_atomically(plist, configuration)

    except Exception:
        # Restore the previous release pointer if publishing failed.
        if switched:
            if previous_release is None:
                current.unlink(missing_ok=True)
            else:
                switch_release(current, previous_release)

        shutil.rmtree(release)
        raise

    return plist, release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the Service Health Monitor macOS backup agent"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Upgrade existing runtime and preserve configuration",
    )
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        parser.error("The installer is intended for macOS")

    try:
        plist, release = install(
            repository=Path(__file__).resolve().parents[1],
            home=Path.home(),
            update=args.update,
        )
    except (OSError, ValueError, RuntimeError, plistlib.InvalidFileException) as exc:
        parser.exit(1, f"Installation failed: {exc}\n")

    print(f"Runtime prepared: {release}")
    print(f"LaunchAgent prepared: {plist}")
    print("The LaunchAgent was NOT activated or reloaded.")
    print("Review its configuration before loading it.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
