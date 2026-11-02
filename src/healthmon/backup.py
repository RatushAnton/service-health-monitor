"""Consistent, verified backups of the monitoring SQLite database."""

import argparse
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

BACKUP_PATTERN = re.compile(r"healthmon-\d{8}T\d{6}Z-[0-9a-f]{12}\.db")

REQUIRED_TABLES = {
    "checks",
    "incident_events",
    "monitor_registry",
    "registry_settings",
}


def verify_database(path: str | Path) -> None:
    """Verify integrity and required application tables, read-only."""
    db = Path(path)

    if not db.is_file():
        raise FileNotFoundError(f"Database not found: {db}")

    uri = db.resolve().as_uri() + "?mode=ro"

    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchall()

        if result != [("ok",)]:
            raise ValueError(f"SQLite integrity check failed: {result}")

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        missing = REQUIRED_TABLES - tables
        if missing:
            raise ValueError(f"Missing required database tables: {sorted(missing)}")


def create_backup(
    source: str | Path,
    backup_dir: str | Path,
    keep: int = 7,
) -> Path:
    """Create an atomic, verified backup without modifying the source."""
    if keep < 1:
        raise ValueError("Retention count must be at least 1")

    source = Path(source)
    destination_dir = Path(backup_dir)

    if not source.is_file():
        raise FileNotFoundError(f"Source database not found: {source}")

    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    if destination_dir.is_symlink():
        raise ValueError("Backup directory must not be a symlink")

    if destination_dir.stat().st_mode & 0o077:
        raise PermissionError(
            "Backup directory must not be accessible to group or others"
        )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"healthmon-{timestamp}-{uuid4().hex[:12]}.db"
    destination = destination_dir / filename

    fd, temporary_name = tempfile.mkstemp(
        prefix=".healthmon-",
        suffix=".tmp",
        dir=destination_dir,
    )
    os.close(fd)
    temporary = Path(temporary_name)

    try:
        source_uri = source.resolve().as_uri() + "?mode=ro"

        with (
            closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as original,
            closing(sqlite3.connect(temporary)) as snapshot,
        ):
            original.backup(snapshot, pages=128, sleep=0.1)

        # Only publish a complete, verified database.
        verify_database(temporary)

        # A hard link publishes atomically without replacing an
        # existing backup. Both paths are on the same filesystem.
        os.link(temporary, destination)

    finally:
        temporary.unlink(missing_ok=True)

    # Always retain the newly created backup.
    # Sort previous backups by modification time, not random filename suffix.
    existing = sorted(
        (
            path
            for path in destination_dir.iterdir()
            if (
                BACKUP_PATTERN.fullmatch(path.name)
                and path.is_file()
                and not path.is_symlink()
                and path != destination
            )
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )

    for obsolete in existing[keep - 1 :]:
        obsolete.unlink()

    return destination


def main(argv: list[str] | None = None) -> int:
    """Run backup creation or read-only validation."""
    parser = argparse.ArgumentParser(
        description="Service Health Monitor SQLite backups"
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    create = subparsers.add_parser("create")
    create.add_argument("--db", required=True)
    create.add_argument("--out-dir", required=True)
    create.add_argument("--keep", type=int, default=7)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--db", required=True)

    restore = subparsers.add_parser(
        "restore-check",
        help="Test restoration into a temporary database",
    )
    restore.add_argument("--db", required=True)

    args = parser.parse_args(argv)

    try:
        if args.command == "create":
            result = create_backup(
                args.db,
                args.out_dir,
                args.keep,
            )
            print(f"Backup created and verified: {result}")

        elif args.command == "verify":
            verify_database(args.db)
            print(f"Database integrity verified: {args.db}")

        elif args.command == "restore-check":
            counts = verify_restore(args.db)
            print("Restore verification successful")
            for table, count in counts.items():
                print(f"  {table}: {count}")

    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Backup operation failed: {exc}", file=sys.stderr)
        return 1

    return 0


def verify_restore(backup_path: str | Path) -> dict[str, int]:
    """Restore a backup into temporary storage and validate it.

    Never opens or overwrites the live monitoring database.
    The restored copy is automatically removed afterward.
    """
    backup = Path(backup_path)

    # Reject corrupted or incomplete backups before restoration.
    verify_database(backup)

    with tempfile.TemporaryDirectory(prefix="healthmon-restore-check-") as directory:
        restored = Path(directory) / "restored.db"
        source_uri = backup.resolve().as_uri() + "?mode=ro"

        with (
            closing(
                sqlite3.connect(
                    source_uri,
                    uri=True,
                    timeout=10,
                )
            ) as original,
            closing(sqlite3.connect(restored)) as destination,
        ):
            original.backup(
                destination,
                pages=128,
                sleep=0.1,
            )

        # Validate the independently restored database.
        verify_database(restored)

        with closing(sqlite3.connect(restored)) as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in sorted(REQUIRED_TABLES)
            }

            counts["archived_monitors"] = connection.execute(
                """
                SELECT COUNT(*)
                FROM monitor_registry
                WHERE archived_at IS NOT NULL
                """
            ).fetchone()[0]

        return counts


if __name__ == "__main__":
    raise SystemExit(main())
