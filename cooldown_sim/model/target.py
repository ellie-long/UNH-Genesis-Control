"""Closed-loop target model: what the simulated target does in response to the operator.

Hybrid by design.  The past record supplies the background -- every
thermometer's real trajectory, fills, drifts and noise -- and the model adds
only the response to what the operator does *differently* from history:

    T_ch(t) = T_ch,hist(t) + dT_ch(drive_op - drive_hist, coldboard, quench)

so an operator (or autopilot) who repeats history reproduces the record
exactly, and one who deviates sees the target respond.

State (all integrated with exact exponential sub-steps of <= MAX_STEP_S):

* P, Q      deuteron vector / tensor polarization (fractions), SEPARATE
            variables.  P relaxes toward P_inf(f, B) under microwaves
            (tau_up) or toward thermal equilibrium P_TE(B, T_lattice) without
            (T1_P).  Q relaxes toward the single-spin-temperature value
            Q_B(P) with Q_heal_tau (ASSUMPTION, see model_params.yaml); ssRF
            burns move P and Q independently.
* dT_mw     lattice deviation from microwave heat (ASSUMED gain/tau)
* dT_cb     lattice deviation from coldboard heat (ASSUMED gain/tau)
* dT_mag    per-magnet-thermometer microwave deviation (FITTED gains, M2-M4)
* q         quench level in [0, 1] (ASSUMED trajectory)

Units: SI-ish lab units -- s, K, T, GHz for microwaves, MHz for NMR.
"""

from __future__ import annotations

import bisect
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from ..data.timeline import Timeline
from . import physics as ph
from .fit_params import PARAMS_FILE, drive

MAX_STEP_S = 30.0
FREQ_HOLD_S = 6 * 3600.0          # hold the last logged microwave frequency this long


def load_params(path: Path = PARAMS_FILE) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass
class Inputs:
    """Operator-side inputs (Sim Control, scenarios, or the MW leg in phase C)."""

    mw_mode: str = "history"          # history | operator
    mw_on: bool = False
    mw_freq_GHz: float = float("nan")
    mw_power_dB: float = 10.0
    coldboard_heat_W: float = 0.0
    quench: bool = False
    nmr_noise_x: float = 1.0
    nmr_phase_offset_deg: float = 0.0
    line_offset_MHz: float = 0.0
    baseline_requests: int = 0


@dataclass
class State:
    t: float
    P: float
    Q: float
    dT_mw: float = 0.0
    dT_cb: float = 0.0
    q: float = 0.0
    dT_mag: dict = field(default_factory=dict)


