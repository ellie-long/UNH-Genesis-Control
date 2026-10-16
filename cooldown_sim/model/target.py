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
* dT_cup    cup (T1/T2/T3) deviation from heat deposited at the target
            (microwave, ssRF): ~10 K/W from the fitted T3 response
* dT_bath   helium-bath deviation from all heat (incl. coldboard): ~2 K/W
* dflow     extra Roots-flow boil-off, heat / 59.5 mW per slm, tau ~15 s
* dT_mag    per-magnet-thermometer microwave deviation (FITTED gains, M2-M4)
* q         quench level in [0, 1] (ASSUMED trajectory)

Microwave drive (``fit_params.drive``) is the fraction of the chain's full
output at 140 GHz: RF on x Vaunix saturation (full power from -5 dB up) x UCA
attenuation (0 V full, 5 V none; vendor curve) x the chain's frequency
response.  The history UCA comes from the cool-down logbooks
(``uca_history``: 5 V at RF ON until the logbook says it was lowered, RF OFF
resets, unlogged RF-on periods full power after 5 min); the operator side
uses the OBSERVED UCA voltage.  The
DNP pumping strength is the drive without the frequency response: P_max and
the lobe come from spin-ups at the operating frequencies, so the chain's
output there is already inside them.

Heat is bookkept in watts: microwaves 62 mW at full drive (MEASURED by the
Roots-flow differential, 2024-12-17), coldboard heat as set, ssRF burns as
queued by the ssRF leg.  Varactor-bias heating from the cold-board supply is
deliberately neglected (none noticed in practice; Ells, 2026-10-15):
``coldboard_heat_W`` is 0 W unless a scenario or Sim Control injects heat as a
test fault.  To model it later, set it from the supply's V x I read-backs.  The cup temperature is the DNP lattice temperature.

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
from . import spin1
from .fit_params import PARAMS_FILE, UCA_SAFE_V, drive, freq_factor, hist_drive, hist_freq

MAX_STEP_S = 30.0


#: per cool-down DNP parameters (material, radical, field calibration differ)
COOLDOWN_DNP_FILE = Path(__file__).with_name("cooldown_dnp.yaml")
#: dnp keys a cool-down entry may override
DNP_KEYS = ("P_max", "tau_up_s", "T1_P_s", "optimum_offset_GHz", "lobe_width_GHz",
            "neg_scale", "neg_side")


def cooldown_dnp(name: str | None, path: Path | None = None) -> dict | None:
    """The fitted DNP entry for a cool-down ('2026-06' or '2026-06-Cooldown'), or None."""
    path = path or COOLDOWN_DNP_FILE
    if not name or not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        table = (yaml.safe_load(fh) or {}).get("cooldowns") or {}
    for key, entry in table.items():
        if key == name or key.split("-Cooldown")[0] == name.split("-Cooldown")[0]:
            return entry
    return None


def load_params(path: Path = PARAMS_FILE, cooldown: str | None = None) -> dict:
    """Model parameters; with ``cooldown``, its fitted DNP values replace the defaults."""
    with open(path, encoding="utf-8") as fh:
        p = yaml.safe_load(fh)
    entry = cooldown_dnp(cooldown)
    if entry:
        p["dnp"].update({k: entry[k] for k in DNP_KEYS if k in entry})
        p["dnp"]["source"] = f"cooldown_dnp.yaml: {cooldown}"
    return p


#: ASSUMED: polarization lifetime once the field is (nearly) gone, e.g. after a
#: ramp-down -- the deuteron T1 collapses at low field; minutes, not hours
LOW_FIELD_T1_S = 120.0

#: central field per magnet supply amp, as the DAQ computed it (0.10274 T/A in
#: every cool-down that logs both; ~48.7 A for 5.0 T)
FIELD_T_PER_A = 0.10274113

#: NMR sweep span (MHz) when neither the operator nor the record gives one
NMR_DEFAULT_SPAN_MHZ = 0.4

