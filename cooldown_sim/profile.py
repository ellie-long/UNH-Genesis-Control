"""Generate loopback replay profiles from the canonical Genesis profiles.

The replay never runs a hand-copied configuration.  For every IOC listed in
``stacks.yaml`` this module reads the *canonical* settings.yaml named by the
canonical IOC-manager file, deep-copies it, and rewrites ONLY:

* instrument endpoints -> the loopback emulator/simulator
  (``ip``, ``port``, VISA ``resource``);
* ``general.epics_addr_list`` -> the same IOCs' replay CA ports
  (master_ioc.py overwrites EPICS_CA_ADDR_LIST from this key, so the
  environment alone cannot redirect it);
* output paths -> absolute paths inside the run directory, so a run never
  writes into a live archive/state location.

Safety flags (``write_enabled``, ``simulation``, ``execution_mode``,
``execution_enabled``, ``apply_enabled``) are never touched.  Scenario
overrides (e.g. ``state_table_validated``) are a separate, explicit list.
Every change is written to ``<run>/profiles/rewrites.json`` and the drift
guard test asserts nothing outside these rules differs.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from . import REPO_ROOT
from .ports import LOOPBACK, load_stacks

ENDPOINT_KEYS = ("ip", "port", "resource")

#: output-path key -> location inside the run directory
OUTPUT_PATHS = {
    "archive_path": "archive/data",
    "last_file": "state/cryo_last_state.yaml",
    "snapshot_dir": "archive/cryo_snapshots",
    "log_file": "archive/cryo_alarm_log.csv",
    "event_log_dir": "archive/events/ssrf",
    "session_data_root": "archive/sessions",
}

#: never rewritten, by profile generation or by scenarios
SAFETY_KEYS = frozenset({"write_enabled", "simulation", "execution_mode",
                         "execution_enabled", "apply_enabled", "initialize_on_connect"})

#: (ioc settings key, setting) a scenario may override, replay-only
SCENARIO_OVERRIDABLE = frozenset({("target_status", "state_table_validated"),
                                  ("target_status", "auto"),
                                  ("safety_interlock", "limits")})

_ADDR_TOKEN = re.compile(r"^(?P<host>[^:\s]+):(?P<port>\d+)$")


@dataclass
class Rewrite:
    file: str
    key: str
    old: object
    new: object
    reason: str


@dataclass
class LaunchSpec:
    """Everything the launcher needs to start one replay IOC."""

    leg: str
    name: str                    # manager entry name, e.g. ls224_fridge
    ioc: str                     # key inside settings.yaml (master_ioc -i)
    canonical_folder: str
    folder: str                  # generated settings folder (absolute)
    ca: int
    pva: int
    prefix: str
    emulator: dict | None = None
    simulator: dict | None = None
    posix_only: bool = False


@dataclass
class GeneratedStack:
    run_dir: Path
    iocs: list[LaunchSpec] = field(default_factory=list)
    rewrites: list[Rewrite] = field(default_factory=list)
    overrides: list[Rewrite] = field(default_factory=list)

    def by_name(self, name: str) -> LaunchSpec:
        for spec in self.iocs:
            if spec.name == name:
                return spec
        raise KeyError(name)

    def write_report(self) -> Path:
        path = self.run_dir / "profiles" / "rewrites.json"
        path.write_text(json.dumps({
            "rewrites": [asdict(r) for r in self.rewrites],
            "scenario_overrides": [asdict(r) for r in self.overrides],
        }, indent=2, default=str), encoding="utf-8")
        return path


# --------------------------------------------------------------------- helpers
def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def leg_entries(stacks: dict, leg: str) -> list[tuple[str, dict, str]]:
    """(entry name, stack spec, manager path) for every IOC of a leg."""
    block = stacks["legs"][leg]
    out = [(name, spec, block["manager"]) for name, spec in block.get("iocs", {}).items()]
    for name, spec in block.get("borrowed", {}).items():
        out.append((name, spec, spec["manager"]))
    return out


def canonical_to_replay(stacks: dict, offset: int = 0, root: Path = REPO_ROOT) -> dict[int, int]:
    """Canonical CA port -> replay CA port, for every IOC the replay launches."""
    mapping: dict[int, int] = {}
    for leg in ("cryo", "ssrf", "mw"):
        for name, spec, manager in leg_entries(stacks, leg):
            entry = _load(root / manager)[name]
            mapping[int(entry["ca_server_port"])] = int(spec["ca"]) + offset
    return mapping


def map_addr_list(value: str, mapping: dict[int, int]) -> str:
    """'127.0.0.1:5111 127.0.0.1:5112' -> replay ports.  Unmapped -> error."""
    out = []
    for token in str(value).split():
        m = _ADDR_TOKEN.match(token)
        if not m or m.group("host") != LOOPBACK:
            raise ValueError(f"epics_addr_list entry {token!r} is not a loopback host:port")
        port = int(m.group("port"))
        if port not in mapping:
            raise ValueError(f"epics_addr_list port {port} has no replay IOC "
                             "(add it to cooldown_sim/stacks.yaml)")
        out.append(f"{LOOPBACK}:{mapping[port]}")
    return " ".join(out)


def _endpoint_port(spec: dict, offset: int) -> int | None:
    for key in ("emulator", "simulator"):
        if key in spec:
            return int(spec[key]["port"]) + offset
    return None


# ------------------------------------------------------------------- generate
def generate(run_dir: Path, legs=("cryo", "ssrf", "mw"), *, offset: int = 0,
             overrides: dict | None = None, stacks: dict | None = None,
             root: Path = REPO_ROOT, posix: bool | None = None) -> GeneratedStack:
    """Write replay profiles for ``legs`` into ``run_dir/profiles``.

    ``overrides``: {ioc_key: {setting: value}} limited to SCENARIO_OVERRIDABLE.
    """
    stacks = stacks or load_stacks()
    run_dir = Path(run_dir).resolve()
    out = GeneratedStack(run_dir)
    mapping = canonical_to_replay(stacks, offset, root)
    overrides = overrides or {}
    for ioc_key, settings in overrides.items():
        for setting in settings:
            if (ioc_key, setting) not in SCENARIO_OVERRIDABLE:
                raise ValueError(f"scenario may not override {ioc_key}.{setting}")

    for leg in legs:
        for name, spec, manager in leg_entries(stacks, leg):
            entry = _load(root / manager)[name]
            folder = entry["settings_folder"]
            ioc_key = entry["ioc"]
            src = root / folder / "settings.yaml"
            settings = _load(src)
            new = copy.deepcopy(settings)
            dest_dir = run_dir / "profiles" / leg / name
            rel = f"{leg}/{name}/settings.yaml"

            def note(key, old, value, reason, bucket=out.rewrites):
                bucket.append(Rewrite(rel, key, old, value, reason))

            # general.epics_addr_list
            addr = settings["general"].get("epics_addr_list", "None")
            if addr is not None and "None" not in str(addr):
                mapped = map_addr_list(addr, mapping)
                new["general"]["epics_addr_list"] = mapped
                note("general.epics_addr_list", addr, mapped, "replay CA ports")

            block = new[ioc_key]
            port = _endpoint_port(spec, offset)
            if port is not None:
                if "resource" in block:
                    value = f"TCPIP0::{LOOPBACK}::{port}::SOCKET"
                    note(f"{ioc_key}.resource", block["resource"], value, "loopback simulator")
                    block["resource"] = value
                else:
                    note(f"{ioc_key}.ip", block.get("ip"), LOOPBACK, "loopback emulator")
                    note(f"{ioc_key}.port", block.get("port"), port, "loopback emulator")
                    block["ip"], block["port"] = LOOPBACK, port

            for key, rel_path in OUTPUT_PATHS.items():
                if key in block:
                    value = str(run_dir / rel_path)
                    note(f"{ioc_key}.{key}", block[key], value, "run-directory output")
                    block[key] = value

            for setting, value in overrides.get(ioc_key, {}).items():
                if setting == "limits":
                    value = str(Path(value).resolve())
                note(f"{ioc_key}.{setting}", block.get(setting), value, "scenario override",
                     out.overrides)
                block[setting] = value

            dest_dir.mkdir(parents=True, exist_ok=True)
            with open(dest_dir / "settings.yaml", "w", encoding="utf-8") as fh:
                yaml.safe_dump(new, fh, sort_keys=False)

            out.iocs.append(LaunchSpec(
                leg=leg, name=name, ioc=ioc_key, canonical_folder=folder,
                folder=str(dest_dir), ca=int(spec["ca"]) + offset, pva=int(spec["pva"]) + offset,
                prefix=settings["general"]["prefix"], emulator=spec.get("emulator"),
                simulator=spec.get("simulator"), posix_only=bool(spec.get("posix_only"))))
    out.write_report()
    return out


# ------------------------------------------------------------- loopback scan
_IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_HOSTISH_KEYS = ("ip", "host", "ssh_host", "resource", "url", "address_list")


def non_loopback_endpoints(settings: dict, path: str = "") -> list[str]:
    """Every non-loopback IPv4 or host-like value in a generated profile.

    Exempt: ``shim.ssh_host`` when the device runs ``simulation: true`` with
    ``shim.start_with_ioc: false`` (the Vaunix shim is never contacted then).
    """
    found: list[str] = []

    def walk(node, where, ctx):
        if isinstance(node, dict):
            sim = node.get("simulation") is True
            for k, v in node.items():
                child_ctx = ctx | ({"simulation"} if sim else set())
                if k == "shim" and isinstance(v, dict) and "simulation" in child_ctx \
                        and v.get("start_with_ioc") is False:
                    continue
                walk(v, f"{where}.{k}" if where else str(k), child_ctx)
                if isinstance(v, str) and k in _HOSTISH_KEYS and v not in (LOOPBACK, "None") \
                        and not v.startswith(f"TCPIP0::{LOOPBACK}::"):
                    found.append(f"{where}.{k}={v}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{where}[{i}]", ctx)
        elif isinstance(node, str):
            for ip in _IPV4.findall(node):
                if ip != LOOPBACK:
                    found.append(f"{where}={node}")

    walk(settings, path, set())
    return sorted(set(found))
