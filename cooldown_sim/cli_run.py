"""``run``, ``validate`` and ``stop`` subcommands."""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import platform
import sys
import time
from pathlib import Path

from . import DATA_ENV, channel_map, guard, ports, profile, pvlist, run_dir
from .clock import SimClock
from .data import records
from .data.timeline import Timeline
from .launcher import IS_WINDOWS, ProcessGroup, stop_run

log = logging.getLogger("cooldown_sim")

DEVICE_START_TIMEOUT_S = 45.0
LOGIC_START_TIMEOUT_S = 45.0


def add_parsers(sub) -> None:
    p = sub.add_parser("run", help="run a simulated cool-down")
    p.add_argument("--mode", choices=["regression"], default="regression",
                   help="regression: unattended, ends with a PASS/FAIL report")
    p.add_argument("--source", choices=["real"], default="real")
    p.add_argument("--cooldown", help="e.g. 2026-06 (directory under $%s)" % DATA_ENV)
    p.add_argument("--data", default=os.environ.get(DATA_ENV))
    p.add_argument("--start", required=True,
                   help="sim start, lab-local: 'YYYY-MM-DD HH:MM' (or HH:MM with --day)")
    p.add_argument("--day", help="YYYY-MM-DD, combined with --start HH:MM")
    p.add_argument("--duration", type=float, default=60.0, help="sim minutes to run (default 60)")
    p.add_argument("--speed", type=float, default=60.0)
    p.add_argument("--legs", default="cryo", help="comma list; this build: cryo")
    p.add_argument("--scenario", default="nominal")
    p.add_argument("--port-offset", type=int, default=0)
    p.add_argument("--noise-rel", type=float, default=2e-5)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=_run)

    p = sub.add_parser("validate", help="re-validate an existing run directory")
    p.add_argument("run", nargs="?", help="run directory (default: latest)")
    p.set_defaults(func=_validate)

    p = sub.add_parser("stop", help="kill leftover processes of a run")
    p.add_argument("run", nargs="?", help="run directory (default: latest)")
    p.set_defaults(func=_stop)


# ------------------------------------------------------------------- helpers
def parse_start(start: str, day: str | None) -> float:
    text = f"{day} {start}" if day and len(start) <= 5 else start
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(text, fmt).replace(tzinfo=records.lab_tz()).timestamp()
        except ValueError:
            continue
    raise SystemExit(f"cannot parse start time {text!r} (use 'YYYY-MM-DD HH:MM')")


def load_timeline(cooldown: str, data_root, t0: float, t1: float, cmap) -> tuple[Timeline, Path]:
    cd = records.resolve_cooldown(cooldown, data_root)
    cols = sorted({c for spec in cmap.all_specs() for c in spec.source_columns})
    table = records.read_cooldown(cd, cols, start=t0 - 3600.0, stop=t1 + 600.0)
    if not len(table):
        raise SystemExit(f"{cd.name} has no rows between {records.format_epoch(t0)} "
                         f"and {records.format_epoch(t1)}")
    return Timeline(table), cd


async def _wait_connected(pvs: list[str], timeout: float) -> list[str]:
    from aioca import caget
    deadline = time.time() + timeout
    pending = list(pvs)
    while pending and time.time() < deadline:
        still = []
        for pv in pending:
            try:
                await caget(pv, timeout=1.0)
            except Exception:  # noqa: BLE001
                still.append(pv)
        pending = still
        if pending:
            await asyncio.sleep(0.5)
    return pending


