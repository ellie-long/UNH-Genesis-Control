"""Microwave leg: observe the real Genesis microwave chain; optionally replay history into it.

Observer (always on when the leg runs): camonitor on the Vaunix IOC's
vendor-derived read-backs -- RF actual + validity, output frequency, power --
and on the UCA IOC's measured voltage and output state drives the target
model.  The model therefore pumps only when the *real* RF chain (supervisor
-> Vaunix IOC -> shim -> brick) is actually ON, and only as strongly as the
real UCA voltage allows: an RF enable that the interlocks refuse leaves the
target unpolarized, as it would, and RF ON at the 5 V enable voltage
delivers no power until the UCA is lowered.

UCA output OFF is passed to the model as 5 V (no power).  The real AMC runs
at FULL power with its UCA input unconnected, but an OFF Siglent output is
not a disconnected cable, its effect on the chain is not established, and
Genesis permits RF ON only with the output ON; the model takes the safe
reading -- it never shows power the operator did not deliberately set.

Autopilot (``engine.autopilot["mw"]``): makes Genesis follow the logged
microwave state using only operator PVs --
``SUP:OutputFrequency``, ``SRC:MW_PWRC``, ``SUP:RFEnable`` / ``SUP:RFAbort``
and the UCA voltage ``PS:VSET_CMD`` -- following the real procedure: UCA at
5 V for RF ON, lowered to the history value once RF ON is confirmed, and back
to 5 V before RF OFF.  The record has no usable UCA voltage, so the history
value is 0 V (full power).  It logs whether each RF ON was accepted, with the
supervisor's InterlockMessage when it was not.
"""

from __future__ import annotations

import asyncio
import math
import time

from ..model.fit_params import HIST_UCA_V, UCA_SAFE_V, hist_drive

SUP = "UNH:GEN:MW:SUP"
SRC = "UNH:GEN:MW:SRC"
PS = "UNH:GEN:MW:PS"                    # UCA supply (microwave_ioc/spd1305x)
OUTPUT_RANGE_GHZ = (138.0, 142.0)
POWER_RANGE_DBM = (-40.0, 10.0)
RF_CONFIRM_S = 8.0
#: supervisor RF-enable window around 5 V (physical supervisor interlock tolerance)
UCA_TOL_V = 0.1
UCA_SETTLE_S = 10.0                     # wall time for the UCA read-back to reach a new setpoint


