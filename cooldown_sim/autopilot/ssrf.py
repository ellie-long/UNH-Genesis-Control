"""ssRF leg: replay logged burns through the REAL sequencer; observe the SMC100A.

Autopilot (``engine.autopilot["ssrf"]``): when the sim clock passes the start
of a burn in the cool-down's ``blast_log``, run the operator's adaptive-burn
workflow on the unchanged ssRF sequencer (simulator execution mode), writing
only sequencer recipe/command PVs:

    recipe (CARRIER_MHZ, START/TARGET_LEVEL_DBM, FM_DEV_KHZ, FM_PERIOD_S,
    FM_REPEATS, VHIGH_V, VLOW_V) -> stage an 8192-point normalized FM waveform
    (WAVEFORM_STAGE_REQ) -> ARM_CMD -> START_CMD -> watch EXECUTION_STATE.

ABORT/DISARM are deliberately never sent: in the current Genesis code their
safe-off cleanup needs an NMR permit the interlock never grants, which locks
the sequencer until restart (known issue ssrf-safe-off-lockup).

Observer: the SMC100A read-backs (RF on, level, carrier).  While RF is
really ON, the burn is applied to the target model -- heat into the cup
(140 mW at -5 dBm, scaling 10 dB per decade: logbook 2024-12-18) and a
tensor-polarization hole that heals with Q_heal_tau (ASSUMED depth/rate).
Since Utsav's PyNMR-only interlock (merged 2026-10-19) grants the permits,
replayed burns complete and turn RF on, so this is exercised end to end.
"""

from __future__ import annotations

import asyncio
import math
import time

import numpy as np

from ..data.blast_log import Burn, read_cooldown_burns
from ..model import physics as ph

SEQ = "UNH:GEN:SSRF:SEQ"
GEN = "UNH:GEN:SSRF:GEN"
FINAL_STATES = {"COMPLETE", "FAILED", "ABORTED", "CLEANUP_FAILED"}
#: heat deposited cold by ssRF: 140 mW at -5 dBm (logbook 2024-12-18: 2.25 slm),
#: 16 mW at -15 dBm -- about one decade per 10 dB
BURN_W_AT_M5_DBM = 0.140
#: burn depth and rate, calibrated to the 2026-06 blast logs against the record's
#: P: a resonant burn removes the fraction BURN_P_DEPTH * frac of P, and frac of
#: Q (the tensor hole), with frac = 1 - exp(-HOLE_RATE * scale * seconds) and
#: scale = 10**((dBm + 5)/10).  Record: -5 dBm 1 s ~3 %, 0 dBm 1 s ~5 %,
#: +3 dBm 1 s 6-8 %, +3 dBm 32 s ~12 % of P; P then re-pumps over 10-20 min.
BURN_P_DEPTH = 0.12
HOLE_RATE_PER_S_AT_M5 = 0.14
RESONANT_WINDOW_MHZ = 0.3


def apply_burn(model, t0: float, t1: float, level_dbm: float, freq_mhz: float) -> None:
    """ssRF on from t0 to t1 at level/frequency: heat the cup and, if resonant,
    burn P and a tensor hole (queued at t1).  Shared by the ssRF leg and the hindcast."""
    if model is None or t1 <= t0:
        return
    level = level_dbm if math.isfinite(level_dbm) else -120.0
    scale = 10.0 ** ((level + 5.0) / 10.0)
    model.queue_burn_heat(t0, t1, BURN_W_AT_M5_DBM * scale)
    B = model.field_T(t1)
    larmor = ph.deuteron_larmor_MHz(B) if B > 0.5 else math.nan
    if not (math.isfinite(larmor) and math.isfinite(freq_mhz) and abs(freq_mhz - larmor) < RESONANT_WINDOW_MHZ):
        return
    frac = 1.0 - math.exp(-HOLE_RATE_PER_S_AT_M5 * scale * (t1 - t0))
    s = model.state
    model.queue_burn(t1, dP=-BURN_P_DEPTH * frac * s.P, dQ=-frac * s.Q)


def stage_waveform() -> list[float]:
    """Normalized triangle FM waveform, 8192 points in [-1, 1]."""
    n = 8192
    x = np.arange(n) / n
    return list((1.0 - 4.0 * np.abs(x - 0.5)).astype(float))


def recipe_from_burn(b: Burn) -> dict:
    """Sequencer recipe for one logged burn (validity rules of ssrf_sequence_core)."""
    dwell_ms = b.dwell_ms if math.isfinite(b.dwell_ms) and b.dwell_ms > 0 else 10.0
    period = 0.01
    start = b.sweep_min_dbm if math.isfinite(b.sweep_min_dbm) else -120.0
    target = b.sweep_max_dbm if math.isfinite(b.sweep_max_dbm) else -22.0
    if target <= start:
        target = start + 1.0
    return {
        "CARRIER_MHZ": round(b.centre_mhz, 3),
        "START_LEVEL_DBM": round(max(start, -145.0), 2),
        "TARGET_LEVEL_DBM": round(min(target, 10.0), 2),
        "FM_DEV_KHZ": round(b.fm_dev_khz if math.isfinite(b.fm_dev_khz) and b.fm_dev_khz > 0 else 10.0, 1),
        "FM_PERIOD_S": period,
        "FM_REPEATS": max(1, int(round(dwell_ms / (period * 1000.0)))),
        "VHIGH_V": 0.5,
        "VLOW_V": -0.5,
    }


