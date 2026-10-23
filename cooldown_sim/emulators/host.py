"""Build and run the emulators for a generated replay stack.

For every launched IOC whose stack entry names an ``emulator``, construct the
matching protocol emulator from the IOC's *generated* settings (so slots,
channels, addresses and calibration always match what the IOC will ask for)
and serve it on the replay port.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .. import REPO_ROOT, conversions
from ..channel_map import ChannelMap
from ..profile import GeneratedStack, LaunchSpec
from .base import Emulator, LoopbackServer
from .cryo import IONIVAC, LS224, LS455, MKS902B, TPG262
from .cryo_optional import AMI135, AMI1700, HastingsFlowmeter, KJLSparcStream
from .ips120 import IPS120
from .scpi_supply import ScpiSupply

log = logging.getLogger(__name__)


@dataclass
class HostedEmulator:
    spec: LaunchSpec
    emulator: Emulator
    server: LoopbackServer
    write_enabled: bool
    notes: list[str] = field(default_factory=list)


def _settings(spec: LaunchSpec) -> dict:
    with open(Path(spec.folder) / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)[spec.ioc]


def _channels(settings: dict) -> list[str]:
    return [c for c in settings.get("channels", []) if "None" not in str(c)]


def _ls224_inverse(settings: dict) -> dict:
    """{slot: inverse curve} -- the same curves the driver applies, loaded the
    same way: classic rows from the calibration file (ODS or legacy CSV), and
    the multi-band CCX Cernox curves (T1/T3) from ``ccx_calibration``."""
    from target_ioc.instruments import ls224 as drv   # same curves the driver applies

    def _path(p):
        p = Path(p)
        return p if p.is_absolute() else REPO_ROOT / p

    chans = _channels(settings)
    slots = dict(zip(chans, [str(s) for s in settings.get("slots", [])]))
    ccx_sensors = settings.get("ccx_sensors", {}) or {}
    out = {}
    cal_path = settings.get("calibration")
    if cal_path:
        if str(cal_path).lower().endswith(".ods"):
            from genesis_common.thermometry_ods import load_classic_ods
            expected = {name: slots[name] for name in chans if name in slots and name not in ccx_sensors}
            table = load_classic_ods(_path(cal_path), expected)
        else:
            table = drv.load_calibration(_path(cal_path))
        out.update({slot: conversions.InverseCalibration(functools.partial(drv.resistance_to_kelvin, entry))
                    for slot, entry in table.items()})
    if settings.get("ccx_calibration") and ccx_sensors:
        from genesis_common.cernox_ccx import ccx_kelvin, load_ccx_bands
        bands = load_ccx_bands(_path(settings["ccx_calibration"]))
        for name, source in ccx_sensors.items():
            if name in slots and source in bands:
                out[slots[name]] = conversions.InverseCalibration(functools.partial(ccx_kelvin, bands[source]))
    return out


def build(spec: LaunchSpec, settings: dict, source, initial: dict | None = None) -> Emulator:
    kind = spec.emulator["kind"]
    chans = _channels(settings)
    if kind == "ls224":
        slots = dict(zip([str(s) for s in settings.get("slots", [])], chans))
        return LS224(slots, source, _ls224_inverse(settings))
    if kind == "mks902b":
        return MKS902B(chans[0], source, address=int(settings.get("address", 253)))
    if kind == "tpg262":
        pair = (chans + ["None", "None"])[:2]
        return TPG262((pair[0], pair[1]), source)
    if kind == "ionivac":
        return IONIVAC(chans[0], source)
    if kind == "ls455":
        return LS455(chans[0], source)
    if kind in ("flowmeter_pi", "flowmeter_diewu"):
        return HastingsFlowmeter(chans[0], source, correction=float(settings.get("correction", 1.0)),
                                 via_pi=kind == "flowmeter_pi")
    if kind == "ami1700":
        return AMI1700(chans[0], source, scale=float(settings.get("scale", 1.0)),
                       offset=float(settings.get("offset", 0.0)))
    if kind == "ami135":
        return AMI135(chans[0], source, scale=float(settings.get("scale", 0.393)))
    if kind == "manometer_low":
        return KJLSparcStream(chans[0], source)
    if kind == "scpi_supply":
        init = dict(initial or {})
        return ScpiSupply(spec.emulator.get("model", "SPD1305X"),
                          max_voltage=float(settings.get("voltage_limit", settings.get("max_voltage", 30.0))),
                          max_current=float(settings.get("current_limit", settings.get("max_current", 5.0))),
                          **init)
    if kind == "ips120":
        return IPS120(inductance_h=float(settings.get("inductance_h", 38.0)),
                      heater_ma=float(settings.get("heater_ma", 40.0)), **dict(initial or {}))
    raise ValueError(f"no emulator for kind {kind!r}")


def magnet_initial(cmap: ChannelMap, timeline, t: float | None) -> dict:
    """The IPS120's starting state: the record's magnet current at the run's
    start (driven), or zero when the record has none."""
    ch = getattr(cmap, "history", {}).get("magnet_i")
    if ch is None or timeline is None or t is None:
        return {}
    try:
        amps = float(ch.recorded(timeline, t))
    except Exception:                       # noqa: BLE001 - no record: start at zero
        return {}
    if amps != amps:                        # NaN: not in the record
        return {}
    return {"initial_current_a": round(min(max(amps, 0.0), 65.0), 3)}


ABSENT_NOTE = "absent in this era's record: connection refused"


def absent_instruments(stack: GeneratedStack, cmap: ChannelMap, timeline) -> dict[str, str]:
    """Emulated instruments the replayed record has no value for at all, whose
    channel map says ``absent: refuse``: they are simply not there (the IOC
    sees a refused connection).  Shared by the host and the validators."""
    out: dict[str, str] = {}
    if timeline is None:
        return out
    for spec in stack.iocs:
        if not spec.emulator:
            continue
        chans = cmap.instruments.get(spec.name, {})
        if chans and all(c.absent == "refuse" and not c.available(timeline) for c in chans.values()):
            out[spec.name] = ABSENT_NOTE
    return out


class EmulatorHost:
    def __init__(self, stack: GeneratedStack, provider, cmap: ChannelMap, timeline=None,
                 supply_initial: dict | None = None):
        self.stack = stack
        self.provider = provider
        self.cmap = cmap
        self.timeline = timeline
        self.supply_initial = supply_initial or {}
        self.hosted: dict[str, HostedEmulator] = {}

    def start(self) -> None:
        absent = absent_instruments(self.stack, self.cmap, self.timeline)
        for spec in self.stack.iocs:
            if not spec.emulator:
                continue
            settings = _settings(spec)
            source = self.provider.source_for(spec.name)
            initial = self.supply_initial.get(spec.name)
            if initial is None and spec.emulator["kind"] == "ips120":
                clock = getattr(self.provider, "clock", None)
                initial = magnet_initial(self.cmap, self.timeline, clock.now() if clock else None)
            emu = build(spec, settings, source, initial)
            emu.name = spec.name
            # the generated profile already carries the (offset) emulator port
            server = LoopbackServer(emu, port=int(settings["port"]))
            hosted = HostedEmulator(spec, emu, server, bool(settings.get("write_enabled", False)))
            # an instrument absent from this era's record and marked 'refuse'
            # in the channel map is simply not there
            if spec.name in absent:
                emu.fault.set("absent")
                hosted.notes.append(absent[spec.name])
            server.start()
            self.hosted[spec.name] = hosted
            log.info("emulator %s on 127.0.0.1:%s%s", spec.name, server.port,
                     " (absent)" if emu.fault.get() == "absent" else "")

    def absent(self) -> dict[str, str]:
        """Instruments started absent (for the run's runtime facts)."""
        return {n: ABSENT_NOTE for n, h in self.hosted.items() if ABSENT_NOTE in h.notes}

    def stop(self) -> None:
        for h in self.hosted.values():
            h.server.stop()

    def set_fault(self, name: str, mode: str) -> None:
        self.hosted[name].server.set_fault(mode)

    def read_only_violations(self) -> dict[str, list[str]]:
        """Writes that reached an emulator behind a write_enabled: false IOC."""
        out = {}
        for name, h in self.hosted.items():
            if not h.write_enabled:
                writes = [w.request.decode("ascii", "replace").strip() for w in h.emulator.writes()]
                if writes:
                    out[name] = writes
        return out

    def clamp_counts(self) -> dict[str, dict[str, int]]:
        return {name: dict(h.emulator.clamped) for name, h in self.hosted.items()
                if getattr(h.emulator, "clamped", None)}
