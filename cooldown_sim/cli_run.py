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

from . import DATA_ENV, REPO_ROOT, channel_map, guard, ports, profile, pvlist, run_dir
from .clock import SimClock
from .data import records
from .data.timeline import Timeline
from .launcher import IS_WINDOWS, ProcessGroup, stop_run

log = logging.getLogger("cooldown_sim")

DEVICE_START_TIMEOUT_S = 45.0
LOGIC_START_TIMEOUT_S = 45.0
LEGS_AVAILABLE = {"cryo", "nmr", "mw", "ssrf"}
#: equilibrium sweeps the fitter autopilot waits for before Update Constants
FIT_AUTOPILOT_SWEEPS = 40
#: ...and the polarization (%, model truth) it waits for: like an operator, it
#: calibrates on a well-polarized line -- Update Constants takes P from the line
#: asymmetry, which is nearly flat at a few percent (constants come out far off)
FIT_AUTOPILOT_MIN_P = 10.0
#: legs that are not IOC stacks in stacks.yaml (launched by the session itself)
SESSION_LEGS = {"nmr"}


def add_parsers(sub) -> None:
    p = sub.add_parser("run", help="run a simulated cool-down")
    p.add_argument("--mode", choices=["regression", "interactive"], default="regression")
    p.add_argument("--source", choices=["real", "synthetic"], default="real",
                   help="real: a past cool-down under $%s; synthetic: a seeded designed day "
                        "(no data needed)" % DATA_ENV)
    p.add_argument("--faults", default="",
                   help="synthetic record faults, comma list: data_gap, glitches")
    p.add_argument("--cooldown", help="e.g. 2026-06 (directory under $%s)" % DATA_ENV)
    p.add_argument("--data", default=os.environ.get(DATA_ENV))
    p.add_argument("--start", default=None,
                   help="sim start, lab-local: 'YYYY-MM-DD HH:MM' (or HH:MM with --day); "
                        "synthetic default: the start of the designed day")
    p.add_argument("--day", help="YYYY-MM-DD, combined with --start HH:MM")
    p.add_argument("--duration", type=float, default=None,
                   help="sim minutes (regression default 60; interactive: until ended)")
    p.add_argument("--speed", type=float, default=60.0)
    p.add_argument("--legs", default="cryo", help="comma list")
    p.add_argument("--scenario", default="nominal")
    p.add_argument("--autostart", action="store_true", help="interactive: start running immediately")
    p.add_argument("--autopilot", default=None,
                   help="comma list of autopilot legs to start ON (mw, ssrf, fit); default: "
                        "'fit' for regression runs with the nmr leg, none for interactive")
    p.add_argument("--model", choices=["closed-loop", "replay"], default=None,
                   help="closed-loop target model (interactive default) or plain replay "
                        "(regression default)")
    p.add_argument("--nmr-protected", action="store_true",
                   help="run the online fitter in protected PyNMR mode (needs the ssrf leg for the "
                        "NMR-switch read-back, and pyepics: requirements-replay.txt)")
    p.add_argument("--port-offset", type=int, default=0)
    p.add_argument("--noise-rel", type=float, default=2e-5)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=_run)

    p = sub.add_parser("scenario", help="run scripted scenarios (list | NAME ... | all)")
    p.add_argument("names", nargs="+", help="'list', 'all', or scenario names")
    p.add_argument("--speed", type=float, default=None, help="override the scenario speed")
    p.add_argument("--data", default=os.environ.get(DATA_ENV))
    p.set_defaults(func=_scenario)

    p = sub.add_parser("ctl", help="control a running session (pause, play, speed, seek, fault ...)")
    p.add_argument("command", help="status | play | pause | speed X | seek 'YYYY-MM-DD HH:MM' | "
                                   "jump MIN | fault INSTRUMENT MODE | autopilot LEG on|off | "
                                   "note TEXT | mark | model KEY VALUE | "
                                   "override INSTRUMENT CHANNEL VALUE|record | magic +|- | stop")
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


