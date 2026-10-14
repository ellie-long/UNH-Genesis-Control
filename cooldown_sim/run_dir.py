"""Run directories: everything one simulated cool-down writes lives in one place.

    logs/cooldown_sim/<UTC stamp>_<mode>_<scenario>/
        manifest.json      git HEAD + dirty flag, platform, args, data hashes
        profiles/          generated loopback copies of the canonical profiles
        logs/              one log per launched process
        archive/           archiver CSVs, alarm log, snapshots, ssRF events
        state/             target_status last-state file, pids.json, clock
        probe.csv          every PV change seen by the read-only recorder
        report.md/.json    validation or after-action report

``logs/`` is already gitignored, so nothing a run produces can be committed by
accident, and nothing a run produces lands in a live output path.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import REPO_ROOT

BASE = Path("logs") / "cooldown_sim"
SUBDIRS = ("profiles", "logs", "archive", "state")


@dataclass
class RunDir:
    path: Path
    mode: str
    scenario: str
    meta: dict = field(default_factory=dict)

    def sub(self, name: str) -> Path:
        return self.path / name

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    def write_manifest(self, **extra) -> dict:
        self.meta.update(extra)
        self.manifest_path.write_text(json.dumps(self.meta, indent=2, default=str),
                                      encoding="utf-8")
        return self.meta


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-") or "run"


def create(mode: str, scenario: str, *, root: Path = REPO_ROOT,
           base: Path | None = None, now: _dt.datetime | None = None) -> RunDir:
    """Create a fresh run directory and its manifest."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    parent = (root / (base or BASE)).resolve()
    path = parent / f"{stamp}_{_slug(mode)}_{_slug(scenario)}"
    n = 1
    while path.exists():
        n += 1
        path = parent / f"{stamp}_{_slug(mode)}_{_slug(scenario)}_{n}"
    path.mkdir(parents=True)
    for name in SUBDIRS:
        (path / name).mkdir()
    run = RunDir(path, mode, scenario)
    run.write_manifest(
        created_utc=now.isoformat(),
        mode=mode,
        scenario=scenario,
        git=git_state(root),
        platform={"system": platform.system(), "release": platform.release(),
                  "python": sys.version.split()[0], "node": platform.node()},
    )
    return run


def open_existing(path: Path) -> RunDir:
    path = Path(path).resolve()
    meta = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    return RunDir(path, meta.get("mode", "?"), meta.get("scenario", "?"), meta)


def latest(root: Path = REPO_ROOT, base: Path | None = None) -> Path | None:
    parent = root / (base or BASE)
    runs = sorted(p for p in parent.glob("*") if (p / "manifest.json").exists())
    return runs[-1] if runs else None


def git_state(root: Path = REPO_ROOT) -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"head": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
            "dirty": bool(git("status", "--porcelain"))}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
