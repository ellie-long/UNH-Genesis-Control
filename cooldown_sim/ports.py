"""Port bookkeeping: the private replay ports, and the live ports to stay off.

Two sets matter:

* **replay ports** -- every CA/PVA/emulator/simulator port a simulated
  cool-down binds, read from ``stacks.yaml`` (optionally shifted by
  ``--port-offset``).  All on 127.0.0.1.
* **live ports** -- every port a live Genesis profile or launch script uses,
  *scanned from the committed configs and scripts at run time* so a new live
  IOC is covered without editing this module.

The guard refuses to start if anything is listening on a live port, and the
launcher refuses if any replay port is already taken.
"""

from __future__ import annotations

import concurrent.futures
import re
import socket
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import REPO_ROOT

LOOPBACK = "127.0.0.1"
STACKS_FILE = Path(__file__).with_name("stacks.yaml")

#: Ports that are live but appear only as literals in code, not configs.
#: (online_fitter defaults: LabVIEW feed, publisher, PyNMR feed; fitter EPICS.)
_LIVE_CODE_PORTS = {8777, 8778, 8779, 5100, 5101}

#: Ports seen in scripts that do not indicate a hardware-owning Genesis stack.
#: 8501 is the Streamlit archive viewer, which only reads archive files.
_NOT_LIVE_STACK = {8501}

_SCRIPT_PORT_RE = re.compile(r"\b(5\d{3}|7777|8\d{3})\b")
_SCRIPT_CONTEXT_RE = re.compile(r"PORT|start_local_ioc|--port", re.IGNORECASE)


# --------------------------------------------------------------------- stacks
def load_stacks(path: Path = STACKS_FILE) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass(frozen=True)
class Endpoint:
    """One replay port with what owns it (for error messages and reports)."""

    port: int
    owner: str
    kind: str          # ca | pva | emulator | simulator | fitter | control | epics


def replay_endpoints(stacks: dict | None = None, offset: int = 0) -> list[Endpoint]:
    """Every port a full simulated cool-down binds, shifted by ``offset``."""
    stacks = stacks or load_stacks()
    out: list[Endpoint] = []
    legs = stacks["legs"]

    def add(port, owner, kind):
        out.append(Endpoint(int(port) + offset, owner, kind))

    for leg in ("cryo", "ssrf", "mw"):
        entries = dict(legs[leg].get("iocs", {}))
        entries.update(legs[leg].get("borrowed", {}))
        for name, spec in entries.items():
            add(spec["ca"], f"{leg}/{name}", "ca")
            add(spec["pva"], f"{leg}/{name}", "pva")
            if "emulator" in spec:
                add(spec["emulator"]["port"], f"{leg}/{name}", "emulator")
            if "simulator" in spec:
                add(spec["simulator"]["port"], f"{leg}/{name}", "simulator")
    fitter = legs["nmr"]["fitter"]
    add(fitter["ca"], "nmr/fitter", "ca")
    add(fitter["pva"], "nmr/fitter", "pva")
    for key in ("fit_port", "publish_port", "pynmr_port"):
        add(fitter[key], f"nmr/fitter:{key}", "fitter")
    ctl = legs["sim"]["controller"]
    add(ctl["ca"], "sim/controller", "ca")
    add(ctl["pva"], "sim/controller", "pva")
    add(ctl["control_port"], "sim/controller", "control")
    add(stacks["epics"]["ca_repeater_port"], "epics/ca_repeater", "epics")
    add(stacks["epics"]["pva_broadcast_port"], "epics/pva_broadcast", "epics")
    return out


def replay_ports(stacks: dict | None = None, offset: int = 0) -> set[int]:
    return {e.port for e in replay_endpoints(stacks, offset)}


def duplicate_replay_ports(stacks: dict | None = None, offset: int = 0) -> dict[int, list[str]]:
    seen: dict[int, list[str]] = {}
    for e in replay_endpoints(stacks, offset):
        seen.setdefault(e.port, []).append(f"{e.owner}({e.kind})")
    return {p: owners for p, owners in seen.items() if len(owners) > 1}


# ----------------------------------------------------------------- live ports
def live_ports(root: Path = REPO_ROOT) -> set[int]:
    """Ports used by live Genesis profiles and launch scripts.

    * every ``*port*`` key with an integer value >= 1024 in ``configs/**/*.yaml``
    * port-like literals on lines of ``scripts/*.sh`` that mention PORT,
      ``start_local_ioc`` or ``--port``
    * the online-fitter literals in :data:`_LIVE_CODE_PORTS`
    """
    ports: set[int] = set(_LIVE_CODE_PORTS)
    for path in sorted((root / "configs").rglob("*.yaml")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError):
            continue
        _collect_yaml_ports(data, ports)
    for path in sorted((root / "scripts").glob("*.sh")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if _SCRIPT_CONTEXT_RE.search(line):
                ports.update(int(m) for m in _SCRIPT_PORT_RE.findall(line))
    return {p for p in ports if p >= 1024} - _NOT_LIVE_STACK


def _collect_yaml_ports(node, ports: set[int]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and "port" in key.lower() and isinstance(value, int) \
                    and not isinstance(value, bool):
                ports.add(value)
            else:
                _collect_yaml_ports(value, ports)
    elif isinstance(node, list):
        for item in node:
            _collect_yaml_ports(item, ports)


# -------------------------------------------------------------------- probing
def listening_ports(candidates: set[int], host: str = LOOPBACK,
                    timeout: float = 0.25) -> set[int]:
    """Which of ``candidates`` currently accept TCP connections on ``host``.

    Uses ``psutil.net_connections`` when permitted (fast, no packets);
    otherwise probes concurrently with short connect attempts.
    """
    try:
        import psutil
        listening = set()
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr:
                if conn.laddr.ip in (host, "0.0.0.0", "::", "::1") and conn.laddr.port in candidates:
                    listening.add(conn.laddr.port)
        return listening
    except Exception:          # noqa: BLE001 - AccessDenied on macOS, etc.
        pass

    def probe(port: int) -> int | None:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return port
        except OSError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        return {p for p in pool.map(probe, sorted(candidates)) if p is not None}


def unbindable_ports(ports: set[int], host: str = LOOPBACK) -> dict[int, str]:
    """Replay ports we could not bind right now (in use, or OS-reserved --
    Hyper-V exclusion ranges on Windows can move after a reboot)."""
    bad: dict[int, str] = {}
    for port in sorted(ports):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # bind the way the servers will: on POSIX SO_REUSEADDR (a port left in
        # TIME_WAIT by the previous run is reusable); on Windows exclusive use
        # (SO_REUSEADDR there would let us "succeed" on a port in active use)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            bad[port] = str(exc)
        finally:
            sock.close()
    return bad