class SSRFLeg:
    def __init__(self, engine, ca, cooldown_dir=None, burns=None):
        self.engine = engine
        self.ca = ca
        self.model = getattr(engine.provider, "model", None)
        self.burns: list[Burn] = [b for b in (burns or []) if b.ok]
        if burns is None and cooldown_dir is not None:
            try:
                self.burns = [b for b in read_cooldown_burns(cooldown_dir) if b.ok]
            except OSError:
                self.burns = []
        self._next = 0
        self._busy = False
        self.results: list[dict] = []
        self._rf = {"on": 0, "level": math.nan, "freq": math.nan}
        self._rf_on_since = None
        self._subs = []

    # ------------------------------------------------------------ observer
    def start(self) -> None:
        from aioca import camonitor
        pvs = {f"{GEN}:RF_RBV": "on", f"{GEN}:LEVEL_RBV": "level", f"{GEN}:FREQ_RBV": "freq"}
        names = list(pvs)

        def cb(value, index):
            if getattr(value, "ok", True):
                self._rf[pvs[names[index]]] = float(value)
                self._observe()

        self._subs = camonitor(names, cb, notify_disconnect=True)
        now = self.engine.clock.now()
        self._next = next((i for i, b in enumerate(self.burns) if b.start >= now), len(self.burns))

    def stop(self) -> None:
        for s in self._subs:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass

    def _observe(self) -> None:
        t = self.engine.clock.now()
        on = bool(self._rf["on"])
        if on and self._rf_on_since is None:
            self._rf_on_since = t
            self.engine.events.write(t, "ssrf", f"SMC RF ON observed: {self._rf['freq']:.3f} MHz, "
                                                f"{self._rf['level']:+.1f} dBm")
        elif not on and self._rf_on_since is not None:
            self._apply_burn(self._rf_on_since, t)
            self._rf_on_since = None
            self.engine.events.write(t, "ssrf", "SMC RF OFF observed")

    def _apply_burn(self, t0: float, t1: float) -> None:
        """Real RF was on from t0 to t1: heat the cup and burn a tensor hole."""
        apply_burn(self.model, t0, t1, self._rf["level"], self._rf["freq"])

    # ----------------------------------------------------------- autopilot
    async def step(self) -> None:
        if not self.engine.autopilot.get("ssrf") or not self.engine.clock.running or self._busy:
            return
        now = self.engine.clock.now()
        due = [i for i in range(self._next, len(self.burns)) if self.burns[i].start <= now]
        if not due:
            return
        skipped = len(due) - 1
        if skipped:
            self.engine.events.write(now, "autopilot", f"ssRF: {skipped} logged burn(s) skipped "
                                                       "(sim clock moved past them during a burn)")
        self._next = due[-1] + 1
        self._busy = True
        try:
            await self._burn(self.burns[due[-1]])
        finally:
            self._busy = False

    async def _burn(self, b: Burn) -> None:
        from aioca import caget
        recipe = recipe_from_burn(b)
        for pv, value in recipe.items():
            await self.ca.caput(f"{SEQ}:{pv}", value)
        stage_before = await caget(f"{SEQ}:WAVEFORM_STAGE_ID", timeout=5)
        await self.ca.caput(f"{SEQ}:WAVEFORM_STAGE_REQ", stage_waveform())
        staged = await self._wait(f"{SEQ}:WAVEFORM_STAGE_STATE", {"STAGED", "FAILED", "REJECTED"}, 10.0)
        result = {"burn_start": b.start, "carrier_MHz": b.centre_mhz, "staged": staged}
        if staged != "STAGED":
            result["outcome"] = f"staging {staged}"
            return self._record(result, await self._reason(f"{SEQ}:WAVEFORM_STAGE_REASON"))
        if await self._wait(f"{SEQ}:READY", {1}, 10.0, numeric=True) != 1:
            result["outcome"] = "not ready"
            return self._record(result, await self._reason(f"{SEQ}:REASON"))
        await self.ca.caput(f"{SEQ}:ARM_CMD", 1)
        if await self._wait(f"{SEQ}:EXECUTION_ARMED", {1}, 5.0, numeric=True) != 1:
            result["outcome"] = "arm refused"
            return self._record(result, await self._reason(f"{SEQ}:EXECUTION_REASON"))
        await self.ca.caput(f"{SEQ}:START_CMD", 1)
        state = await self._wait(f"{SEQ}:EXECUTION_STATE", FINAL_STATES, 60.0)
        result["outcome"] = state or "timeout"
        result["stage_id_before"] = float(stage_before)
        return self._record(result, await self._reason(f"{SEQ}:EXECUTION_REASON"))

    async def _wait(self, pv, wanted, timeout, numeric=False):
        from aioca import caget
        deadline = time.time() + timeout
        value = None
        while time.time() < deadline:
            try:
                value = await caget(pv, timeout=2)
            except Exception:  # noqa: BLE001
                value = None
            if value is not None:
                v = float(value) if numeric else str(value)
                if v in wanted:
                    return v
            await asyncio.sleep(0.2)
        return None if value is None else (float(value) if numeric else str(value))

    async def _reason(self, pv) -> str:
        from aioca import caget
        try:
            return str(await caget(pv, timeout=2))
        except Exception:  # noqa: BLE001
            return "?"

    def _record(self, result: dict, reason: str) -> None:
        result["reason"] = reason
        self.results.append(result)
        from ..data.records import format_epoch
        self.engine.events.write(self.engine.clock.now(), "ssrf-burn",
                                 f"logged burn of {format_epoch(result['burn_start'])} at "
                                 f"{result['carrier_MHz']:.3f} MHz -> {result['outcome']}: {reason}")

    async def run(self, stop_event) -> None:
        while not stop_event.is_set():
            try:
                await self.step()
            except Exception as exc:  # noqa: BLE001
                self.engine.events.write(self.engine.clock.now(), "autopilot-error", f"ssRF: {exc}")
                self._busy = False
            await asyncio.sleep(0.5)