class MicrowaveLeg:
    def __init__(self, engine, ca):
        self.engine = engine
        self.ca = ca
        self.model = getattr(engine.provider, "model", None)
        self._values = {"rf": 0, "valid": 0, "freq": math.nan, "power": math.nan,
                        "uca": math.nan, "uca_out": math.nan}
        self._subs = []
        self._last_on = None
        self._last_uca = math.nan
        self._cmd = {"on": None, "freq": None, "power": None}
        self.refusals = 0
        self.accepted = 0

    # ---------------------------------------------------------- observer
    def start(self) -> None:
        from aioca import camonitor
        pvs = {f"{SRC}:MW_RF_RBV": "rf", f"{SRC}:MW_RF_VALID": "valid",
               f"{SRC}:MW_OutFI": "freq", f"{SRC}:MW_PWRI": "power",
               f"{PS}:VMEAS": "uca", f"{PS}:OUTPUT_RBV": "uca_out"}
        names = list(pvs)

        def cb(value, index):
            if getattr(value, "ok", True):
                self._values[pvs[names[index]]] = float(value)
                self._push()

        self._subs = camonitor(names, cb, notify_disconnect=True)

    def stop(self) -> None:
        for s in self._subs:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass

    def uca_V(self) -> float:
        """UCA voltage the chain sees (V), as the model input: the measured
        voltage with the output ON, 5 V with it OFF (see module doc), NaN if unknown."""
        out = self._values["uca_out"]
        if not math.isfinite(out):
            return math.nan
        return self._values["uca"] if out else UCA_SAFE_V

    def _push(self) -> None:
        on = bool(self._values["rf"]) and bool(self._values["valid"])
        uca = self.uca_V()
        if hasattr(self.engine.provider, "observe_mw"):
            self.engine.provider.observe_mw(on, self._values["freq"], self._values["power"], uca)
        if on != self._last_on:
            self._last_on = on
            self.engine.events.write(self.engine.clock.now(), "microwave",
                                     f"RF {'ON' if on else 'OFF'} observed at "
                                     f"{self._values['freq']:.3f} GHz, {self._values['power']:+.1f} dBm")
        if math.isfinite(uca) and not abs(uca - self._last_uca) < 0.05:
            self._last_uca = uca
            off = "" if self._values["uca_out"] else " (output OFF: no power)"
            self.engine.events.write(self.engine.clock.now(), "microwave",
                                     f"UCA {uca:.3f} V observed{off}")

    # ---------------------------------------------------------- autopilot
    def desired(self, t: float) -> tuple[bool, float, float]:
        """(RF on, output GHz, power dBm) the record says the microwaves had
        (the UCA while on is HIST_UCA_V: the record has no usable UCA)."""
        tl = self.engine.timeline
        on = hist_drive(tl, t) > 0.05
        f = self.model._hist_freq(t) if self.model is not None else math.nan
        p = tl.value_at("mw_power_db", t)
        if not (math.isfinite(p) and POWER_RANGE_DBM[0] <= p <= POWER_RANGE_DBM[1]):
            p = 10.0
        return on, f, p

    async def step(self) -> None:
        if not self.engine.autopilot.get("mw") or not self.engine.clock.running:
            return
        on, f, p = self.desired(self.engine.clock.now())
        if math.isfinite(f) and OUTPUT_RANGE_GHZ[0] <= f <= OUTPUT_RANGE_GHZ[1] and \
                (self._cmd["freq"] is None or abs(f - self._cmd["freq"]) > 1e-3):
            await self.ca.caput(f"{SUP}:OutputFrequency", round(f, 6))
            self._cmd["freq"] = f
        if self._cmd["power"] is None or abs(p - self._cmd["power"]) > 0.05:
            await self.ca.caput(f"{SRC}:MW_PWRC", p)
            self._cmd["power"] = p
        if on != self._cmd["on"]:
            self._cmd["on"] = on
            if on:
                # Genesis enables RF only with the UCA at 5.0 +/- 0.1 V
                await self._set_uca(UCA_SAFE_V, "for RF ON (enable needs 5 V)")
                await self.ca.caput(f"{SUP}:RFEnable", 1)
                if await self._confirm_on():
                    # after a confirmed ON, deliberate UCA reduction is permitted
                    await self._set_uca(HIST_UCA_V, "after RF ON confirmed (history: full power)")
            else:
                await self._set_uca(UCA_SAFE_V, "before RF OFF")
                await self.ca.caput(f"{SUP}:RFAbort", 1)

    async def _set_uca(self, volts: float, why: str) -> bool:
        """Bring the UCA to ``volts`` through the operator setpoint and wait for
        the measured read-back; no write if it is already there.  Neither the
        UCA IOC nor the supervisor imposes a ramp-rate limit: one step."""
        if self._values["uca_out"] == 1 and abs(self.uca_V() - volts) <= UCA_TOL_V:
            return True
        self.engine.events.write(self.engine.clock.now(), "microwave",
                                 f"autopilot: UCA -> {volts:.1f} V {why}")
        await self.ca.caput(f"{PS}:VSET_CMD", volts)
        deadline = time.time() + UCA_SETTLE_S
        while time.time() < deadline:
            await asyncio.sleep(0.25)
            if abs(self.uca_V() - volts) <= UCA_TOL_V:
                return True
        self.engine.events.write(self.engine.clock.now(), "autopilot-error",
                                 f"microwave: UCA read-back {self.uca_V():.3f} V did not reach "
                                 f"{volts:.1f} V within {UCA_SETTLE_S:.0f} s")
        return False

    async def _confirm_on(self) -> bool:
        from aioca import caget
        deadline = time.time() + RF_CONFIRM_S
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            if self._last_on:
                self.accepted += 1
                return True
        try:
            msg = await caget(f"{SUP}:InterlockMessage", timeout=2)
        except Exception:  # noqa: BLE001
            msg = "?"
        self.refusals += 1
        self.engine.events.write(self.engine.clock.now(), "autopilot-refused",
                                 f"microwave RF ON not confirmed: {msg}")
        return False

    async def run(self, stop_event) -> None:
        while not stop_event.is_set():
            try:
                await self.step()
            except Exception as exc:  # noqa: BLE001 - keep the session alive
                self.engine.events.write(self.engine.clock.now(), "autopilot-error", f"microwave: {exc}")
            await asyncio.sleep(1.0)