#: record columns the closed-loop target model reads besides the channel map's
MODEL_COLUMNS = ("T1", "vp_temp", "central_field", "mw_rf_status", "mw_power_db", "mw_freq_ghz",
                 "uca_v", "online_p", "nmr_centfreq", "nmr_span", "nmr_status", "vme_mode")


def load_timeline(cooldown: str, data_root, t0: float, t1: float, cmap) -> tuple[Timeline, Path]:
    cd = records.resolve_cooldown(cooldown, data_root)
    cols = sorted({c for spec in cmap.all_specs() for c in spec.source_columns} | set(MODEL_COLUMNS))
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
    """The (generated, offset) endpoint port of an emulated/simulated instrument."""
    with open(Path(spec.folder) / "settings.yaml", encoding="utf-8") as fh:
        block = yaml.safe_load(fh)[spec.ioc]
    if "resource" in block:                      # TCPIP0::127.0.0.1::<port>::SOCKET
        return int(str(block["resource"]).split("::")[2])
    return int(block["port"])


def simulator_command(spec) -> list[str]:
    sim, port = spec.simulator, str(_settings_port(spec))
    if "module" in sim:
        return ["-m", sim["module"], "--port", port]
    return [sim["tool"], "--host", "127.0.0.1", "--port", port]


async def _wait_ports(ports: list[int], timeout: float) -> list[int]:
    import socket
    deadline = time.time() + timeout
    pending = list(ports)
    while pending and time.time() < deadline:
        still = []
        for p in pending:
            try:
                socket.create_connection(("127.0.0.1", p), timeout=0.5).close()
            except OSError:
                still.append(p)
        pending = still
        if pending:
            await asyncio.sleep(0.3)
    return pending


#: start-up state of emulated supplies (out-of-band, as if set before the window)
SUPPLY_INITIAL = {
    # the operator brings the UCA to 5 V before any microwave work
    # (docs/MICROWAVE_RF_OPERATION.md); the replay starts from there
    "mw_spd1305x": {"voltage": 5.0, "current": 0.1, "output": True, "load_ohms": 1.0e4},
}


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


def _line_strong(engine, t: float) -> bool:
    """True when the target is polarized enough to calibrate on (no model: True)."""
    if not hasattr(engine.provider, "truth"):
        return True
    try:
        return abs(float(engine.provider.truth(t)["P"])) >= FIT_AUTOPILOT_MIN_P
    except Exception:  # noqa: BLE001 - never block the session on a status read
        return True


def model_kind(args) -> str:
    if args.model:
        return args.model
    legs = {x.strip() for x in args.legs.split(",")}
    # the live NMR source needs the target model's P and Q; the microwave leg
    # exists to drive it
    return "closed-loop" if args.mode == "interactive" or legs & {"nmr", "mw", "ssrf"} else "replay"


def build_provider(args, timeline, cmap, clock):
    """Value provider: the closed-loop target model, or None for plain replay."""
    if model_kind(args) != "closed-loop":
        return None
    from .model.provider import ModelProvider
    from .model.target import load_params
    name = _cooldown_name(args)
    return ModelProvider(timeline, cmap, clock, t0=clock.now(), noise_rel=args.noise_rel,
                         seed=args.seed, params=load_params(cooldown=name), cooldown=name)


