"""Tests for consistent SQLite backup and verification."""

import sqlite3
from pathlib import Path

import pytest

from healthmon.backup import create_backup, main, verify_database
from healthmon.storage import init_db, save_result


@pytest.fixture
def source_db(tmp_path: Path) -> Path:
    db = tmp_path / "health.db"
    init_db(db)

    save_result(
        db,
        {
            "target_id": "test_dns",
            "check_type": "dns",
            "checked_at": "2026-11-02T10:00:00+00:00",
            "success": True,
            "latency_ms": 2.0,
            "message": "Resolved successfully",
        },
    )

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO registry_settings(singleton, settings_json)
            VALUES (1, ?)
            """,
            ('{"timeout_seconds":3,"failure_threshold":2,"recovery_threshold":1}',),
        )
        conn.execute(
            """
            INSERT INTO monitor_registry(
                target_id, target_json, enabled,
                created_at, archived_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "test_dns",
                '{"id":"test_dns","name":"DNS","type":"dns","host":"example.com"}',
                0,
                "2026-11-02T09:00:00+00:00",
                "2026-11-02T10:01:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO incident_events(
                target_id, event_type, occurred_at, message
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "test_dns",
                "DOWN",
                "2026-11-02T10:00:00+00:00",
                "Test incident",
            ),
        )

    return db


def test_backup_preserves_history_registry_and_incidents(
    source_db: Path, tmp_path: Path
):
    backup = create_backup(source_db, tmp_path / "backups")

    verify_database(backup)

    with sqlite3.connect(backup) as conn:
        checks = conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]
        incidents = conn.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0]
        registry = conn.execute(
            """
            SELECT enabled, archived_at
            FROM monitor_registry
            WHERE target_id = 'test_dns'
            """
        ).fetchone()

    assert checks == 1
    assert incidents == 1
    assert registry == (0, "2026-11-02T10:01:00+00:00")


def test_missing_source_is_not_created(tmp_path: Path):
    source = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError):
        create_backup(source, tmp_path / "backups")

    assert not source.exists()


def test_corrupted_database_is_rejected(tmp_path: Path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        verify_database(path)


def test_empty_database_is_rejected(tmp_path: Path):
    path = tmp_path / "empty.db"

    with sqlite3.connect(path):
        pass

    with pytest.raises(ValueError, match="Missing required"):
        verify_database(path)


def test_backups_are_unique_and_not_overwritten(source_db: Path, tmp_path: Path):
    folder = tmp_path / "backups"

    first = create_backup(source_db, folder, keep=2)
    second = create_backup(source_db, folder, keep=2)

    assert first != second
    assert first.exists()
    assert second.exists()


def test_retention_preserves_unrelated_files(source_db: Path, tmp_path: Path):
    folder = tmp_path / "backups"
    folder.mkdir(mode=0o700)

    unrelated = folder / "important-notes.txt"
    unrelated.write_text("do not delete")

    for _ in range(4):
        create_backup(source_db, folder, keep=2)

    backups = list(folder.glob("healthmon-*.db"))

    assert len(backups) == 2
    assert unrelated.read_text() == "do not delete"


def test_failed_backup_preserves_existing_backup(source_db: Path, tmp_path: Path):
    folder = tmp_path / "backups"
    good = create_backup(source_db, folder)

    source_db.write_bytes(b"corrupted source")

    with pytest.raises(sqlite3.DatabaseError):
        create_backup(source_db, folder)

    assert good.exists()
    verify_database(good)

    assert not list(folder.glob("*.tmp"))


def test_backup_directory_is_private(source_db: Path, tmp_path: Path):
    backup = create_backup(source_db, tmp_path / "backups")

    assert backup.stat().st_mode & 0o077 == 0
    assert backup.parent.stat().st_mode & 0o077 == 0


def test_cli_create_and_verify(source_db: Path, tmp_path: Path):
    folder = tmp_path / "backups"

    assert (
        main(
            [
                "create",
                "--db",
                str(source_db),
                "--out-dir",
                str(folder),
                "--keep",
                "3",
            ]
        )
        == 0
    )

    backup = next(folder.glob("healthmon-*.db"))

    assert main(["verify", "--db", str(backup)]) == 0


def test_invalid_retention_rejected(source_db: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="Retention"):
        create_backup(source_db, tmp_path / "backups", keep=0)


def test_restore_check_preserves_records(source_db: Path, tmp_path: Path):
    """A temporary restored copy contains all critical records."""
    from healthmon.backup import verify_restore

    backup = create_backup(source_db, tmp_path / "backups")
    original_bytes = backup.read_bytes()

    counts = verify_restore(backup)

    assert counts["checks"] == 1
    assert counts["incident_events"] == 1
    assert counts["monitor_registry"] == 1
    assert counts["registry_settings"] == 1
    assert counts["archived_monitors"] == 1

    # The verification operation must not mutate the backup.
    assert backup.read_bytes() == original_bytes


def test_restore_check_rejects_corruption(tmp_path: Path):
    """A corrupted backup must never pass restoration checks."""
    from healthmon.backup import verify_restore

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a SQLite database")

    with pytest.raises(sqlite3.DatabaseError):
        verify_restore(corrupt)


def test_retention_keeps_newest_with_same_second_names(
    source_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Random suffix ordering must not determine which backup survives."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from healthmon import backup as backup_module

    class FrozenClock:
        @staticmethod
        def now(tz):
            return datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

    suffixes = iter(["ffffffffffff", "000000000000"])

    monkeypatch.setattr(backup_module, "datetime", FrozenClock)
    monkeypatch.setattr(
        backup_module,
        "uuid4",
        lambda: SimpleNamespace(hex=next(suffixes)),
    )

    folder = tmp_path / "backups"

    first = create_backup(source_db, folder, keep=1)
    second = create_backup(source_db, folder, keep=1)

    assert not first.exists()
    assert second.exists()
    assert list(folder.glob("healthmon-*.db")) == [second]


def test_cli_restore_check(source_db: Path, tmp_path: Path):
    """The restore-check command succeeds on a verified backup."""
    backup = create_backup(source_db, tmp_path / "backups")

    assert main(["restore-check", "--db", str(backup)]) == 0


def test_restore_uses_backup_directory_for_scratch(
    source_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Restore verification must not depend on Docker's small /tmp."""
    from healthmon import backup as backup_module

    folder = tmp_path / "backups"
    backup = create_backup(source_db, folder)

    original = backup_module.tempfile.TemporaryDirectory
    scratch_dirs = []

    def track_temporary_directory(*args, **kwargs):
        scratch_dirs.append(Path(kwargs["dir"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(
        backup_module.tempfile,
        "TemporaryDirectory",
        track_temporary_directory,
    )

    counts = backup_module.verify_restore(backup)

    assert counts["checks"] == 1
    assert scratch_dirs == [folder]
    assert not list(folder.glob("healthmon-restore-check-*"))
