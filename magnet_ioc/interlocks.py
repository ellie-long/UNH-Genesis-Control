"""The cryogenics precondition and ramp blocks for the magnet (review issue 13).

Ells (2026-10-22): the magnet cannot run without liquid helium, so the magnet
subsystem must not move the current or the switch unless the cryogenics
subsystem is running; the magnet LHe level-meter checks matter most.
Missing, disconnected or stale cryogenics data BLOCKS -- it never lets a
command through.  HOLD, heater OFF and the outage button never need it.

Checks (every threshold is a setting; provisional ones name their question):

* the cryogenics IOCs are up: their ``MAN:<ioc>_time`` heartbeats are recent
  (question 91: which IOCs, how recent);
* the magnet LHe level is connected, recent and >= 20" (>= 16" in Expert
  mode, AMI's minimum operating level; Ells 2026-10-22);
* the magnet thermometers are not warming (question 87: which, how warm);
* the shells' vacuum is not in alarm: 1e-5 Torr with the magnet's bottom
  below 80 K, 1e-4 Torr above (Ells, question 3).

The same fetch also brings the fringe-field gaussmeter (LS455), which is
shown, never checked: the student's V4 had the LHe level and the fringe field
typed in by hand; Ells (2026-10-23): read them from the existing Genesis IOCs.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

P = "UNH:GEN:CRYO"


@dataclass
class PermitConfig:
    lhe_pv: str = f"{P}:MAG:LHE:Magnet_LI"
    lhe_min_in: float = 20.0
    lhe_min_expert_in: float = 16.0
    #: IOC name -> heartbeat PV (question 91)
    heartbeats: dict = field(default_factory=lambda: {
        "lhe_magnet": f"{P}:MAG:LHE:MAN:lhe_magnet_time",
        "ls224_magnet": f"{P}:MAG:MAN:ls224_magnet_time",
        "ionivac": f"{P}:ION:MAN:ionivac_time",
    })
    heartbeat_max_age_s: float = 60.0
    reading_max_age_s: float = 60.0
    #: thermometer -> PV, and the warmest each may be (question 87: provisional)
    magnet_temps: dict = field(default_factory=lambda: {
        "M4": f"{P}:MAG:M4_TI", "M5": f"{P}:MAG:M5_TI"})
    magnet_temp_max_k: float = 6.0
    shells_pv: str = f"{P}:ION:Ion_PI"
    magnet_bottom: str = "M5"
    shells_max_cold_torr: float = 1e-5        # magnet bottom below shells_cold_below_k
    shells_max_warm_torr: float = 1e-4
    shells_cold_below_k: float = 80.0
    #: display only (the student's FRINGE_FIELD_T / _G): the LS455 IOC
    fringe_pv: str = f"{P}:FIELD:Fringe_FieldT"

    @classmethod
    def from_settings(cls, s: dict | None) -> "PermitConfig":
        c = cls()
        for k, v in (s or {}).items():
            if k in c.__dataclass_fields__:
                setattr(c, k, dict(v) if isinstance(v, dict) else
                        (float(v) if isinstance(getattr(c, k), float) else v))
        return c

    def pvs(self) -> list[str]:
        return list(dict.fromkeys([self.lhe_pv, *self.heartbeats.values(),
                                   *self.magnet_temps.values(), self.shells_pv,
                                   self.fringe_pv]))


@dataclass(frozen=True)
class Reading:
    """One PV as fetched: value, CA timestamp (epoch s), connected."""
    value: float
    timestamp: float
    ok: bool = True


def _num(r: Reading | None) -> float:
    if r is None or not r.ok:
        return math.nan
    try:
        v = float(r.value)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def evaluate(cfg: PermitConfig, readings: dict, now: float, expert: bool = False) -> tuple[bool, str]:
    """(ok, reason).  ``readings``: PV -> Reading (absent = not connected)."""
    for ioc, pv in cfg.heartbeats.items():
        beat = _num(readings.get(pv))
        if not math.isfinite(beat):
            return False, f"cryo IOC {ioc} not running"
        if now - beat > cfg.heartbeat_max_age_s:
            return False, f"cryo IOC {ioc} stale ({now - beat:.0f} s)"
    lhe = readings.get(cfg.lhe_pv)
    level = _num(lhe)
    if not math.isfinite(level):
        return False, "magnet LHe level not available"
    if now - lhe.timestamp > cfg.reading_max_age_s:
        return False, f"magnet LHe level stale ({now - lhe.timestamp:.0f} s)"
    floor = cfg.lhe_min_expert_in if expert else cfg.lhe_min_in
    if level < floor:
        return False, f"magnet LHe {level:.1f} in < {floor:g} in"
    temps = {}
    for name, pv in cfg.magnet_temps.items():
        t = _num(readings.get(pv))
        if not math.isfinite(t):
            return False, f"magnet thermometer {name} not available"
        temps[name] = t
        if t > cfg.magnet_temp_max_k:
            return False, f"magnet {name} {t:.2f} K > {cfg.magnet_temp_max_k:g} K"
    vac = _num(readings.get(cfg.shells_pv))
    if not math.isfinite(vac):
        return False, "shells vacuum not available"
    bottom = temps.get(cfg.magnet_bottom, math.nan)
    limit = cfg.shells_max_cold_torr if (math.isfinite(bottom) and bottom < cfg.shells_cold_below_k) \
        else cfg.shells_max_warm_torr
    if vac > limit:
        return False, f"shells vacuum {vac:.1e} Torr > {limit:.0e}"
    return True, "OK"


class CryoPermit:
    """Fetches the cryogenics PVs over Channel Access and evaluates them."""

    def __init__(self, cfg: PermitConfig, *, fetch=None, clock=time.time):
        self.cfg, self.clock = cfg, clock
        self._fetch = fetch or self._ca_fetch
        self.last = (False, "not checked yet")
        #: the latest fetch (PV -> Reading): the device shows the LHe level
        #: and the fringe field from it
        self.readings: dict = {}
        self.fetched_at = math.nan

    async def _ca_fetch(self, pvs):
        import aioca
        values = await aioca.caget(pvs, format=aioca.FORMAT_TIME, timeout=2.0, throw=False)
        out = {}
        for pv, v in zip(pvs, values):
            if getattr(v, "ok", True) is False:
                continue
            out[pv] = Reading(v, float(getattr(v, "timestamp", 0.0)), True)
        return out

    async def check(self, expert: bool = False) -> tuple[bool, str]:
        try:
            readings = await self._fetch(self.cfg.pvs())
        except Exception as exc:                 # noqa: BLE001 - no data: blocked
            self.readings, self.fetched_at = {}, self.clock()
            self.last = (False, f"cryo readings failed: {exc}"[:60])
            return self.last
        self.readings, self.fetched_at = readings, self.clock()
        self.last = evaluate(self.cfg, readings, self.clock(), expert)
        return self.last

    def value(self, pv: str) -> float:
        """The latest fetched value of ``pv`` (NaN when not connected)."""
        return _num(self.readings.get(pv))