def _cooldown_name(args) -> str | None:
    """Directory name of the replayed past cool-down (selects its fitted DNP values)."""
    if getattr(args, "source", "real") != "real" or not getattr(args, "cooldown", None):
        return None
    try:
        root = args.data or os.environ.get("GENESIS_REPLAY_DATA")
        return records.resolve_cooldown(args.cooldown, Path(root) if root else None).name
    except Exception:  # noqa: BLE001 - fall back to the default parameters
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
    cmap = channel_map.load()
    duration = args.duration if args.duration is not None else (
        60.0 if args.mode == "regression" else 24 * 60.0)
    burns = None
    if args.nmr_protected:
        import importlib.util
        if IS_WINDOWS:
            # the protected receiver's pyepics client and the POSIX-only producer pin
            print("--nmr-protected runs on Linux/WSL only")
            return EXIT_SETUP
        if "nmr" not in legs or "ssrf" not in legs:
            print("--nmr-protected needs the nmr and ssrf legs (NMR-switch read-back)")
            return EXIT_SETUP
        if importlib.util.find_spec("epics") is None:
            print("--nmr-protected needs pyepics: pip install -r requirements-replay.txt")
            return EXIT_SETUP
    if args.source == "synthetic":
        from .synth import cooldown as synth
        faults = tuple(f.strip() for f in args.faults.split(",") if f.strip())
        made = synth.make(seed=args.seed, faults=faults)
        timeline, cd, burns = made.timeline, None, made.burns
        t0 = parse_start(args.start, args.day) if args.start else made.t0
        t1 = min(t0 + 60.0 * duration, made.t1)
        source_meta = {"kind": "synthetic", "seed": args.seed, "faults": list(faults),
                       "t0": t0, "t1": t1}
        source_label = f"synthetic seed {args.seed}" + (f" faults {','.join(faults)}" if faults else "")
    else:
        if not args.cooldown or not args.start:
            print("--source real needs --cooldown and --start")
            return EXIT_SETUP
        t0 = parse_start(args.start, args.day)
        t1 = t0 + 60.0 * duration
        try:
            timeline, cd = load_timeline(args.cooldown, args.data, t0, t1, cmap)
        except (FileNotFoundError, SystemExit) as exc:
            print(exc)
            return EXIT_SETUP
        source_meta = {"kind": "real", "cooldown": str(cd), "t0": t0, "t1": t1}
        source_label = f"real {cd.name}"
    if args.mode == "interactive":
        t1 = min(t1, timeline.end)

    run = run_dir.create(args.mode, args.scenario)
    stacks = ports.load_stacks()
    spec = getattr(args, "scenario_spec", None) or {}
    stack = profile.generate(run.path, tuple(l for l in legs if l not in SESSION_LEGS),
                             offset=args.port_offset, overrides=spec.get("overrides"))
    from .validation import save_stack
    save_stack(stack)
    ctl_folder, ctl = controller_profile(run.path, stacks, args.port_offset)
    needed = {s.ca for s in stack.iocs} | {s.pva for s in stack.iocs}
    needed |= {int(_settings_port(s)) for s in stack.iocs if s.emulator or s.simulator}
    needed |= {ctl["ca"], ctl["pva"], ctl["control_port"]}
    fitter = {k: int(v) + args.port_offset for k, v in stacks["legs"]["nmr"]["fitter"].items()}
    if "nmr" in legs:
        needed |= {fitter["ca"], fitter["pva"], fitter["fit_port"], fitter["publish_port"]}
    taken = ports.unbindable_ports(needed)
    if taken:
        print("replay ports unavailable (try --port-offset):")
        for port, why in taken.items():
            print(f"  {port}: {why}")
        return EXIT_SETUP

    meta = {"mode": args.mode, "scenario": args.scenario, "source": source_label,
            "window": f"{records.format_epoch(t0)} -> {records.format_epoch(t1)}",
            "speed": args.speed, "legs": ",".join(legs), "model": model_kind(args),
            "platform": f"{platform.system()} {platform.release()}",
            "git": run.meta.get("git", {}).get("head", "")[:12]}
    run.write_manifest(source=source_meta,
                       args={k: v for k, v in vars(args).items() if k != "func"}, meta=meta,
                       controller=ctl, fitter=fitter if "nmr" in legs else None,
                       legs=list(legs))
    print(f"run directory: {run.path}")
    _route_native_stderr(run.sub("logs") / "runner.log")
    return asyncio.run(_session(args, run, stack, stacks, timeline, cmap, t0, t1, meta,
                                ctl_folder, ctl, set(legs), fitter, cd, burns))