#: the LabVIEW vapor-pressure temperature reads exactly this when Manometer-Lo
#: reads 0 (a dropout, not a bath temperature): treat it as missing
VP_DROPOUT_K = 0.650

#: Manometer-Hi (MKS 902B) to Manometer-Lo (XCG, 10 Torr F.S.) crossover for
#: the vapor-pressure temperature (Ells; the record's LabVIEW ranges agree)
VP_CROSSOVER_TORR = 5.0

@dataclass
class Inputs:
    """Operator-side inputs (Sim Control, scenarios, or the MW leg in phase C)."""

    mw_mode: str = "history"          # history | operator
    mw_on: bool = False
    mw_freq_GHz: float = float("nan")
    mw_power_dB: float = 10.0
    #: UCA voltage (V): 0 full power, 5 none.  Starts at the safe 5 V, like the
    #: emulated UCA supply (cli_run.SUPPLY_INITIAL)
    mw_uca_V: float = UCA_SAFE_V
    coldboard_heat_W: float = 0.0
    quench: bool = False
    nmr_noise_x: float = 1.0
    nmr_phase_offset_deg: float = 0.0
    line_offset_MHz: float = 0.0
    #: NMR sweep window set by the operator (MHz); 0 = follow the record
    nmr_center_MHz: float = 0.0
    nmr_span_MHz: float = 0.0
    baseline_requests: int = 0


@dataclass
class State:
    t: float
    P: float
    Q: float
    dT_cup: float = 0.0
    dT_bath: float = 0.0
    dT_cb_bath: float = 0.0
    dflow: float = 0.0
    q: float = 0.0
    dT_mag: dict = field(default_factory=dict)
    burn_heat_W: float = 0.0
    Pe: float = float("nan")          # electron polarization (spin-1 rate model only)