def _tail(path: Path, n: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


# ----------------------------------------------------------------------- run
def _run(args) -> int:
    from .__main__ import EXIT_REFUSED, EXIT_SETUP
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    refusals = guard.check()
    if refusals:
        print("REFUSED - a simulated cool-down must not start here:")
        for r in refusals:
            print(f"  {r}")
        return EXIT_REFUSED
    legs = tuple(x.strip() for x in args.legs.split(",") if x.strip())
    unsupported = set(legs) - {"cryo"}
    if unsupported:
        print(f"legs not available in this build: {sorted(unsupported)}")
        return EXIT_SETUP
    if not args.cooldown:
        print("--cooldown is required for --source real")
        return EXIT_SETUP

    cmap = channel_map.load()
    t0 = parse_start(args.start, args.day)
    t1 = t0 + 60.0 * args.duration
    try:
        timeline, cd = load_timeline(args.cooldown, args.data, t0, t1, cmap)
    except (FileNotFoundError, SystemExit) as exc:
        print(exc)
        return EXIT_SETUP

    run = run_dir.create(args.mode, args.scenario)
    stacks = ports.load_stacks()
    stack = profile.generate(run.path, legs, offset=args.port_offset)
    from .validation import save_stack
    save_stack(stack)
    needed = {s.ca for s in stack.iocs} | {s.pva for s in stack.iocs}
    needed |= {int(_settings_port(s)) for s in stack.iocs if s.emulator}
    taken = ports.unbindable_ports(needed)
    if taken:
        print("replay ports unavailable (try --port-offset):")
        for port, why in taken.items():
            print(f"  {port}: {why}")
        return EXIT_SETUP

    meta = {"mode": args.mode, "scenario": args.scenario, "source": f"real {cd.name}",
            "window": f"{records.format_epoch(t0)} -> {records.format_epoch(t1)}",
            "speed": args.speed, "legs": ",".join(legs),
            "platform": f"{platform.system()} {platform.release()}",
            "git": run.meta.get("git", {}).get("head", "")[:12]}
    run.write_manifest(source={"kind": "real", "cooldown": str(cd), "t0": t0, "t1": t1},
                       args=vars(args) | {"func": None}, meta=meta)
    print(f"run directory: {run.path}")
    _route_native_stderr(run.sub("logs") / "runner.log")
    return asyncio.run(_regression(args, run, stack, stacks, timeline, cmap, t0, t1, meta))


def _route_native_stderr(path: Path) -> None:
    """Send C-level stderr (libca connection chatter, the missing-caRepeater
    notice) and log records to the run's runner.log; keep Python's own
    sys.stderr on the console for real tracebacks."""
    try:
        console = os.dup(2)
        fh = open(path, "ab")
        os.dup2(fh.fileno(), 2)
        sys.stderr = os.fdopen(console, "w", buffering=1)
    except OSError:
        pass


def _settings_port(spec) -> int:
    import yaml
    with open(Path(spec.folder) / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)[spec.ioc]["port"]


async def _regression(args, run, stack, stacks, timeline, cmap, t0, t1, meta) -> int:
    from .__main__ import EXIT_FAIL, EXIT_OK, EXIT_SETUP
    from .engine import SimEngine
    from .epics_env import apply_client_env_here, server_env

    clock = SimClock(t0, args.speed, running=False)
    engine = SimEngine(run, stack, timeline, cmap, clock, noise_rel=args.noise_rel, seed=args.seed)
    ca_ports = sorted(s.ca for s in stack.iocs)
    apply_client_env_here(ca_ports, stacks)          # before aioca is imported
    from .autopilot.ca import RestrictedCA
    from .probe import Probe

    group = ProcessGroup(run.path)
    skipped: dict[str, str] = {}
    deaths: list[str] = []
    probe = None
    try:
        engine.start()
        devices = [s for s in stack.iocs if s.emulator or s.simulator]
        logic_iocs = [s for s in stack.iocs if not (s.emulator or s.simulator)]
        for phase, specs, timeout in (("device", devices, DEVICE_START_TIMEOUT_S),
                                      ("logic", logic_iocs, LOGIC_START_TIMEOUT_S)):
            started = []
            for s in specs:
                if s.posix_only and IS_WINDOWS:
                    skipped[s.name] = "POSIX-only IOC (imports fcntl/pwd) skipped on native Windows"
                    continue
                env = server_env(s.ca, s.pva, ca_ports, stacks)
                group.start_ioc(s.name, s.folder, s.ioc, env, role=phase)
                started.append(s)
            missing = await _wait_connected([f"{s.prefix}:MAN:{s.ioc}_time" for s in started], timeout)
            dead = group.dead()
            if missing or dead:
                for c in dead:
                    print(f"[FAIL] {c.name} exited:\n{_tail(c.log_path)}")
                print(f"[FAIL] {phase} IOCs not reachable: {missing}")
                return EXIT_SETUP
            print(f"[ok] {len(started)} {phase} IOC(s) up")

        probe = Probe(pvlist.for_stack(stack), run.path / "probe.csv")
        probe.start()
        ca = RestrictedCA(engine.events, clock)
        if any(s.leg == "cryo" and s.ioc == "archiver" for s in stack.iocs):
            await ca.caput("UNH:GEN:CRYO:ARCH:Archive_Enable", 1)
        await asyncio.sleep(3.0)                     # first polls at the start time
        engine.play()
        print(f"[run] {records.format_epoch(t0)} -> {records.format_epoch(t1)} at {args.speed:g}x "
              f"(~{(t1 - t0) / args.speed / 60:.1f} wall min)")
        last_print = 0.0
        while clock.now() < t1:
            await asyncio.sleep(1.0)
            for c in group.dead():
                if c.name not in [d.split(":")[0] for d in deaths]:
                    deaths.append(f"{c.name}: exit {c.proc.returncode}\n{_tail(c.log_path)}")
                    print(f"[FAIL] {c.name} died")
            if time.time() - last_print > 15:
                last_print = time.time()
                print(f"  sim {records.format_epoch(clock.now())}  "
                      f"PVs connected {len(probe.connected)}/{len(probe.pvs)}")
        engine.pause()
        await asyncio.sleep(2.0)
    finally:
        never = probe.never_connected() if probe else []
        if probe:
            probe.close()
        group.stop()
        runtime = {"deaths": deaths, "skipped": skipped, "never_connected": never,
                   "read_only_violations": engine.host.read_only_violations(),
                   "clamps": engine.host.clamp_counts()}
        engine.stop()
        import json
        (run.sub("state") / "runtime.json").write_text(json.dumps(runtime, indent=2), encoding="utf-8")

    from .validation import run_all
    verdict, md, html, checks = run_all(run.path, stack, runtime=runtime, timeline=timeline,
                                        t0=t0, t1=t1, meta=meta)
    print(f"\n{verdict}: {md}")
    if html:
        print(f"channel map cross-check: {html}")
    for c in checks:
        if c.verdict in ("FAIL", "KNOWN", "SKIPPED"):
            print(f"  {c.verdict:7s} {c.name}: {c.summary}")
    return EXIT_OK if verdict == "PASS" else EXIT_FAIL


# ------------------------------------------------------------ validate / stop
def _resolve_run(arg) -> Path | None:
    return Path(arg).resolve() if arg else run_dir.latest()


def _validate(args) -> int:
    import json
    from .__main__ import EXIT_FAIL, EXIT_OK, EXIT_SETUP
    from .validation import load_stack, run_all
    run = _resolve_run(args.run)
    if run is None:
        print("no run directory")
        return EXIT_SETUP
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    runtime = json.loads((run / "state" / "runtime.json").read_text(encoding="utf-8"))
    stack = load_stack(run)
    src = manifest.get("source", {})
    timeline = None
    if src.get("kind") == "real":
        cmap = channel_map.load()
        timeline, _cd = load_timeline(src["cooldown"], None, src["t0"], src["t1"], cmap)
    verdict, md, html, _checks = run_all(run, stack, runtime=runtime, timeline=timeline,
                                         t0=src.get("t0"), t1=src.get("t1"),
                                         meta=manifest.get("meta", {}))
    print(f"{verdict}: {md}")
    return EXIT_OK if verdict == "PASS" else EXIT_FAIL


def _stop(args) -> int:
    run = _resolve_run(args.run)
    if run is None:
        print("no run directory")
        return 3
    stopped = stop_run(run)
    print("stopped: " + (", ".join(stopped) if stopped else "nothing left running"))
    return 0