async def _session(args, run, stack, stacks, timeline, cmap, t0, t1, meta, ctl_folder, ctl,
                   legs, fitter, cooldown_dir=None, burns=None) -> int:
    from .__main__ import EXIT_FAIL, EXIT_OK, EXIT_SETUP
    from .control import ControlServer
    from .engine import SimEngine
    from .epics_env import apply_client_env_here, server_env

    interactive = args.mode == "interactive"
    clock = SimClock(t0, args.speed, running=False)
    engine = SimEngine(run, stack, timeline, cmap, clock, noise_rel=args.noise_rel, seed=args.seed,
                       provider=build_provider(args, timeline, cmap, clock),
                       supply_initial=SUPPLY_INITIAL)
    spec = getattr(args, "scenario_spec", None)
    if args.autopilot is None and spec is not None and "autopilot" in spec:
        args.autopilot = ",".join(spec["autopilot"] or [])
    if args.autopilot is not None:
        start_on = {x.strip() for x in args.autopilot.split(",") if x.strip()}
    else:
        start_on = set()
        if not interactive:
            start_on |= {"fit"} if "nmr" in legs else set()
            start_on |= {"mw"} if "mw" in legs else set()
            start_on |= {"ssrf"} if "ssrf" in legs else set()
    for leg in start_on & set(engine.autopilot):
        engine.autopilot[leg] = True
    engine.info.update({"mode": f"{args.mode} ({model_kind(args)})", "scenario": args.scenario,
                        "source": meta["source"],
                        "window": meta["window"], "run_dir": str(run.path)})
    ca_ports = sorted({s.ca for s in stack.iocs} | {ctl["ca"]}
                      | ({fitter["ca"]} if "nmr" in legs else set()))
    apply_client_env_here(ca_ports, stacks)          # before aioca is imported
    from .autopilot.ca import RestrictedCA
    from .probe import Probe

    control = ControlServer(ctl["control_port"], engine.handle).start()
    group = ProcessGroup(run.path)
    skipped: dict[str, str] = {}
    deaths: list[str] = []
    probe = None
    feeder = None
    mw_leg = None
    ssrf_leg = None
    ssrf_skipped = ""
    leg_stop = asyncio.Event()
    leg_tasks = []
    player = None
    interrupted = False
    try:
        engine.start()
        sims = [s for s in stack.iocs if s.simulator and not (s.posix_only and IS_WINDOWS)]
        for s in sims:
            group.start(f"sim_{s.name}", simulator_command(s),
                        server_env(0, 0, ca_ports, stacks), role="simulator")
        missing_ports = await _wait_ports([_settings_port(s) for s in sims], 30.0)
        if missing_ports:
            print(f"[FAIL] simulators not listening on {missing_ports}")
            for s in sims:
                print(_tail(run.sub("logs") / f"sim_{s.name}.log"))
            return EXIT_SETUP
        if sims:
            print(f"[ok] {len(sims)} instrument simulator(s) up")
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
        if "nmr" in legs:
            fitter_args = ["-m", "online_fitter.epics_run", "--port", str(fitter["fit_port"]),
                           "--publish-port", str(fitter["publish_port"]),
                           "--material", "d-butanol", "--mode", "equilibrium"]
            if args.nmr_protected:
                # the live protected path, unchanged: spectra on the PyNMR port are
                # fitted only for an armed, pinned PyNMR producer (none exists here)
                fitter_args += ["--nmr-protected", "--nmr-source", "pynmr",
                                "--pynmr-port", str(fitter["pynmr_port"])]
            group.start("online_fitter", fitter_args,
                        server_env(fitter["ca"], fitter["pva"], ca_ports, stacks), role="fitter")
            missing = await _wait_connected(["UNH:GEN:SSRF:FIT:FITTER_UP"], 60.0)
            if missing:
                print(f"[FAIL] online fitter not reachable:\n{_tail(run.sub('logs') / 'online_fitter.log')}")
                return EXIT_SETUP
            print("[ok] online fitter up (UNH:GEN:SSRF:FIT)")
        if interactive:
            group.start_ioc("sim_controller", str(ctl_folder), "controller",
                            server_env(ctl["ca"], ctl["pva"], ca_ports, stacks), role="controller")
            missing = await _wait_connected([f"{ctl['prefix']}:MAN:controller_time"], 30.0)
            if missing:
                print(f"[FAIL] Sim Control IOC not reachable:\n{_tail(run.sub('logs') / 'sim_controller.log')}")
                return EXIT_SETUP
            print(f"[ok] Sim Control IOC up ({ctl['prefix']})")

        probe = Probe(pvlist.for_stack(stack, legs=legs), run.path / "probe.csv")
        probe.start()
        ca = RestrictedCA(engine.events, clock)
        if "mw" in legs:
            from .autopilot.mw import MicrowaveLeg
            mw_leg = MicrowaveLeg(engine, ca)
            mw_leg.start()
            leg_tasks.append(asyncio.create_task(mw_leg.run(leg_stop)))
        if "ssrf" in legs:
            if IS_WINDOWS:
                ssrf_skipped = ("the ssRF sequencer imports fcntl/pwd and cannot run on native "
                                "Windows; burn replay needs Linux/WSL")
            else:
                from .autopilot.ssrf import SSRFLeg
                ssrf_leg = SSRFLeg(engine, ca, cooldown_dir, burns=burns)
                ssrf_leg.start()
                leg_tasks.append(asyncio.create_task(ssrf_leg.run(leg_stop)))
        if spec is not None and spec.get("actions"):
            from .scenario import ScenarioPlayer
            player = ScenarioPlayer(spec, engine, ca, t0)
            leg_tasks.append(asyncio.create_task(player.run(leg_stop)))
        if "nmr" in legs:
            from .nmr.feeder import NMRFeeder
            from .nmr.source import NMRSource
            source = NMRSource(engine.provider.model, timeline, seed=args.seed)
            feed_port = fitter["pynmr_port"] if args.nmr_protected else fitter["fit_port"]
            feeder = NMRFeeder(source, clock, feed_port, run.path / "nmr.csv",
                               provider=engine.provider, events=engine.events).start()
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
        fit_autopilot_done = False
        while not engine.stop_requested.is_set():
            # fitter autopilot: calibrate once the fitter has seen enough equilibrium sweeps
            if (feeder is not None and engine.autopilot.get("fit") and not fit_autopilot_done
                    and feeder.sent >= FIT_AUTOPILOT_SWEEPS and _line_strong(engine, clock.now())):
                await ca.caput("UNH:GEN:SSRF:FIT:UPDATE_CONSTANTS_CMD", 1)
                fit_autopilot_done = True
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
        leg_stop.set()
        for task in leg_tasks:
            try:
                await asyncio.wait_for(task, timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                task.cancel()
        if mw_leg is not None:
            mw_leg.stop()
        if ssrf_leg is not None:
            ssrf_leg.stop()
        if feeder is not None:
            feeder.stop()
        if probe:
            probe.close()
        group.stop()
        control.stop()
        runtime = {"deaths": deaths, "skipped": skipped, "never_connected": never,
                   "read_only_violations": engine.host.read_only_violations(),
                   "clamps": engine.host.clamp_counts(), "interrupted": interrupted,
                   "mw_rf_on_accepted": mw_leg.accepted if mw_leg else None,
                   **({"ssrf_burns": ssrf_leg.results if ssrf_leg else [],
                       "ssrf_skipped": ssrf_skipped} if "ssrf" in legs else {}),
                   "mw_rf_on_refused": mw_leg.refusals if mw_leg else None,
                   "nmr_sweeps": feeder.sent if feeder else 0,
                   "nmr_protected": bool(args.nmr_protected),
                   "scenario_spec": spec, "scenario_t0": t0,
                   "model_deviates": bool((spec or {}).get("deviates_from_record")),
                   "scenario_actions": ({"total": len(player.actions), "done": player.done,
                                         "errors": player.errors} if player else None),
                   "nmr_errors": feeder.errors if feeder else 0}
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
    elif cmd == "magic":
        req = {"sign": a[0] if a else "+"}
    elif cmd == "override":            # override tpg262 OVC 2e-3 | override tpg262 OVC record
        value = a[2] if len(a) > 2 else "record"
        req = {"instrument": a[0], "channel": a[1],
               "value": None if value.lower() in ("record", "none", "off") else float(value)}
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
    if src.get("kind") == "synthetic":
        from .synth import cooldown as synth
        timeline = synth.make(seed=src.get("seed", 0), faults=tuple(src.get("faults", ()))).timeline
    elif src.get("kind") == "real":
        cooldown = src["cooldown"]
        if not Path(cooldown).is_dir():           # recorded on another OS/mount: find it by name
            cooldown = Path(cooldown.replace("\\", "/")).name
        timeline, _cd = load_timeline(cooldown, os.environ.get(DATA_ENV), src["t0"], src["t1"],
                                      channel_map.load())
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


# -------------------------------------------------------------- scenarios
def scenario_args(spec: dict, speed: float | None, data: str | None):
    """argparse-equivalent run arguments for a scenario."""
    import argparse
    return argparse.Namespace(
        mode="regression", source=spec.get("source", "synthetic"), faults=",".join(spec.get("faults", [])),
        cooldown=spec.get("cooldown"), data=data, start=spec.get("start"), day=None,
        duration=float(spec.get("duration_min", 30)), speed=float(speed or spec.get("speed", 20)),
        legs=",".join(spec.get("legs", ["cryo"])), scenario=spec["name"], autostart=False,
        autopilot=None, model=spec.get("model"), nmr_protected=bool(spec.get("nmr_protected")),
        port_offset=0, noise_rel=2e-5, seed=int(spec.get("seed", 0)), scenario_spec=spec)


def _scenario(args) -> int:
    from . import scenario as sc
    from .__main__ import EXIT_FAIL, EXIT_OK
    if args.names == ["list"]:
        for name in sc.available():
            spec = sc.load(name)
            print(f"{name:22s} {spec.get('source', 'synthetic'):9s} {','.join(spec.get('legs', [])):18s} "
                  f"{spec.get('description', '')}")
        return EXIT_OK
    names = sc.available() if args.names == ["all"] else args.names
    results = []
    for name in names:
        spec = sc.load(name)
        if spec.get("source") == "real" and not args.data:
            results.append((name, "SKIPPED", f"needs ${DATA_ENV}"))
            continue
        if spec.get("posix_only") and IS_WINDOWS:
            results.append((name, "SKIPPED", "needs Linux/WSL"))
            continue
        if len(names) == 1:
            print(f"\n=== scenario {name}: {spec.get('description', '')}", flush=True)
            code = _run(scenario_args(spec, args.speed, args.data))
        else:                       # one process per scenario: libca state is per-process
            import subprocess
            cmd = [sys.executable, "-m", "cooldown_sim", "scenario", name]
            if args.speed:
                cmd += ["--speed", str(args.speed)]
            code = subprocess.run(cmd, cwd=str(REPO_ROOT)).returncode
        results.append((name, "PASS" if code == 0 else "FAIL", f"exit {code}"))
    print("\nScenario summary")
    for name, verdict, why in results:
        print(f"  {verdict:7s} {name:22s} {why}")
    return EXIT_FAIL if any(v == "FAIL" for _, v, _ in results) else EXIT_OK
