from pathlib import Path
import pytest
from archive.data_paths import (
    data_root, subsystem_csv_dir, legacy_archive_path,
    ensure_data_directories, _default_data_root,
)


def test_external_override(monkeypatch, tmp_path):
    path = tmp_path / "local_data"
    monkeypatch.setenv("GENESIS_DATA_ROOT", str(path))
    assert data_root() == path
    assert subsystem_csv_dir() == path / "archive/subsystems/pv_csv"
    assert legacy_archive_path("archive/data") == subsystem_csv_dir()


def test_relative_override_rejected(monkeypatch):
    monkeypatch.setenv("GENESIS_DATA_ROOT", "./local_data")
    with pytest.raises(ValueError):
        data_root()


def test_default_is_outside_checkout(monkeypatch):
    monkeypatch.delenv("GENESIS_DATA_ROOT", raising=False)
    assert "worktrees" not in data_root().parts
    assert data_root().name == "local_data" or data_root().name.endswith("-local_data")
    assert not data_root().is_relative_to(Path(__file__).resolve().parents[1])


def test_all_standard_directories_created_automatically(monkeypatch, tmp_path):
    root = tmp_path / "archive_data"
    monkeypatch.setenv("GENESIS_DATA_ROOT", str(root))
    assert ensure_data_directories() == root
    for rel in (
        "archive/master",
        "archive/subsystems/pv_csv",
        "archive/events/ssrf",
        "archive/sessions/ssrf",
        "logs",
        "runtime",
    ):
        assert (root / rel).is_dir()
    # Repeated starts preserve the same data and directories.
    marker = root / "archive/master" / "retained.txt"
    marker.write_text("recording")
    ensure_data_directories()
    assert marker.read_text() == "recording"


def test_default_layouts_are_outside_git_checkout(tmp_path):
    project = tmp_path / "UNH-Genesis-Control"
    for checkout, expected in (
        (project / "repo", project / "local_data"),
        (project / "worktrees" / "unified-master-archiver", project / "local_data"),
        (project, tmp_path / "UNH-Genesis-Control-local_data"),
    ):
        assert _default_data_root(checkout) == expected.resolve()
        assert not _default_data_root(checkout).is_relative_to(checkout.resolve())


def test_explicit_subsystem_csv_path(monkeypatch, tmp_path):
    monkeypatch.setenv("GENESIS_DATA_ROOT", str(tmp_path))
    assert legacy_archive_path("archive/subsystems/pv_csv") == subsystem_csv_dir()
