"""Cross-platform process group for replay IOCs (no GNU Screen).

Every child:

* runs with cwd = repository root (drivers resolve calibration CSVs and state
  tables against cwd) and the scrubbed loopback EPICS environment;
* gets ``stdin=PIPE`` held open -- ``master_ioc.py`` ends in
  ``softioc.interactive_ioc``, which exits when stdin closes -- so a clean
  stop is ``exit()`` written to that pipe;
* logs stdout+stderr to ``<run>/logs/<name>.log``.

Windows: each child is created in a new process group and assigned to a Job
Object with KILL_ON_JOB_CLOSE, so if the launcher dies every child dies too.
POSIX: each child gets its own session; teardown signals the process group.

``pids.json`` records every child with its command line; ``stop_run`` only
ever signals a PID whose live command line still contains the run directory,
so a recycled PID can never be hit.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import REPO_ROOT

log = logging.getLogger(__name__)
IS_WINDOWS = os.name == "nt"

#: every child carries the run directory here, so leftovers can be identified
#: even when their command line does not mention it (tools/sim_*.py)
RUN_ENV = "COOLDOWN_SIM_RUN"


# ------------------------------------------------------------- Windows job
class _Job:
    """Windows Job Object that kills its processes when the handle closes."""

    def __init__(self) -> None:
        self.handle = None
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            handle = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000      # KILL_ON_JOB_CLOSE
            ok = k32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info))
            if handle and ok:
                self.handle, self._k32 = handle, k32
        except Exception:      # noqa: BLE001 - best effort; teardown still kills
            log.exception("could not create Windows job object")

    def assign(self, proc: subprocess.Popen) -> None:
        if self.handle is None:
            return
        try:
            self._k32.AssignProcessToJobObject(self.handle, int(proc._handle))  # noqa: SLF001
        except Exception:      # noqa: BLE001
            log.exception("could not assign pid %s to job", proc.pid)


# ------------------------------------------------------------- process group
@dataclass
class Child:
    name: str
    proc: subprocess.Popen
    cmd: list[str]
    log_path: Path
    role: str
    started: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None


class ProcessGroup:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.children: list[Child] = []
        self._job = _Job()

    def start(self, name: str, args: list[str], env: dict, role: str = "ioc") -> Child:
        log_path = self.run_dir / "logs" / f"{name}.log"
        cmd = [sys.executable, "-u", *args]
        env = dict(env, **{RUN_ENV: str(self.run_dir.resolve())})
        fh = open(log_path, "ab")
        kwargs = dict(cwd=str(REPO_ROOT), env=env, stdin=subprocess.PIPE, stdout=fh,
                      stderr=subprocess.STDOUT)
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)
        fh.close()
        self._job.assign(proc)
        child = Child(name, proc, cmd, log_path, role)
        self.children.append(child)
        self._write_pids()
        log.info("started %s (pid %d)", name, proc.pid)
        return child

    def start_ioc(self, name: str, folder: str, ioc: str, env: dict, role: str = "ioc") -> Child:
        return self.start(name, ["master_ioc.py", "-s", str(folder), "-i", ioc], env, role)

    def dead(self) -> list[Child]:
        return [c for c in self.children if not c.alive]

    def stop(self, names: list[str] | None = None, timeout: float = 6.0) -> None:
        """exit() over stdin, then terminate, then kill -- in reverse start order."""
        targets = [c for c in reversed(self.children) if names is None or c.name in names]
        for c in targets:
            if c.alive and c.proc.stdin:
                try:
                    c.proc.stdin.write(b"exit()\n")
                    c.proc.stdin.flush()
                except OSError:
                    pass
        deadline = time.time() + timeout
        for c in targets:
            try:
                c.proc.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                self._terminate(c)
        for c in targets:
            try:
                c.proc.wait(3)
            except subprocess.TimeoutExpired:
                c.proc.kill()
            if c.proc.stdin:
                try:
                    c.proc.stdin.close()
                except OSError:
                    pass
        self._write_pids()

    def _terminate(self, c: Child) -> None:
        try:
            if IS_WINDOWS:
                c.proc.terminate()
            else:
                os.killpg(os.getpgid(c.proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    def _write_pids(self) -> None:
        data = [{"name": c.name, "pid": c.proc.pid, "role": c.role, "cmd": c.cmd,
                 "alive": c.alive, "returncode": c.proc.returncode, "log": str(c.log_path)}
                for c in self.children]
        (self.run_dir / "state" / "pids.json").write_text(json.dumps(data, indent=2),
                                                          encoding="utf-8")


def stop_run(run_dir: Path) -> list[str]:
    """Kill leftover processes of a run (e.g. after a crash).  Only PIDs whose
    current command line or COOLDOWN_SIM_RUN environment still names the run
    directory are signalled."""
    import psutil
    run_dir = Path(run_dir).resolve()
    marker, marker_alt = str(run_dir), str(run_dir).replace("\\", "/")
    pids_file = run_dir / "state" / "pids.json"
    stopped = []
    if not pids_file.exists():
        return stopped
    for entry in json.loads(pids_file.read_text(encoding="utf-8")):
        try:
            proc = psutil.Process(entry["pid"])
            cmd = " ".join(proc.cmdline())
        except Exception:      # noqa: BLE001 - gone or not ours
            continue
        try:
            tagged = proc.environ().get(RUN_ENV) == marker
        except Exception:      # noqa: BLE001 - environ not readable
            tagged = False
        if tagged or marker in cmd or marker_alt in cmd:
            proc.kill()
            stopped.append(f"{entry['name']} (pid {entry['pid']})")
    return stopped
