"""Refuse to run a simulated cool-down anywhere near a live Genesis stack.

The simulator keeps the live PV names (so the operator screens and logic run
unchanged) and isolates itself by host and port instead.  That is only safe on
a development host with no live stack running, so before anything starts the
guard checks:

1. the host is not a production control computer (no override);
2. no live Genesis GNU-Screen sessions exist (``genesis-*``, ``ioc-manager``);
3. no foreign Genesis processes run (``master_ioc.py``, ``ioc_manager.py``,
   ``online_fitter``, ``tools/sim_*``) -- foreign meaning not started for this
   run directory;
4. nothing listens on 127.0.0.1 on any live Genesis port.

Any finding is a refusal; ``python -m cooldown_sim guard`` exits 2.

The guard never signals processes (``os.kill(pid, 0)`` would send CTRL_C_EVENT
on Windows); it only reads process tables and connection lists.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import ports as ports_mod

#: Production/operator control computers.  Never run the simulator there.
PRODUCTION_HOSTS = frozenset({"sliferlab-ubuntu"})

LIVE_SESSION_PREFIXES = ("genesis-", "ioc-manager", "ioc_manager")

#: Command-line fragments that identify a Genesis runtime process.
GENESIS_PROCESS_MARKERS = ("master_ioc.py", "ioc_manager.py", "online_fitter",
                           "tools/sim_", "tools\\sim_")


@dataclass(frozen=True)
class Refusal:
    check: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.detail}"


# ------------------------------------------------------------------- checks
def short_hostname() -> str:
    return socket.gethostname().split(".")[0].lower()


def check_host(hostname: str | None = None) -> list[Refusal]:
    host = (hostname or short_hostname()).lower()
    if host in PRODUCTION_HOSTS:
        return [Refusal("host", f"{host} is a production control computer; "
                                "simulated cool-downs run on development hosts only")]
    return []


def list_screen_sessions() -> list[str]:
    """Session names from ``screen -ls`` ('' list when screen is absent)."""
    if not shutil.which("screen"):
        return []
    try:
        out = subprocess.run(["screen", "-ls"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines():
        parts = line.strip().split()
        if parts and "." in parts[0] and parts[0].split(".", 1)[0].isdigit():
            names.append(parts[0].split(".", 1)[1])
    return names


def check_screen(sessions: Iterable[str] | None = None) -> list[Refusal]:
    sessions = list_screen_sessions() if sessions is None else list(sessions)
    live = [s for s in sessions if s.lower().startswith(LIVE_SESSION_PREFIXES)]
    if live:
        return [Refusal("screen", "live Genesis screen sessions running: " + ", ".join(sorted(live)))]
    return []


def _iter_process_cmdlines() -> Iterable[tuple[int, str]]:
    import psutil
    own = {os.getpid()}
    try:
        own.update(p.pid for p in psutil.Process().parents())
    except Exception:          # noqa: BLE001
        pass
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.info["pid"] in own:
                continue
            cmd = " ".join(proc.info.get("cmdline") or [])
        except Exception:      # noqa: BLE001 - AccessDenied / vanished
            continue
        if cmd:
            yield proc.info["pid"], cmd


def check_processes(run_dir: Path | None = None,
                    processes: Iterable[tuple[int, str]] | None = None) -> list[Refusal]:
    """Genesis runtime processes not belonging to this run directory."""
    procs = _iter_process_cmdlines() if processes is None else processes
    marker = str(run_dir) if run_dir is not None else None
    marker_alt = marker.replace("\\", "/") if marker else None
    foreign = []
    for pid, cmd in procs:
        if not any(m in cmd for m in GENESIS_PROCESS_MARKERS):
            continue
        if "cooldown_sim" in cmd and (marker is None or marker in cmd or marker_alt in cmd):
            # our own launcher/controller processes for this run
            continue
        if marker and (marker in cmd or marker_alt in cmd):
            continue
        foreign.append(f"pid {pid}: {cmd[:160]}")
    if foreign:
        return [Refusal("process", "Genesis processes already running:\n    "
                        + "\n    ".join(foreign))]
    return []


def check_live_ports(live: set[int] | None = None,
                     probe: Callable[[set[int]], set[int]] = ports_mod.listening_ports) -> list[Refusal]:
    live = ports_mod.live_ports() if live is None else live
    busy = probe(live)
    if busy:
        return [Refusal("port", "live Genesis ports are listening on 127.0.0.1: "
                        + ", ".join(str(p) for p in sorted(busy)))]
    return []


# ------------------------------------------------------------------- driver
def check(run_dir: Path | None = None, *, hostname: str | None = None,
          sessions: Iterable[str] | None = None,
          processes: Iterable[tuple[int, str]] | None = None,
          live: set[int] | None = None,
          probe: Callable[[set[int]], set[int]] = ports_mod.listening_ports) -> list[Refusal]:
    """All refusals (empty list means it is safe to start)."""
    found: list[Refusal] = []
    found += check_host(hostname)
    found += check_screen(sessions)
    found += check_processes(run_dir, processes)
    found += check_live_ports(live, probe)
    return found


def describe_host() -> str:
    return f"{short_hostname()} ({platform.system()} {platform.release()})"
