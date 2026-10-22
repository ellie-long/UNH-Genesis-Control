"""Operator lifecycle for the Genesis read-only master archiver.

  python -m archive.master_service start|status|stop

Only the recorder process is managed. No subsystem IOC or hardware is started,
stopped or written by this module. All persistent state lives outside Git.
"""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone

from archive.data_paths import data_root, master_root, ensure_data_directories

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = data_root() / "runtime"
PID_FILE = RUNTIME / "master-archive.pid"
LOCK_FILE = RUNTIME / "master-archive.lock"
STATUS_FILE = RUNTIME / "master-archive-status.json"
LOG_FILE = data_root() / "logs" / "master-archive.log"
MIN_FREE_BYTES = 2 * 1024 ** 3


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_info():
    def query(*args):
        try:
            return subprocess.check_output(["git", "-C", str(ROOT), *args],
                                           stderr=subprocess.DEVNULL, text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"
    return query("rev-parse", "HEAD"), query("branch", "--show-current")


def git_dirty():
    """Return True for modified/untracked source, None if unknown."""
    try:
        result = subprocess.run(
            [
                "git", "-C", str(ROOT),
                "status", "--porcelain",
                "--untracked-files=normal",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    return bool(result.stdout.strip())


def alive(pid):
    """Never signal an unrelated process after PID reuse."""
    try:
        cmd = Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\0", b" ")
    except (OSError, ValueError):
        return False
    return b"archive.master_service" in cmd and b" run" in cmd


def read_pid():
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def save_status(status):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    temp = STATUS_FILE.with_name(STATUS_FILE.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(status, indent=2) + "\n")
    temp.replace(STATUS_FILE)


def ca_addresses():
    """Resolve physical Genesis CA endpoints without starting hardware.

    Explicit operator overrides retain precedence. Automatic discovery uses
    physical Cryo, Microwave, and ssRF configurations only.
    """
    explicit = os.getenv("GENESIS_MASTER_CA_ADDR_LIST", "").strip()
    if explicit:
        return explicit

    inherited = os.getenv("EPICS_CA_ADDR_LIST", "").strip()
    if inherited:
        return inherited

    import yaml

    addresses = set()

    manager_paths = (
        "configs/target_physical/ioc_manager/settings.yaml",
        "configs/commissioning/microwave_physical/ioc_manager/settings.yaml",
    )

    for relative in manager_paths:
        config = yaml.safe_load(
            (ROOT / relative).read_text(encoding="utf-8")
        ) or {}

        for entry in config.values():
            if not isinstance(entry, dict):
                continue

            for key in ("ca_server_port", "manager_ca_server_port"):
                port = entry.get(key)
                if port is not None:
                    addresses.add(f"127.0.0.1:{int(port)}")

            configured = entry.get("epics_addr_list")
            if isinstance(configured, str) and configured.lower() != "none":
                addresses.update(configured.split())

    ssrf_path = ROOT / "configs/ssrf_physical/archiver/settings.yaml"
    ssrf = yaml.safe_load(ssrf_path.read_text(encoding="utf-8")) or {}
    configured = (ssrf.get("general") or {}).get("epics_addr_list")

    if not isinstance(configured, str) or not configured.strip():
        raise RuntimeError("Missing physical ssRF CA address configuration")

    addresses.update(configured.split())

    # Physical ssRF archive IOC, started on CA port 5092 by
    # scripts/start_ssrf_physical.sh. The fitter uses port 5100.
    addresses.add("127.0.0.1:5092")

    if not addresses:
        raise RuntimeError("No physical Genesis CA addresses configured")

    return " ".join(sorted(addresses))


def free_bytes(path):
    usage = shutil.disk_usage(path)
    return usage.free


async def record_forever(interval, min_free_bytes):
    from archive.master_registry import build_registry
    from archive.master_recorder import MasterRecorder
    from archive.master_ca import MasterCACollector
    from archive.master_runtime import MasterRuntime

    registry, report = build_registry(root=ROOT)
    if not registry:
        raise RuntimeError("Master PV registry is empty")
    version, branch = git_info()
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    session = master_root() / (f"{stamp}_{version[:12]}_{os.getpid()}")
    session.mkdir(parents=True, exist_ok=False)
    config = (ROOT / "archive" / "master_sources.yaml").read_bytes()
    details = {
        "schema_version": 1,
        "started_utc": utc_now(),
        "git_sha": version,
        "git_branch": branch,
        "git_dirty": git_dirty(),
        "host": os.uname().nodename,
        "snapshot_interval_s": interval,
        "pv_count": len(registry),
        "master_sources_sha256": hashlib.sha256(config).hexdigest(),
        "ca_addresses": os.environ["EPICS_CA_ADDR_LIST"],
        "registry_report": report,
    }
    (session / "session.json").write_text(json.dumps(details, indent=2, default=str) + "\n")
    recorder = MasterRecorder(session, registry, interval_s=interval)
    collector = MasterCACollector(recorder)
    runtime = MasterRuntime(recorder, collector)
    task = asyncio.create_task(runtime.run())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:
            pass
    failure = None
    status = {"state": "running", "pid": os.getpid(), "session": str(session),
              "git_sha": version, "started_utc": details["started_utc"],
              "configured_pvs": len(registry)}
    try:
        while not task.done():
            available = free_bytes(data_root())
            status.update(runtime.health())
            status.update({"free_bytes": available, "updated_utc": utc_now()})
            save_status(status)
            if available < min_free_bytes:
                failure = f"Low disk space: {available} bytes free"
                task.cancel()
            await asyncio.sleep(3)
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            failure = str(exc)
            raise
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        status.update(runtime.health())
        status.update({"state": "error" if failure else "stopped",
                       "reason": failure, "updated_utc": utc_now()})
        save_status(status)
    if failure:
        raise RuntimeError(failure)


def serve(interval, free_gib):
    ensure_data_directories()
    with LOCK_FILE.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Master recorder already running") from None
        PID_FILE.write_text(str(os.getpid()) + "\n")
        try:
            os.environ["EPICS_CA_ADDR_LIST"] = ca_addresses()
            os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
            os.environ.setdefault("EPICS_CA_MAX_ARRAY_BYTES", "262144")
            asyncio.run(record_forever(interval, int(free_gib * 1024 ** 3)))
        finally:
            PID_FILE.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "run", "status", "stop"))
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--min-free-gib", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.command == "status":
        pid = read_pid()
        if pid and alive(pid):
            print(f"[UP] master recorder PID {pid}")
            if STATUS_FILE.exists():
                print(STATUS_FILE.read_text().strip())
            return 0
        print("[DOWN] master recorder")
        return 1
    if args.command == "stop":
        pid = read_pid()
        if not pid or not alive(pid):
            print("[DOWN] no master recorder to stop")
            return 0
        os.kill(pid, signal.SIGTERM)
        print(f"[STOP REQUESTED] master recorder PID {pid}")
        return 0
    if args.interval < 1.0 or args.min_free_gib <= 0:
        parser.error("Interval >=1 second and min-free-gib >0 are required")
    if args.command == "run":
        serve(args.interval, args.min_free_gib)
        return 0
    pid = read_pid()
    if pid and alive(pid):
        print(f"[UP] master recorder already running PID {pid}")
        return 0
    RUNTIME.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with LOG_FILE.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "archive.master_service", "run",
             "--interval", str(args.interval), "--min-free-gib", str(args.min_free_gib)],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    import time
    for _ in range(50):
        if process.poll() is not None:
            print(f"[FAIL] master recorder exited; inspect {LOG_FILE}")
            return 1
        if read_pid() == process.pid and alive(process.pid):
            print(f"[UP] master recorder PID {process.pid}; log: {LOG_FILE}")
            return 0
        time.sleep(0.1)
    print(f"[FAIL] master recorder did not confirm startup; inspect {LOG_FILE}")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
