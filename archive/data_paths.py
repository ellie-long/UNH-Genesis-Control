"""Persistent Genesis recording paths, independent of Git checkouts/worktrees.

Set GENESIS_DATA_ROOT for a custom location. Defaults to the local
UNH-Genesis-Control/local_data directory, outside repo/ and worktrees/.
This module has no instrument or network access.
"""
import os
from pathlib import Path


def data_root():
    override = os.environ.get("GENESIS_DATA_ROOT", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("GENESIS_DATA_ROOT must be an absolute path")
        return path.resolve()
    return _default_data_root(Path(__file__).resolve().parents[1])


def _default_data_root(checkout):
    """Keep data outside the checkout for both nested and flat deployments."""
    checkout = Path(checkout).resolve()
    if checkout.parent.name == "worktrees":
        return (checkout.parent.parent / "local_data").resolve()
    if checkout.name == "repo" and checkout.parent.name == "UNH-Genesis-Control":
        return (checkout.parent / "local_data").resolve()
    # A flat clone must not write local_data *inside* its Git checkout.
    return (checkout.parent / f"{checkout.name}-local_data").resolve()


def archive_root():
    return data_root() / "archive"


def subsystem_csv_dir():
    return archive_root() / "subsystems" / "pv_csv"


def master_root():
    return archive_root() / "master"


def ssrf_event_dir():
    return archive_root() / "events" / "ssrf"


def ssrf_session_dir():
    return archive_root() / "sessions" / "ssrf"


def ensure_data_directories():
    """Create standard recording directories on first master-recorder startup.

    Does not copy, delete, truncate, or migrate any existing recordings.
    """
    root = data_root()
    for directory in (
        master_root(),
        subsystem_csv_dir(),
        ssrf_event_dir(),
        ssrf_session_dir(),
        root / "logs",
        root / "runtime",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return root


def resolve_recording_path(path, default):
    """Resolve recording destinations outside Git checkouts."""
    if path is None or (isinstance(path, str) and not path.strip()):
        return Path(default)

    requested = Path(path).expanduser()
    if requested.is_absolute():
        return requested

    return (data_root() / requested).resolve()


def legacy_archive_path(path):
    """Translate old device config paths to one persistent subsystem CSV dir.

    Explicit absolute paths are respected to allow specialist deployments.
    """
    if path is None:
        return subsystem_csv_dir()
    requested = Path(path).expanduser()
    if requested.is_absolute():
        return requested
    if requested.as_posix() in ("archive", "archive/data", "data"):
        return subsystem_csv_dir()
    # Even other relative archive destinations belong under local_data.
    return data_root() / requested