class TargetModel:
    def __init__(self, timeline: Timeline, t0: float, params: dict | None = None):
        self.tl = timeline
        self.p = params or load_params()
        self.dnp = self.p["dnp"]
        self.assumed = self.p["assumed"]
        self.mag_gain = {ch: g["gain_K_per_drive"] for ch, g in self.p["thermal"]["channels"].items()
                         if ch.startswith("M") and g["status"] == "fitted"}
        self.thermal_tau = float(self.p["thermal"].get("tau_s", 730.0))
        self.inputs = Inputs()
        self.burns: list = []                      # pending (t, dP, dQ), sorted by t
        self.clamped_burns = 0                     # burns projected back into the physical region
        self._lock = threading.RLock()
        self.state = self._initial(t0)

    # ----------------------------------------------------------- history
    def field_T(self, t: float) -> float:
        b = self.tl.value_at("central_field", t)
        return b if math.isfinite(b) and b > 0.5 else 0.0

    def _hist_freq(self, t: float) -> float:
        ts, vs = self.tl.samples("mw_freq_ghz", t - FREQ_HOLD_S, t)
        good = (vs > 100) & (vs < 200)
        return float(vs[good][-1]) if good.any() else float("nan")

    def hist_mw(self, t: float) -> tuple[float, float]:
        """(drive, frequency GHz) the record says the microwaves had at t."""
        return drive(self.tl.value_at("mw_rf_status", t), self.tl.value_at("mw_power_db", t)), \
            self._hist_freq(t)

    def op_mw(self, t: float) -> tuple[float, float]:
        if self.inputs.mw_mode != "operator":
            return self.hist_mw(t)
        f = self.inputs.mw_freq_GHz
        return drive(1.0 if self.inputs.mw_on else 0.0, self.inputs.mw_power_dB), \
            (f if math.isfinite(f) else self._hist_freq(t))

    def hist_lattice_T(self, t: float) -> float:
        for ch in ("T1", "vp_temp"):
            v = self.tl.value_at(ch, t)
            if math.isfinite(v) and 0.05 < v < 400:
                return v
        return float("nan")

    # ------------------------------------------------------------ state
    def _initial(self, t: float) -> State:
        p_pct = self.tl.value_at("online_p", t)
        if math.isfinite(p_pct) and abs(p_pct) < 100:
            P = p_pct / 100.0
        else:
            P = ph.te_polarization(self.field_T(t) or 5.0, self.hist_lattice_T(t) or 1.0)[0]
        return State(t=t, P=P, Q=ph.q_boltzmann(P), dT_mag={ch: 0.0 for ch in self.mag_gain})

    def reset(self, t: float) -> None:
        """Re-initialise from the record at t (after a seek)."""
        with self._lock:
            keep_q = self.state.q
            self.state = self._initial(t)
            self.state.q = keep_q if self.inputs.quench else 0.0

    def lattice_T(self, t: float | None = None) -> float:
        s = self.state
        base = self.hist_lattice_T(s.t if t is None else t)
        if not math.isfinite(base):
            base = 1.0
        T = max(base + s.dT_mw + s.dT_cb, 0.05)      # floor: never zero or negative kelvin
        return T + s.q * (self.assumed["quench"]["lattice_target_K"] - T)

    def queue_burn(self, t: float, dP: float, dQ: float) -> None:
        """Schedule a polarization change (ssRF burn) at sim time t."""
        with self._lock:
            bisect.insort(self.burns, (float(t), float(dP), float(dQ)))

    def p_inf(self, f_GHz: float, B_T: float) -> float:
        if B_T <= 0.5 or not math.isfinite(f_GHz):
            return 0.0
        delta = f_GHz - (ph.electron_larmor_GHz(B_T) + self.dnp["optimum_offset_GHz"])
        return self.dnp["P_max"] * ph.dnp_lobe(delta, self.dnp["lobe_width_GHz"])

    # ---------------------------------------------------------- advance
    def advance(self, t: float) -> State:
        with self._lock:
            s = self.state
            if t <= s.t:
                return s
            while s.t < t:
                dt = min(MAX_STEP_S, t - s.t)
                self._step(s, dt)
                s.t += dt
            return s

    def _step(self, s: State, dt: float) -> None:
        tm = s.t + 0.5 * dt
        d_hist, _f_hist = self.hist_mw(tm)
        d_op, f_op = self.op_mw(tm)
        lat, cb, qa = self.assumed["lattice"], self.assumed["coldboard"], self.assumed["quench"]

        def relax(x, target, tau):
            if not math.isfinite(target):          # never let a bad input poison the state
                return x
            return target + (x - target) * math.exp(-dt / tau)

        s.dT_mw = relax(s.dT_mw, lat["gain_K_per_drive"] * (d_op - d_hist), lat["tau_s"])
        s.dT_cb = relax(s.dT_cb, cb["gain_K_per_W"] * self.inputs.coldboard_heat_W, cb["tau_s"])
        for ch, gain in self.mag_gain.items():
            s.dT_mag[ch] = relax(s.dT_mag.get(ch, 0.0), gain * (d_op - d_hist), self.thermal_tau)
        s.q = relax(s.q, 1.0 if self.inputs.quench else 0.0,
                    qa["rise_tau_s"] if self.inputs.quench else qa["recovery_tau_s"])

        B = self.field_T(tm)
        T_lat = self.lattice_T(tm)
        # a quench destroys polarization fast (ASSUMED rate), not with the 3 h T1
        rate_off = (1.0 - s.q) / self.dnp["T1_P_s"] + s.q / qa["T1_quenched_s"]
        if d_op > 0.05 and B > 0.5 and math.isfinite(f_op) and s.q < 0.5:
            target = self.p_inf(f_op, B) * min(d_op, 1.0)
            s.P = relax(s.P, target, self.dnp["tau_up_s"])
        else:
            s.P = relax(s.P, ph.te_polarization(B, T_lat)[0] if B > 0.5 else 0.0, 1.0 / rate_off)
        s.Q = relax(s.Q, ph.q_boltzmann(s.P), self.dnp["Q_heal_tau_s"])
        while self.burns and self.burns[0][0] <= s.t + dt:
            _tb, dP, dQ = self.burns.pop(0)
            s.P, s.Q, clamped = ph.project_physical(s.P + dP, s.Q + dQ)
            self.clamped_burns += int(clamped)

    # ---------------------------------------------------------- outputs
    def temperature(self, channel: str, hist_value: float, t: float) -> float:
        """Modelled reading of thermometer ``channel`` given its recorded value."""
        s = self.advance(t)
        if not math.isfinite(hist_value):
            return hist_value
        qa = self.assumed["quench"]
        v = hist_value + s.dT_mag.get(channel, 0.0)
        target = qa["magnet_target_K"] if channel.startswith("M") else qa["fridge_target_K"]
        return v + s.q * max(target - v, 0.0)

    def cell_pressure(self, hist_torr: float, t: float) -> float:
        s = self.advance(t)
        if not math.isfinite(hist_torr):
            return hist_torr
        T_hist = self.hist_lattice_T(t)
        T_mod = (T_hist if math.isfinite(T_hist) else 1.0) + s.dT_mw + s.dT_cb
        p = hist_torr * ph.he4_vapor_pressure_ratio(T_hist, T_mod) if math.isfinite(T_hist) else hist_torr
        return p + s.q * max(self.assumed["quench"]["cell_target_Torr"] - p, 0.0)

    def ovc_pressure(self, hist_torr: float, t: float) -> float:
        s = self.advance(t)
        target = self.assumed["quench"]["ovc_target_Torr"]
        if not math.isfinite(hist_torr):
            return s.q * target if s.q > 0.01 else hist_torr
        return hist_torr + s.q * max(target - hist_torr, 0.0)

    def spin_temperature(self, P: float, B: float) -> float:
        """Vector spin temperature [K] whose Boltzmann P equals ``P`` (negative for P < 0)."""
        if B <= 0.5 or not math.isfinite(P) or abs(P) >= 1:
            return float("nan")
        lo, hi = -60.0, 60.0                 # y = h nu_L / k T_s; P(y) is monotonic
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if 2 * math.sinh(mid) / (1 + 2 * math.cosh(mid)) < P:
                lo = mid
            else:
                hi = mid
        y = 0.5 * (lo + hi)
        return ph.H_OVER_K_K_PER_MHZ * ph.deuteron_larmor_MHz(B) / y if abs(y) > 1e-12 else float("inf")

    def truth(self, t: float) -> dict:
        s = self.advance(t)
        B = self.field_T(t)
        d_op, f_op = self.op_mw(t)
        return {"P": 100.0 * s.P, "Q": 100.0 * s.Q, "T_lattice": self.lattice_T(t),
                "T_spin": self.spin_temperature(s.P, B), "B": B, "mw_drive": d_op,
                "mw_freq_GHz": f_op, "heat_cb_W": self.inputs.coldboard_heat_W,
                "quench_level": s.q, "Q_boltzmann": 100.0 * ph.q_boltzmann(s.P)}
