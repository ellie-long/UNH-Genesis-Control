"""Microwave leg: observe the real Genesis microwave chain; optionally replay history into it.

Observer (always on when the leg runs): camonitor on the Vaunix IOC's
vendor-derived read-backs -- RF actual + validity, output frequency, power --
drives the target model.  The model therefore pumps only when the *real* RF
chain (supervisor -> Vaunix IOC -> shim -> brick) is actually ON: an RF
enable that the interlocks refuse leaves the target unpolarized, as it would.

Autopilot (``engine.autopilot["mw"]``): makes Genesis follow the logged
microwave state using only operator PVs --
``SUP:OutputFrequency``, ``SRC:MW_PWRC``, ``SUP:RFEnable`` / ``SUP:RFAbort``
-- and logs whether each RF ON was accepted, with the supervisor's
InterlockMessage when it was not.
"""

from __future__ import annotations

import asyncio
import math
import time

from ..model.fit_params import drive

SUP = "UNH:GEN:MW:SUP"
SRC = "UNH:GEN:MW:SRC"
OUTPUT_RANGE_GHZ = (138.0, 142.0)
POWER_RANGE_DBM = (-40.0, 10.0)
RF_CONFIRM_S = 8.0


class MicrowaveLeg:
    def __init__(self, engine, ca):
        self.engine = engine
        self.ca = ca
        self.model = getattr(engine.provider, "model", None)
        self._values = {"rf": 0, "valid": 0, "freq": math.nan, "power": math.nan}
        self._subs = []
        self._last_on = None
        self._cmd = {"on": None, "freq": None, "power": None}
        self.refusals = 0
        self.accepted = 0

    # ---------------------------------------------------------- observer
    def start(self) -> None:
        from aioca import camonitor
        pvs = {f"{SRC}:MW_RF_RBV": "rf", f"{SRC}:MW_RF_VALID": "valid",
               f"{SRC}:MW_OutFI": "freq", f"{SRC}:MW_PWRI": "power"}
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

    def _push(self) -> None:
        on = bool(self._values["rf"]) and bool(self._values["valid"])
        if hasattr(self.engine.provider, "observe_mw"):
            self.engine.provider.observe_mw(on, self._values["freq"], self._values["power"])
        if on != self._last_on:
            self._last_on = on
            self.engine.events.write(self.engine.clock.now(), "microwave",
                                     f"RF {'ON' if on else 'OFF'} observed at "
                                     f"{self._values['freq']:.3f} GHz, {self._values['power']:+.1f} dBm")

    # ---------------------------------------------------------- autopilot
    def desired(self, t: float) -> tuple[bool, float, float]:
        """(RF on, output GHz, power dBm) the record says the microwaves had."""
        tl = self.engine.timeline
        on = drive(tl.value_at("mw_rf_status", t), tl.value_at("mw_power_db", t)) > 0.05
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
                await self.ca.caput(f"{SUP}:RFEnable", 1)
                await self._confirm_on()
            else:
                await self.ca.caput(f"{SUP}:RFAbort", 1)

    async def _confirm_on(self) -> None:
        from aioca import caget
        deadline = time.time() + RF_CONFIRM_S
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            if self._last_on:
                self.accepted += 1
                return
        try:
            msg = await caget(f"{SUP}:InterlockMessage", timeout=2)
        except Exception:  # noqa: BLE001
            msg = "?"
        self.refusals += 1
        self.engine.events.write(self.engine.clock.now(), "autopilot-refused",
                                 f"microwave RF ON not confirmed: {msg}")

    async def run(self, stop_event) -> None:
        while not stop_event.is_set():
            try:
                await self.step()
            except Exception as exc:  # noqa: BLE001 - keep the session alive
                self.engine.events.write(self.engine.clock.now(), "autopilot-error", f"microwave: {exc}")
            await asyncio.sleep(1.0)
