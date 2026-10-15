"""``run``, ``ctl``, ``validate`` and ``stop`` subcommands.

``run --mode regression``  unattended: plays start -> start+duration at a fixed
                           speed, then validates and writes a PASS/FAIL report.
``run --mode interactive`` rehearsal: everything above plus the Sim Control
                           IOC (UNH:GEN:SIM); starts PAUSED so the operator can
                           open the screens (``python -m cooldown_sim phoebus``)
                           and press Run.  Ends on End Session, ``ctl stop`` or
                           Ctrl+C, then writes the same report.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import platform
import sys
import time
from pathlib import Path

import yaml

from . import DATA_ENV, channel_map, guard, ports, profile, pvlist, run_dir
from .clock import SimClock
from .data import records
from .data.timeline import Timeline
from .launcher import IS_WINDOWS, ProcessGroup, stop_run

log = logging.getLogger("cooldown_sim")

DEVICE_START_TIMEOUT_S = 45.0
LOGIC_START_TIMEOUT_S = 45.0
LEGS_AVAILABLE = {"cryo"}


def add_parsers(sub) -> None:
    p = sub.add_parser("run", help="run a simulated cool-down")
    p.add_argument("--mode", choices=["regression", "interactive"], default="regression")
    p.add_argument("--source", choices=["real"], default="real")
    p.add_argument("--cooldown", help="e.g. 2026-06 (directory under $%s)" % DATA_ENV)
    p.add_argument("--data", default=os.environ.get(DATA_ENV))
    p.add_argument("--start", required=True,
                   help="sim start, lab-local: 'YYYY-MM-DD HH:MM' (or HH:MM with --day)")
    p.add_argument("--day", help="YYYY-MM-DD, combined with --start HH:MM")
    p.add_argument("--duration", type=float, default=None,
                   help="sim minutes (regression default 60; interactive: until ended)")
    p.add_argument("--speed", type=float, default=60.0)
    p.add_argument("--legs", default="cryo", help="comma list")
    p.add_argument("--scenario", default="nominal")
    p.add_argument("--autostart", action="store_true", help="interactive: start running immediately")
    p.add_argument("--port-offset", type=int, default=0)
    p.add_argument("--noise-rel", type=float, default=2e-5)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=_run)

    p = sub.add_parser("ctl", help="control a running session (pause, play, speed, seek, fault ...)")
    p.add_argument("command", help="status | play | pause | speed X | seek 'YYYY-MM-DD HH:MM' | "
                                   "jump MIN | fault INSTRUMENT MODE | autopilot LEG on|off | "
                                   "note TEXT | mark | model KEY VALUE | stop")
    p.add_argument("args", nargs="*")
    p.add_argument("--run", help="run directory (default: latest)")
    p.set_defaults(func=_ctl)

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
    with open(Path(spec.folder) / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)[spec.ioc]["port"]


def controller_profile(run_path: Path, stacks: dict, offset: int) -> tuple[Path, dict]:
    """Settings folder for the Sim Control IOC inside the run directory."""
    ctl = stacks["legs"]["sim"]["controller"]
    folder = run_path / "profiles" / "sim" / "controller"
    folder.mkdir(parents=True, exist_ok=True)
    settings = {
        "general": {"prefix": ctl["prefix"], "log_dir": "logs", "epics_addr_list": "None", "delay": 0.5},
        "controller": {"module": "cooldown_sim.controller", "delay": 0.5,
                       "control_port": int(ctl["control_port"]) + offset, "history_points": 240},
    }
    with open(folder / "settings.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(settings, fh, sort_keys=False)
    return folder, {"prefix": ctl["prefix"], "ca": int(ctl["ca"]) + offset,
                    "pva": int(ctl["pva"]) + offset, "control_port": int(ctl["control_port"]) + offset}


def build_provider(args, timeline, cmap, clock):
    """Value provider for this session (closed-loop model added in phase B2)."""
    return None


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
    unsupported = set(legs) - LEGS_AVAILABLE
    if unsupported:
        print(f"legs not available in this build: {sorted(unsupported)}")
        return EXIT_SETUP
    if not args.cooldown:
        print("--cooldown is required for --source real")
        return EXIT_SETUP

    cmap = channel_map.load()
    t0 = parse_start(args.start, args.day)
    duration = args.duration if args.duration is not None else (
        60.0 if args.mode == "regression" else 24 * 60.0)
    t1 = t0 + 60.0 * duration
    try:
        timeline, cd = load_timeline(args.cooldown, args.data, t0, t1, cmap)
    except (FileNotFoundError, SystemExit) as exc:
        print(exc)
        return EXIT_SETUP
    if args.mode == "interactive":
        t1 = min(t1, timeline.end)

    run = run_dir.create(args.mode, args.scenario)
    stacks = ports.load_stacks()
    stack = profile.generate(run.path, legs, offset=args.port_offset)
    from .validation import save_stack
    save_stack(stack)
    ctl_folder, ctl = controller_profile(run.path, stacks, args.port_offset)
    needed = {s.ca for s in stack.iocs} | {s.pva for s in stack.iocs}
    needed |= {int(_settings_port(s)) for s in stack.iocs if s.emulator}
    needed |= {ctl["ca"], ctl["pva"], ctl["control_port"]}
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
                       args={k: v for k, v in vars(args).items() if k != "func"}, meta=meta,
                       controller=ctl)
    print(f"run directory: {run.path}")
    _route_native_stderr(run.sub("logs") / "runner.log")
    return asyncio.run(_session(args, run, stack, stacks, timeline, cmap, t0, t1, meta,
                                ctl_folder, ctl))


async def _session(args, run, stack, stacks, timeline, cmap, t0, t1, meta, ctl_folder, ctl) -> int:
    from .__main__ import EXIT_FAIL, EXIT_OK, EXIT_SETUP
    from .control import ControlServer
    from .engine import SimEngine
    from .epics_env import apply_client_env_here, server_env

    interactive = args.mode == "interactive"
    clock = SimClock(t0, args.speed, running=False)
    engine = SimEngine(run, stack, timeline, cmap, clock, noise_rel=args.noise_rel, seed=args.seed,
                       provider=build_provider(args, timeline, cmap, clock))
    engine.info.update({"mode": args.mode, "scenario": args.scenario, "source": meta["source"],
                        "window": meta["window"], "run_dir": str(run.path)})
    ca_ports = sorted({s.ca for s in stack.iocs} | {ctl["ca"]})
    apply_client_env_here(ca_ports, stacks)          # before aioca is imported
    from .autopilot.ca import RestrictedCA
    from .probe import Probe

    control = ControlServer(ctl["control_port"], engine.handle).start()
    group = ProcessGroup(run.path)
    skipped: dict[str, str] = {}
    deaths: list[str] = []
    probe = None
    interrupted = False
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
                group.start_ioc(s.name, s.folder, s.ioc, server_env(s.ca, s.pva, ca_ports, stacks),
                                role=phase)
                started.append(s)
            missing = await _wait_connected([f"{s.prefix}:MAN:{s.ioc}_time" for s in started], timeout)
            dead = group.dead()
            if missing or dead:
                for c in dead:
                    print(f"[FAIL] {c.name} exited:\n{_tail(c.log_path)}")
                print(f"[FAIL] {phase} IOCs not reachable: {missing}")
                return EXIT_SETUP
            print(f"[ok] {len(started)} {phase} IOC(s) up")
        if interactive:
            group.start_ioc("sim_controller", str(ctl_folder), "controller",
                            server_env(ctl["ca"], ctl["pva"], ca_ports, stacks), role="controller")
            missing = await _wait_connected([f"{ctl['prefix']}:MAN:controller_time"], 30.0)
            if missing:
                print(f"[FAIL] Sim Control IOC not reachable:\n{_tail(run.sub('logs') / 'sim_controller.log')}")
                return EXIT_SETUP
            print(f"[ok] Sim Control IOC up ({ctl['prefix']})")

        probe = Probe(pvlist.for_stack(stack), run.path / "probe.csv")
        probe.start()
        ca = RestrictedCA(engine.events, clock)
        if any(s.leg == "cryo" and s.ioc == "archiver" for s in stack.iocs):
            await ca.caput("UNH:GEN:CRYO:ARCH:Archive_Enable", 1)
        await asyncio.sleep(3.0)                     # first polls at the start time

        if interactive:
            print(f"\nINTERACTIVE SESSION ready at {records.format_epoch(t0)} "
                  f"({'running' if args.autostart else 'PAUSED'}, {args.speed:g}x).")
            print("  Screens:  python -m cooldown_sim phoebus")
            print("  Control:  python -m cooldown_sim ctl status|play|pause|speed 60|fault ls224_fridge silent|stop")
            print("  End with End Session on Sim Control, 'ctl stop', or Ctrl+C.\n")
            if args.autostart:
                engine.play()
        else:
            engine.play()
            print(f"[run] {records.format_epoch(t0)} -> {records.format_epoch(t1)} at {args.speed:g}x "
                  f"(~{(t1 - t0) / args.speed / 60:.1f} wall min)")

        last_print = 0.0
        while not engine.stop_requested.is_set():
            if clock.now() >= t1:
                if not interactive:
                    break
                if clock.running:
                    engine.pause()
                    engine.last_event = "end of the recorded window: paused"
                    print("[end of record window] paused; seek back or End Session")
            await asyncio.sleep(1.0)
            for c in group.dead():
                if not any(d.startswith(f"{c.name}:") for d in deaths):
                    deaths.append(f"{c.name}: exit {c.proc.returncode}\n{_tail(c.log_path)}")
                    print(f"[FAIL] {c.name} died")
            if time.time() - last_print > (60 if interactive else 15):
                last_print = time.time()
                print(f"  sim {records.format_epoch(clock.now())} "
                      f"({'running' if clock.running else 'paused'}, {clock.speed:g}x)  "
                      f"PVs connected {len(probe.connected)}/{len(probe.pvs)}")
        if clock.running:
            engine.pause()
        await asyncio.sleep(2.0)
    except (KeyboardInterrupt, asyncio.CancelledError):
        interrupted = True
        print("\n[interrupted] stopping the session")
    finally:
        never = probe.never_connected() if probe else []
        if probe:
            probe.close()
        group.stop()
        control.stop()
        runtime = {"deaths": deaths, "skipped": skipped, "never_connected": never,
                   "read_only_violations": engine.host.read_only_violations(),
                   "clamps": engine.host.clamp_counts(), "interrupted": interrupted}
        engine.stop()
        (run.sub("state") / "runtime.json").write_text(json.dumps(runtime, indent=2), encoding="utf-8")

    from .validation import run_all
    t_end = max(c.sim for c in clock.checkpoints())
    verdict, md, html, checks = run_all(run.path, stack, runtime=runtime, timeline=timeline,
                                        t0=t0, t1=max(t_end, t0 + 60.0), meta=meta)
    print(f"\n{verdict}: {md}")
    if html:
        print(f"channel map cross-check: {html}")
    for c in checks:
        if c.verdict in ("FAIL", "KNOWN", "SKIPPED"):
            print(f"  {c.verdict:7s} {c.name}: {c.summary}")
    return EXIT_OK if verdict == "PASS" else EXIT_FAIL


# ------------------------------------------------------------ ctl / validate / stop
def _resolve_run(arg) -> Path | None:
    return Path(arg).resolve() if arg else run_dir.latest()


def _ctl(args) -> int:
    from .control import ControlClient
    from .timeparse import parse_lab_time
    run = _resolve_run(args.run)
    if run is None:
        print("no run directory")
        return 3
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    port = manifest.get("controller", {}).get("control_port")
    if not port:
        print(f"{run} has no control channel")
        return 3
    cmd, a = args.command, args.args
    req: dict = {}
    if cmd == "speed":
        req = {"value": float(a[0])}
    elif cmd == "seek":
        req = {"value": parse_lab_time(" ".join(a))}
    elif cmd == "jump":
        req = {"minutes": float(a[0])}
    elif cmd == "fault":
        req = {"instrument": a[0], "mode": a[1] if len(a) > 1 else "none"}
    elif cmd == "autopilot":
        req = {"leg": a[0], "on": a[1].lower() in ("on", "1", "true")}
    elif cmd in ("note", "mark"):
        req = {"text": " ".join(a)}
    elif cmd == "model":
        req = {"key": a[0], "value": float(a[1])}
    try:
        client = ControlClient(int(port))
    except OSError:
        print(f"no session is running for {run.name}")
        return 3
    try:
        reply = client.call(cmd, **req)
    finally:
        client.close()
    if not reply.get("ok"):
        print(f"refused: {reply.get('error')}")
        return 1
    st = reply["status"]
    print(f"{st['sim_local']}  {'RUNNING' if st['running'] else 'PAUSED'} {st['speed']:g}x  "
          f"[{st.get('mode', '?')}/{st.get('scenario', '?')}]  last: {st.get('last_event', '')}")
    faults = {k: v for k, v in st.get("faults", {}).items() if v != "none"}
    if faults:
        print("faults: " + ", ".join(f"{k}={v}" for k, v in faults.items()))
    if st.get("truth"):
        print("truth: " + ", ".join(f"{k}={v:.4g}" for k, v in st["truth"].items()
                                     if isinstance(v, (int, float))))
    return 0


def _validate(args) -> int:
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
        timeline, _cd = load_timeline(src["cooldown"], None, src["t0"], src["t1"], channel_map.load())
    verdict, md, _html, _checks = run_all(run, stack, runtime=runtime, timeline=timeline,
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
