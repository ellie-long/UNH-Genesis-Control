"""ModelProvider: the record, plus the closed-loop target model's response.

Drop-in replacement for ReplayProvider.  Thermometers, the cell pressure and
the OVC pressure are passed through TargetModel; every other channel is the
plain record.  Exposes ``truth()``, ``set_input()`` and ``reset()`` for the
engine / Sim Control.
"""

from __future__ import annotations

import math

from ..providers import ReplayProvider
from .target import TargetModel

THERMO_INSTRUMENTS = ("ls224_fridge", "ls224_magnet")

#: Sim Control / ctl model keys -> Inputs fields (with coercion)
INPUT_KEYS = {
    "coldboard_heat_W": float, "quench": bool, "nmr_noise_x": float,
    "nmr_phase_offset_deg": float, "line_offset_MHz": float,
    "nmr_center_MHz": float, "nmr_span_MHz": float,
    "mw_on": bool, "mw_freq_GHz": float, "mw_power_dB": float, "mw_uca_V": float,
}


class ModelProvider(ReplayProvider):
    def __init__(self, timeline, cmap, clock, *, t0: float, params: dict | None = None,
                 cooldown: str | None = None, **kw):
        super().__init__(timeline, cmap, clock, **kw)
        self.model = TargetModel(timeline, t0, params, cooldown)
        self.host = None                 # set by the engine: lets inputs drive emulators
        self.fit_P = float("nan")        # online fitter's latest P (%), set by the NMR leg

    def recorded(self, instrument: str, channel: str, t: float) -> float:
        base = super().recorded(instrument, channel, t)
        if (instrument, channel) in self.overrides:
            return base
        if instrument in THERMO_INSTRUMENTS:
            return self.model.temperature(channel, base, t)
        if instrument == "mks902b":
            return self.model.cell_pressure(base, t)
        if instrument == "tpg262" and channel == "OVC":
            return self.model.ovc_pressure(base, t)
        self.model.advance(t)
        return base

    # ------------------------------------------------------------ control
    def truth(self, t: float) -> dict:
        out = self.model.truth(t)
        out["fit_P"] = self.fit_P
        out["fit_resid_P"] = self.fit_P - out["P"] if math.isfinite(self.fit_P) else float("nan")
        return out

    def reset(self, t: float) -> None:
        self.model.reset(t)

    def set_input(self, key: str, value) -> None:
        inp = self.model.inputs
        self.model.advance(self.clock.now())         # integrate up to now with the old inputs
        if key == "take_baseline":
            inp.baseline_requests += 1
            return
        if key not in INPUT_KEYS:
            raise ValueError(f"unknown model input {key!r}")
        if key == "mw_mode" or key.startswith("mw_"):
            inp.mw_mode = "operator"
        setattr(inp, key, INPUT_KEYS[key](value))
        if key == "coldboard_heat_W":
            self._show_coldboard(float(value))

    def observe_mw(self, on: bool, freq_GHz: float, power_dB: float,
                   uca_V: float = float("nan")) -> bool:
        """The Microwave leg's REAL read-backs drive the model (no event logging
        here; the caller logs changes).  ``uca_V`` is the UCA voltage the chain
        sees (the caller maps output OFF to 5 V); NaN keeps the last value.
        Returns True if the RF state changed."""
        inp = self.model.inputs
        self.model.advance(self.clock.now())
        changed = inp.mw_mode != "operator" or inp.mw_on != bool(on)
        inp.mw_mode = "operator"
        inp.mw_on = bool(on)
        if math.isfinite(freq_GHz) and 100.0 < freq_GHz < 200.0:
            inp.mw_freq_GHz = float(freq_GHz)
        if math.isfinite(power_dB):
            inp.mw_power_dB = float(power_dB)
        if math.isfinite(uca_V):
            inp.mw_uca_V = float(uca_V)
        return changed

    def _show_coldboard(self, watts: float) -> None:
        """Make the emulated coldboard supply show the heat being applied (as if
        set at its front panel -- the Genesis coldboard IOC stays read-only)."""
        h = (self.host.hosted.get("siglent_coldboard") if self.host else None)
        if h is None:
            return
        load = h.emulator.load_ohms
        if watts > 0:
            v = math.sqrt(watts * load)
            h.emulator.set_state(voltage=v, current=min(v / load * 1.2, h.emulator.max_current),
                                 output=True)
        else:
            h.emulator.set_state(output=False)