class TargetModel:
    def __init__(self, timeline: Timeline, t0: float, params: dict | None = None,
                 cooldown: str | None = None):
        self.tl = timeline
        self.cooldown = cooldown          # past cool-down replayed (selects the material)
        self._spin1: dict = {}            # material -> spin1.Spin1 (None: not available)
        self._dnp_by_material: dict = {}  # material -> phenomenological DNP values
        self.p = params or load_params(cooldown=cooldown)
        self.dnp = self.p["dnp"]
        self.assumed = self.p["assumed"]
        self.mag_gain = {ch: g["gain_K_per_drive"] for ch, g in self.p["thermal"]["channels"].items()
                         if ch.startswith("M") and g["status"] == "fitted"}
        self.thermal_tau = float(self.p["thermal"].get("tau_s", 730.0))
        self.heat = self.p["heat"]
        self.inputs = Inputs()
        self.burns: list = []                      # pending (t, dP, dQ), sorted by t
        self.burn_heat: list = []                  # (t_start, t_end, watts) from the ssRF leg
        self.clamped_burns = 0                     # burns projected back into the physical region
        self._lock = threading.RLock()
        self.state = self._initial(t0)

    # ----------------------------------------------------------- history
    def magic_GHz(self, sign: str, t: float) -> float:
        """The DAQ-style 'magic' frequency at t: '+' best P-up, '-' best P-down."""
        B = self.field_T(t)
        return spin1.magic_ratio(sign, self.material(t)) * B if B > 0.5 else float("nan")

    def material(self, t: float) -> str:
        """Target material at t: the replayed cool-down's schedule, else the default."""
        return spin1.material_for(self.cooldown, t)

    def spin1_engine(self, t: float):
        """The spin-1 rate model for the material at t, or None (phenomenological DNP)."""
        if self.p["dnp"].get("engine") == "phenomenological" or spin1.uses_cooldown_fit(self.cooldown, t):
            return None
        name = self.material(t)
        if name not in self._spin1:
            spec = spin1.load_table()["materials"].get(name) or {}
            ok = spin1.available() and "rates" in spec
            self._spin1[name] = spin1.Spin1(name) if ok else None
        return self._spin1[name]

    def nmr_window(self, t: float) -> tuple[float, float]:
        """(centre, span) MHz of the NMR sweep: the operator's setting if any,
        else the record's, else the deuteron Larmor frequency and a default span."""
        cf, span = self.inputs.nmr_center_MHz, self.inputs.nmr_span_MHz
        if not (math.isfinite(cf) and 5.0 < cf < 300.0):
            cf = self.tl.value_at("nmr_centfreq", t)
        if not (math.isfinite(span) and 0.05 < span < 5.0):
            span = self.tl.value_at("nmr_span", t)
        if not (math.isfinite(cf) and 5.0 < cf < 300.0):
            B = self.field_T(t)
            cf = ph.deuteron_larmor_MHz(B) if B > 0.5 else 32.283
        if not (math.isfinite(span) and 0.05 < span < 5.0):
            span = NMR_DEFAULT_SPAN_MHZ
        return cf, span

    def field_T(self, t: float) -> float:
        """Central field (T): the record's, else from the magnet supply current.

        The DAQ computed "Central Mag Field" from the supply current (exactly
        FIELD_T_PER_A in every cool-down that logs both); 2020-21 log only the
        current."""
        b = self.tl.value_at("central_field", t)
        if not math.isfinite(b):
            b = self.tl.value_at("magnet_current", t) * FIELD_T_PER_A
        return b if math.isfinite(b) and b > 0.5 else 0.0

    def _hist_freq(self, t: float) -> float:
        return hist_freq(self.tl, t)

    def hist_mw(self, t: float) -> tuple[float, float]:
        """(drive, frequency GHz) the microwaves had at t: RF, power and frequency
        from the record, the UCA from the logbook timeline (``hist_drive``)."""
        return hist_drive(self.tl, t), self._hist_freq(t)

    def op_mw(self, t: float) -> tuple[float, float]:
        """(drive, frequency GHz) the operator's microwaves have at t, from RF
        on/off, the Vaunix power, the frequency and the observed UCA voltage."""
        if self.inputs.mw_mode != "operator":
            return self.hist_mw(t)
        f = self.inputs.mw_freq_GHz
        f = f if math.isfinite(f) else self._hist_freq(t)
        return drive(1.0 if self.inputs.mw_on else 0.0, self.inputs.mw_power_dB, f,
                     self.inputs.mw_uca_V), f

    def hist_lattice_T(self, t: float) -> float:
        """Recorded cup temperature (T1, else T3, else the bath)."""
        return self._first_valid_T(("T1", "T3", "vp_temp"), t)

    def hist_bath_T(self, t: float) -> float:
        """Recorded bath temperature: T90 from the cell manometers (the lab's
        offline conversion, trusted over LabVIEW's online vp_temp), else
        vp_temp, else T1."""
        T = self.hist_vp_T90(t)
        return T if math.isfinite(T) else self._first_valid_T(("vp_temp", "T1"), t)

    def hist_vp_T90(self, t: float) -> float:
        """He-4 T90 from the recorded cell pressure, crossing from Manometer-Hi
        to Manometer-Lo below VP_CROSSOVER_TORR as the lab's DAQ does."""
        hi = self.tl.value_at("manometer_hi", t)
        if not math.isfinite(hi):
            hi = self.tl.value_at("manometer", t)       # 2020: single manometer
        lo = self.tl.value_at("manometer_lo", t)
        if math.isfinite(hi) and hi >= VP_CROSSOVER_TORR:
            p = hi
        elif math.isfinite(lo) and lo > 0:
            p = lo
        else:
            p = hi
        T = ph.he4_t90_K(p)
        return T if 0.5 < T < 5.0 else float("nan")

    def _first_valid_T(self, channels, t: float) -> float:
        for ch in channels:
            v = self.tl.value_at(ch, t)
            if ch == "vp_temp" and abs(v - VP_DROPOUT_K) < 5e-4:
                continue
            if math.isfinite(v) and 0.05 < v < 400:
                return v
        return float("nan")

    def mw_heat_W(self, drive_value: float) -> float:
        return self.heat["mw_W_at_full_drive"] * max(drive_value, 0.0)

    def burn_heat_W(self, t: float) -> float:
        return sum(w for a, b, w in self.burn_heat if a <= t < b)

    def queue_burn_heat(self, t_start: float, t_end: float, watts: float) -> None:
        with self._lock:
            self.burn_heat.append((float(t_start), float(t_end), float(watts)))
            self.burn_heat = [x for x in self.burn_heat if x[1] > self.state.t - 3600.0]

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
        # the fitted cup response already includes the bath's share of target heat;
        # coldboard heat reaches the cup only through the bath.  The cup is never
        # colder than the (modelled) bath it sits in.
        T = base + s.dT_cup + s.dT_cb_bath
        bath = self.hist_bath_T(s.t if t is None else t)
        if math.isfinite(bath):
            T = max(T, bath + s.dT_bath)
        T = max(T, 0.05)                              # floor: never zero or negative kelvin
        return T + s.q * (self.assumed["quench"]["lattice_target_K"] - T)

    def queue_burn(self, t: float, dP: float, dQ: float) -> None:
        """Schedule a polarization change (ssRF burn) at sim time t."""
        with self._lock:
            bisect.insort(self.burns, (float(t), float(dP), float(dQ)))

    def dnp_at(self, t: float) -> dict:
        """Phenomenological DNP values at t: the material's own block if it has one
        (spin1_materials.yaml), else the defaults / this cool-down's fitted values."""
        if spin1.uses_cooldown_fit(self.cooldown, t):
            return self.dnp
        name = self.material(t)
        if name not in self._dnp_by_material:
            spec = (spin1.load_table().get("materials") or {}).get(name) or {}
            self._dnp_by_material[name] = {**self.dnp, **(spec.get("phenomenological") or {})}
        return self._dnp_by_material[name]

    def p_inf(self, f_GHz: float, B_T: float, dnp: dict | None = None) -> float:
        dnp = dnp or self.dnp
        if B_T <= 0.5 or not math.isfinite(f_GHz):
            return 0.0
        delta = f_GHz - (ph.electron_larmor_GHz(B_T) + dnp["optimum_offset_GHz"])
        return dnp["P_max"] * ph.dnp_lobe(delta, dnp["lobe_width_GHz"],
                                          dnp.get("neg_scale", 1.0), dnp.get("neg_side", 1))

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
        cup, bath, qa = self.assumed["cup"], self.assumed["bath"], self.assumed["quench"]

        def relax(x, target, tau):
            if not math.isfinite(target):          # never let a bad input poison the state
                return x
            return target + (x - target) * math.exp(-dt / tau)

        # heat deposited at the target relative to history (W); coldboard into the bath
        q_target = self.mw_heat_W(d_op) - self.mw_heat_W(d_hist) + self.burn_heat_W(tm)
        q_cb = self.inputs.coldboard_heat_W
        s.burn_heat_W = self.burn_heat_W(tm)
        s.dT_cup = relax(s.dT_cup, cup["gain_K_per_W"] * q_target, cup["tau_s"])
        s.dT_bath = relax(s.dT_bath, bath["gain_K_per_W"] * (q_target + q_cb), bath["tau_s"])
        s.dT_cb_bath = relax(s.dT_cb_bath, bath["gain_K_per_W"] * q_cb, bath["tau_s"])
        s.dflow = relax(s.dflow, (q_target + q_cb) / self.heat["W_per_slm"], self.heat["flow_tau_s"])
        for ch, gain in self.mag_gain.items():
            s.dT_mag[ch] = relax(s.dT_mag.get(ch, 0.0), gain * (d_op - d_hist), self.thermal_tau)
        s.q = relax(s.q, 1.0 if self.inputs.quench else 0.0,
                    qa["rise_tau_s"] if self.inputs.quench else qa["recovery_tau_s"])

        B = self.field_T(tm)
        T_lat = self.lattice_T(tm)
        # a quench destroys polarization fast (ASSUMED rate), not with the 3 h T1
        dnp = self.dnp_at(tm)
        rate_off = (1.0 - s.q) / dnp["T1_P_s"] + s.q / qa["T1_quenched_s"]
        if B <= 0.5:
            rate_off = max(rate_off, 1.0 / LOW_FIELD_T1_S)
        pump = d_op / freq_factor(f_op)                 # drive without the frequency response
        rates = self.spin1_engine(tm) if (B > 0.5 and s.q < 0.5) else None
        if rates is not None:
            # spin-1 rate equations: P, Q and the electron from the fitted rates.  Their
            # thermal targets use the bath (vapor-pressure) temperature, as in the fits;
            # the cup thermometer reads warmer under microwaves
            T_bath = self.hist_bath_T(tm) + s.dT_bath
            T_spin = T_bath if math.isfinite(T_bath) and T_bath > 0.05 else T_lat
            if s.q > 0:
                T_spin = T_spin + s.q * (qa["lattice_target_K"] - T_spin)
            st = spin1.Spin1State(s.Pe, s.P, s.Q) if math.isfinite(s.Pe) else rates.initial(s.P, B, T_spin, s.Q)
            st = rates.step(st, dt, f_GHz=f_op, B_T=B, T_K=T_spin, power=min(pump, 1.0))
            s.P, s.Q, s.Pe = st.Pn, st.Qn, st.Pe
        else:
            s.Pe = float("nan")
            if pump > 0.05 and B > 0.5 and math.isfinite(f_op) and s.q < 0.5:
                target = self.p_inf(f_op, B, dnp) * min(pump, 1.0)
                s.P = relax(s.P, target, dnp["tau_up_s"])
            else:
                s.P = relax(s.P, ph.te_polarization(B, T_lat)[0] if B > 0.5 else 0.0, 1.0 / rate_off)
            s.Q = relax(s.Q, ph.q_boltzmann(s.P), dnp["Q_heal_tau_s"])
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
        T_hist = self.hist_bath_T(t)
        T_mod = max((T_hist if math.isfinite(T_hist) else 1.0) + s.dT_bath, 0.05)
        p = hist_torr
        if math.isfinite(T_hist):
            ratio = ph.he4_p90_torr(T_mod) / ph.he4_p90_torr(T_hist)
            p = hist_torr * (ratio if math.isfinite(ratio) else ph.he4_vapor_pressure_ratio(T_hist, T_mod))
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
        T_bath = self.hist_bath_T(t)
        heat_total = (self.mw_heat_W(d_op) + self.inputs.coldboard_heat_W + s.burn_heat_W)
        return {"P": 100.0 * s.P, "Q": 100.0 * s.Q, "T_lattice": self.lattice_T(t),
                "T_bath": (T_bath + s.dT_bath) if math.isfinite(T_bath) else float("nan"),
                "T_spin": self.spin_temperature(s.P, B), "B": B, "mw_drive": d_op,
                "mw_freq_GHz": f_op, "heat_cb_W": self.inputs.coldboard_heat_W,
                "heat_total_W": heat_total, "boiloff_delta_slm": s.dflow,
                "quench_level": s.q, "Q_boltzmann": 100.0 * ph.q_boltzmann(s.P),
                "magic_plus_GHz": self.magic_GHz("+", t), "magic_minus_GHz": self.magic_GHz("-", t),
                "nmr_window_center_MHz": self.nmr_window(t)[0],
                "nmr_window_span_MHz": self.nmr_window(t)[1]}
