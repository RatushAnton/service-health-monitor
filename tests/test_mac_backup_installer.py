"""Tests for isolated macOS backup installer behavior."""

import importlib.util
import plistlib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install-mac-backup-agent.py"

spec = importlib.util.spec_from_file_location(
    "install_mac_backup_agent",
    SCRIPT,
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"

    for relative in installer.BUNDLED_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test content")

    return root


@pytest.fixture(autouse=True)
def fake_venv(monkeypatch: pytest.MonkeyPatch):
    """Avoid creating real Python environments in unit tests."""

    def create(self, path):
        python = Path(path) / "bin/python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("fake interpreter")

    monkeypatch.setattr(installer.venv.EnvBuilder, "create", create)


def test_fresh_install_uses_isolated_runtime(
    repository: Path,
    tmp_path: Path,
):
    home = tmp_path / "home"

    plist, release = installer.install(repository, home)

    with plist.open("rb") as stream:
        configuration = plistlib.load(stream)

    runtime = home / "Library/Application Support/service-health-monitor"
    current = runtime / "current"

    assert current.is_symlink()
    assert current.resolve() == release
    assert (release / ".venv/bin/python").is_file()

    assert configuration["ProgramArguments"][0] == str(
        current / "scripts/pull-latest-pi-backup.sh"
    )
    assert configuration["ProgramArguments"][2] == str(runtime / "backups")
    assert configuration["EnvironmentVariables"]["PYTHONPATH"] == str(current / "src")
    assert configuration["StartInterval"] == 21600

    assert not (
        configuration["ProgramArguments"][0].startswith(str(home / "Documents"))
    )

    assert plist.stat().st_mode & 0o077 == 0
    assert runtime.stat().st_mode & 0o077 == 0


def test_existing_install_requires_explicit_update(
    repository: Path,
    tmp_path: Path,
):
    home = tmp_path / "home"

    plist, release = installer.install(repository, home)
    original = plist.read_bytes()

    with pytest.raises(FileExistsError):
        installer.install(repository, home)

    assert plist.read_bytes() == original
    assert (
        home / "Library/Application Support/service-health-monitor/current"
    ).resolve() == release


def test_update_preserves_backups_and_configuration(
    repository: Path,
    tmp_path: Path,
):
    home = tmp_path / "home"

    plist, previous = installer.install(repository, home)

    runtime = home / "Library/Application Support/service-health-monitor"
    backup = runtime / "backups/important.db"
    backup.write_bytes(b"preserve this backup")

    with plist.open("rb") as stream:
        configuration = plistlib.load(stream)

    configuration["ProgramArguments"][1] = "custom-ssh-host"
    configuration["StartInterval"] = 3600

    with plist.open("wb") as stream:
        plistlib.dump(configuration, stream)

    updated_plist, latest = installer.install(
        repository,
        home,
        update=True,
    )

    with updated_plist.open("rb") as stream:
        updated = plistlib.load(stream)

    assert latest != previous
    assert previous.is_dir()
    assert latest.is_dir()
    assert (runtime / "current").resolve() == latest

    assert updated["ProgramArguments"][1] == "custom-ssh-host"
    assert updated["StartInterval"] == 3600
    assert backup.read_bytes() == b"preserve this backup"


def test_update_failure_preserves_previous_release(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    plist, previous = installer.install(repository, home)
    original_plist = plist.read_bytes()

    def fail_create(self, path):
        raise RuntimeError("simulated environment failure")

    monkeypatch.setattr(
        installer.venv.EnvBuilder,
        "create",
        fail_create,
    )

    with pytest.raises(RuntimeError, match="simulated"):
        installer.install(repository, home, update=True)

    runtime = home / "Library/Application Support/service-health-monitor"

    assert (runtime / "current").resolve() == previous
    assert plist.read_bytes() == original_plist


def test_update_rejects_unrecognized_agent(
    repository: Path,
    tmp_path: Path,
):
    home = tmp_path / "home"
    plist, previous = installer.install(repository, home)

    with plist.open("rb") as stream:
        configuration = plistlib.load(stream)

    configuration["ProgramArguments"][0] = "/tmp/unrelated-program"

    with plist.open("wb") as stream:
        plistlib.dump(configuration, stream)

    with pytest.raises(ValueError, match="Unrecognized"):
        installer.install(repository, home, update=True)

    runtime = home / "Library/Application Support/service-health-monitor"
    assert (runtime / "current").resolve() == previous
