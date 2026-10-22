"""Open the Phoebus operator screens on a running simulated cool-down.

Writes ``<run>/phoebus_replay.ini`` in the same property format as
configs/phoebus/*.ini, but listing ONLY this run's loopback replay CA ports,
then launches Phoebus with Sim Control plus the unchanged live screens.

Phoebus keeps its user folder inside the run directory
(``-Dphoebus.user``), so a rehearsal never changes the operator's normal
Phoebus preferences or saved layouts.

Phoebus is found via $PHOEBUS_BIN (a ``product-*.jar``, ``phoebus.sh`` or
``phoebus.bat``), then ``phoebus``/``phoebus.sh`` on PATH, then the newest
``~/phoebus/product-*/product-*.jar``.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import REPO_ROOT, run_dir

SIM_CONTROL = Path(__file__).with_name("gui") / "genesis_sim_control.bob"
LIVE_SCREENS = {"cryo": "genesis_cryo.bob", "mw": "genesis_microwave.bob", "ssrf": "genesis_ssrf.bob",
                "fitter": "genesis_ssrf_fitter.bob", "panopticon": "genesis_panopticon.bob",
                "cryo_safety": "genesis_cryo_safety.bob", "cart": "genesis_pumping_cart.bob"}


def find_phoebus() -> list[str] | None:
    """Command prefix that launches Phoebus, or None."""
    env = os.environ.get("PHOEBUS_BIN")
    candidates = [env] if env else []
    for name in ("phoebus", "phoebus.sh"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates += sorted(glob.glob(str(Path.home() / "phoebus" / "product-*" / "product-*.jar")),
                         reverse=True)
    for c in candidates:
        if not c or not Path(c).exists():
            continue
        if c.endswith(".jar"):
            java = shutil.which("java")
            if java:
                return [java, "-DCA_DISABLE_REPEATER=true", "-Dfile.encoding=UTF-8", "-jar", c]
        elif c.endswith(".bat"):
            return ["cmd", "/c", c]
        else:
            return [c]
    return None


def write_ini(run: Path, ca_ports: list[int], pva_ports: list[int] = ()) -> Path:
    path = Path(run) / "phoebus_replay.ini"
    # Pin BOTH Phoebus Channel Access clients to this run's replay ports:
    # Phoebus 4 serves ca:// with JCA (org.phoebus.pv.ca/*); Phoebus 5 also
    # ships the Jackie client (org.phoebus.pv.jackie/*), which otherwise
    # reads EPICS_CA_* from the environment.
    addr = " ".join(f"127.0.0.1:{p}" for p in sorted(ca_ports))
    # pva:// screens (e.g. Microwave): name each IOC's PVA server (TCP search)
    pva = ""
    if pva_ports:
        servers = " ".join(f"127.0.0.1:{p}" for p in sorted(pva_ports))
        pva = (f"org.phoebus.pv.pva/epics_pva_name_servers={servers}\n"
               "org.phoebus.pv.pva/epics_pva_addr_list=\n"
               "org.phoebus.pv.pva/epics_pva_auto_addr_list=false\n")
    path.write_text(
        "# Phoebus settings for a SIMULATED cool-down (cooldown_sim).\n"
        "# Loopback replay ports only; generated per run.\n"
        "org.phoebus.pv/default=ca\n"
        f"org.phoebus.pv.ca/addr_list={addr}\n"
        "org.phoebus.pv.ca/auto_addr_list=false\n"
        "org.phoebus.pv.ca/max_array_bytes=262144\n"
        "org.phoebus.pv.jackie/use_env=false\n"
        f"org.phoebus.pv.jackie/ca_address_list={addr}\n"
        "org.phoebus.pv.jackie/ca_auto_address_list=false\n"
        "org.phoebus.pv.jackie/ca_max_array_bytes=262144\n" + pva, encoding="utf-8")
    return path


def run_ca_ports(run: Path) -> list[int]:
    stack = json.loads((Path(run) / "state" / "stack.json").read_text(encoding="utf-8"))
    ports = {s["ca"] for s in stack["iocs"]}
    manifest = json.loads((Path(run) / "manifest.json").read_text(encoding="utf-8"))
    ctl = manifest.get("controller", {})
    if ctl.get("ca"):
        ports.add(int(ctl["ca"]))
    fitter = manifest.get("fitter") or {}
    if fitter.get("ca"):
        ports.add(int(fitter["ca"]))
    return sorted(ports)


def run_pva_ports(run: Path) -> list[int]:
    stack = json.loads((Path(run) / "state" / "stack.json").read_text(encoding="utf-8"))
    ports = {s["pva"] for s in stack["iocs"] if s.get("pva")}
    manifest = json.loads((Path(run) / "manifest.json").read_text(encoding="utf-8"))
    for key in ("controller", "fitter"):
        if (manifest.get(key) or {}).get("pva"):
            ports.add(int(manifest[key]["pva"]))
    return sorted(ports)


def command(run: Path, screens: list[str]) -> list[str] | None:
    prefix = find_phoebus()
    if prefix is None:
        return None
    ini = write_ini(run, run_ca_ports(run), run_pva_ports(run))
    user = Path(run) / "phoebus_user"
    user.mkdir(exist_ok=True)
    # JVM options must precede -jar
    cmd = list(prefix)
    if "-jar" in cmd:
        cmd.insert(cmd.index("-jar"), f"-Dphoebus.user={user}")
    cmd += ["-nosplash", "-settings", str(ini), "-resource", SIM_CONTROL.resolve().as_uri()]
    for name in screens:
        cmd += ["-resource", (REPO_ROOT / "gui" / LIVE_SCREENS[name]).resolve().as_uri()]
    return cmd


def add_parsers(sub) -> None:
    p = sub.add_parser("phoebus", help="open Sim Control and the operator screens on a session")
    p.add_argument("--run", help="run directory (default: latest)")
    p.add_argument("--screens", default="cryo", help="comma list of: " + ",".join(LIVE_SCREENS))
    p.add_argument("--print", action="store_true", help="print the command instead of launching")
    p.set_defaults(func=_phoebus)


def _phoebus(args) -> int:
    run = Path(args.run).resolve() if args.run else run_dir.latest()
    if run is None:
        print("no run directory")
        return 3
    screens = [s.strip() for s in args.screens.split(",") if s.strip()]
    cmd = command(run, screens)
    if cmd is None:
        print("Phoebus not found: set PHOEBUS_BIN to phoebus.sh/.bat or the product-*.jar")
        return 3
    if args.print:
        print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
        return 0
    log = open(Path(run) / "logs" / "phoebus.log", "ab")
    subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL)
    print(f"Phoebus starting on {run.name} (log: {run / 'logs' / 'phoebus.log'})")
    return 0
