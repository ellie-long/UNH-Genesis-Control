"""Fit the thermal-response parameters of the target model from past cool-downs.

No measured microwave heat load or fridge time constants exist, so they are
inferred from the record: every *isolated* microwave RF on/off transition
(stable for ``STABLE_S`` either side, not within ``MIDNIGHT_GUARD_S`` of a
day-file boundary -- the DAQ logs spurious status flips there) gives one step
response per thermometer.

For each channel:

* step size per unit microwave drive -- the median over all qualifying
  transitions (sign-aligned: an OFF step counts as the negative of an ON
  step), with its interquartile range;
* time constant -- the median 1/e rise time of the T1 (cup) response;
* a channel is used only if >= ``MIN_STEPS`` steps agree in sign at least
  70 % of the time AND their interquartile range excludes zero; otherwise its
  gain is 0 and it is flagged ``unfitted``.

Microwave drive is the dimensionless fraction of the chain's full output
power (1.0 = full power at 140 GHz; the heat at full drive is the measured
62 mW reaching the helium, waveguide and mirror losses included):

    drive = RF_on x saturation(P_dB) x uca_fraction(V_UCA, f) x freq_factor(f)

* ``saturation``: the Vaunix setpoint drives the VDI amplifier/multiplier
  chain (AMC, x12, 138-142 GHz), which is SATURATED from about -5 dB on the
  Vaunix upward: every setpoint >= -5 dB gives full power (the record's +10 dB
  and -3 to -5 dB eras alike).  Below -5 dB an ASSUMED roll-off
  10**((P_dB + 5)/10) (not measured); missing or garbage power -> saturated.
* ``uca_fraction``: the actual power is set by the UCA (user-controlled
  attenuator) voltage, 0 V = full, 5 V = none; vendor test data for the
  repaired AMC (2025-10), interpolated linearly in V and frequency.
* ``freq_factor``: the chain's maximum output vs frequency (same vendor data),
  relative to its value at 140.0 GHz.

The record's ``UCA (V)`` column has not been the real UCA for years (a manual
supply drove it), so history is taken at full power whenever RF is on
(``HIST_UCA_V`` = 0 V).  The vendor curves describe the chain as repaired in
2025-10; earlier cool-downs are assumed to share its frequency response.
Gains are K per unit drive.

Output: ``cooldown_sim/model/model_params.yaml`` (derived numbers only,
with provenance -- never raw data).
"""

from __future__ import annotations

import datetime as _dt
import math
from pathlib import Path

import numpy as np
import yaml

from ..data import records
from ..data.timeline import Timeline

PARAMS_FILE = Path(__file__).with_name("model_params.yaml")
STABLE_S = 1800.0
MIDNIGHT_GUARD_S = 600.0
MIN_STEPS = 3
#: folders whose files duplicate another cool-down (would double-count steps)
DUPLICATES = {"2026-07-Cooldown"}
CHANNELS = ["T1", "T2", "T3", "vp_temp"] + [f"F{i}" for i in range(1, 12)] + [f"M{i}" for i in range(1, 6)]
COLUMNS = ["mw_rf_status", "mw_power_db", "mw_freq_ghz"] + CHANNELS


DRIVE_DEFINITION = ("RF_on x saturation(P_dB) x uca_fraction(V_UCA, f) x freq_factor(f); "
                    "saturation 1 for P_dB >= -5 (ASSUMED 10**((P_dB+5)/10) below); UCA and "
                    "frequency response from vendor test data for the repaired AMC (2025-10), "
                    "freq_factor 1 at 140 GHz; history at UCA 0 V (full power) when RF is on")

#: Vaunix setpoint (dB) from which the AMC chain is saturated (full power)
SATURATION_DB = -5.0
#: UCA voltage assumed for the record (its UCA column is not the real UCA)
HIST_UCA_V = 0.0
#: UCA voltage with no microwave output (also the RF-enable transition value)
UCA_SAFE_V = 5.0
FREQ_RANGE_GHZ = (138.0, 142.0)
REF_FREQ_GHZ = 140.0
#: hold the last logged microwave frequency this long
FREQ_HOLD_S = 6 * 3600.0

#: Output vs UCA voltage, % of maximum at ~10 dBm input, at 138 / 140 / 142 GHz --
#: vendor test data for the repaired AMC (2025-10), digitized from the vendor's
#: curve.  Approximate (the shape varies with conditions).
UCA_V = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 2.2, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.0, 3.1,
                  3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 4.0, 5.0])
UCA_FREQ_GHZ = np.array([138.0, 140.0, 142.0])
UCA_PCT = np.array([
    [100.0, 100.0, 100.0], [99.5, 98.5, 98.5], [96.5, 96.0, 95.5], [92.0, 92.0, 92.0],
    [86.5, 87.5, 88.0], [83.0, 83.5, 85.5], [77.5, 78.0, 81.0], [73.0, 74.0, 77.0],
    [66.5, 66.0, 71.0], [58.0, 55.0, 63.5], [48.5, 43.5, 55.5], [33.0, 34.0, 46.0],
    [17.0, 26.0, 36.0], [7.0, 18.0, 26.0], [2.5, 12.5, 18.0], [1.0, 7.5, 11.0],
    [0.5, 3.5, 6.0], [0.2, 1.5, 3.0], [0.0, 0.5, 1.5], [0.0, 0.2, 0.8],
    [0.0, 0.1, 0.5], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])

#: Maximum output vs frequency (mW) -- vendor test data for the repaired AMC
#: (2025-10).  Used only relative to its value at REF_FREQ_GHZ.
MAX_POWER_GHZ = np.round(np.arange(138.0, 142.01, 0.2), 1)
MAX_POWER_MW = np.array([282, 277, 289, 290, 281, 304, 317, 326, 331, 333, 325,
                         322, 305, 312, 309, 302, 294, 283, 262, 261, 252], dtype=float)


def _band(freq_GHz: float) -> float:
    """Frequency clamped to the chain's band; unknown -> the 140 GHz reference."""
    if not math.isfinite(freq_GHz):
        return REF_FREQ_GHZ
    return min(max(freq_GHz, FREQ_RANGE_GHZ[0]), FREQ_RANGE_GHZ[1])


def saturation(power_db: float) -> float:
    """Fraction of full chain output for a Vaunix setpoint (dB): 1 from
    SATURATION_DB up; below it an ASSUMED roll-off (not measured)."""
    if not (math.isfinite(power_db) and -60 <= power_db <= 30):
        return 1.0                     # missing / garbage: the usual (saturated) setting
    if power_db >= SATURATION_DB:
        return 1.0
    return 10.0 ** ((power_db - SATURATION_DB) / 10.0)


def uca_fraction(uca_V: float, freq_GHz: float = float("nan")) -> float:
    """Fraction of the chain's maximum output at UCA voltage uca_V (0 V full,
    >= 4 V none).  Non-finite voltage -> 0 (no attenuation information: no power)."""
    if not math.isfinite(uca_V):
        return 0.0
    v = min(max(uca_V, UCA_V[0]), UCA_V[-1])
    pct = [np.interp(v, UCA_V, UCA_PCT[:, j]) for j in range(len(UCA_FREQ_GHZ))]
    return float(np.interp(_band(freq_GHz), UCA_FREQ_GHZ, pct)) / 100.0


def freq_factor(freq_GHz: float) -> float:
    """Chain's maximum output at freq_GHz relative to REF_FREQ_GHZ (1 if unknown)."""
    ref = np.interp(REF_FREQ_GHZ, MAX_POWER_GHZ, MAX_POWER_MW)
    return float(np.interp(_band(freq_GHz), MAX_POWER_GHZ, MAX_POWER_MW) / ref)


def drive(rf_on: float, power_db: float, freq_GHz: float = float("nan"),
          uca_V: float = HIST_UCA_V) -> float:
    """Microwave power as a fraction of full output at 140 GHz (see module doc)."""
    if not (math.isfinite(rf_on) and rf_on > 0.5):
        return 0.0
    return saturation(power_db) * uca_fraction(uca_V, freq_GHz) * freq_factor(freq_GHz)


def hist_freq(tl: Timeline, t: float) -> float:
    """Last logged in-band microwave frequency within FREQ_HOLD_S before t (GHz), or NaN."""
    _ts, vs = tl.samples("mw_freq_ghz", t - FREQ_HOLD_S, t)
    good = (vs > 100) & (vs < 200)
    return float(vs[good][-1]) if good.any() else float("nan")


def hist_drive(tl: Timeline, t: float) -> float:
    """Drive the record says the microwaves had at t (UCA taken as HIST_UCA_V:
    the record's UCA column is ignored)."""
    return drive(tl.value_at("mw_rf_status", t), tl.value_at("mw_power_db", t), hist_freq(tl, t))


def _near_midnight(t: float) -> bool:
    d = _dt.datetime.fromtimestamp(t, records.lab_tz())
    s = d.hour * 3600 + d.minute * 60 + d.second
    return s < MIDNIGHT_GUARD_S or s > 86400 - MIDNIGHT_GUARD_S


def transitions(tl: Timeline) -> list[tuple[float, float]]:
    """(time, drive change) for isolated RF transitions."""
    t, rf = tl.samples("mw_rf_status")
    out = []
    for i in np.nonzero(np.diff(rf) != 0)[0]:
        ts = t[i + 1]
        if _near_midnight(ts):
            continue
        before = (t > ts - STABLE_S) & (t < ts)
        after = (t > ts) & (t < ts + STABLE_S)
        if before.sum() < 20 or after.sum() < 20:
            continue
        if not (np.all(rf[before] == rf[i]) and np.all(rf[after] == rf[i + 1])):
            continue
        f = hist_freq(tl, ts + 60)                  # one frequency for both sides
        d_before = drive(rf[i], tl.value_at("mw_power_db", ts - 60), f)
        d_after = drive(rf[i + 1], tl.value_at("mw_power_db", ts + 60), f)
        if abs(d_after - d_before) < 0.05:
            continue
        out.append((ts, d_after - d_before))
    return out


def step(tl: Timeline, ch: str, ts: float) -> float:
    """Step size at ts with the pre-step linear trend removed: median of the
    post-step samples minus the trend extrapolated from the 25 min before."""
    tb, vb = tl.samples(ch, ts - 1500, ts - 30)
    ta, va = tl.samples(ch, ts + 600, ts + 1700)
    if len(tb) < 8 or len(ta) < 5:
        return float("nan")
    slope, icpt = np.polyfit(tb - ts, vb, 1)
    return float(np.median(va - (slope * (ta - ts) + icpt)))


def rise_time(tl: Timeline, ch: str, ts: float) -> float:
    """1/e time of the response to the step at ts (s), or NaN."""
    t, v = tl.samples(ch, ts - 600, ts + STABLE_S)
    if len(t) < 20:
        return float("nan")
    v0 = np.nanmedian(v[t < ts])
    v1 = np.nanmedian(v[t > ts + 0.6 * STABLE_S])
    if not (np.isfinite(v0) and np.isfinite(v1)) or abs(v1 - v0) < 1e-3:
        return float("nan")
    frac = (v[t >= ts] - v0) / (v1 - v0)
    hit = np.nonzero(frac >= 1 - 1 / math.e)[0]
    return float(t[t >= ts][hit[0]] - ts) if len(hit) else float("nan")


def fit(data_root: Path, cooldowns: list[str] | None = None) -> dict:
    cds = [records.resolve_cooldown(c, data_root) for c in cooldowns] if cooldowns else \
        [p for p in records.list_cooldowns(data_root)
         if p.name[:4] >= "2021" and "Subset" not in p.name and p.name not in DUPLICATES]
    steps: dict[str, list[float]] = {c: [] for c in CHANNELS}
    taus: list[float] = []
    used = []
    for cd in cds:
        tl = Timeline(records.read_cooldown(cd, COLUMNS))
        for ts, dd in transitions(tl):
            used.append(f"{cd.name} {records.format_epoch(ts)} drive {dd:+.2f}")
            for ch in CHANNELS:
                s = step(tl, ch, ts)
                if math.isfinite(s):
                    steps[ch].append(s / dd)
            tau = rise_time(tl, "T1", ts)
            if math.isfinite(tau) and 30 < tau < STABLE_S:
                taus.append(tau)
    gains = {}
    for ch, vals in steps.items():
        v = np.array(vals)
        if len(v) < MIN_STEPS:
            gains[ch] = {"gain_K_per_drive": 0.0, "n": int(len(v)), "status": "unfitted: too few steps"}
            continue
        med = float(np.median(v))
        agree = float(np.mean(np.sign(v) == np.sign(med))) if med else 0.0
        q1, q3 = (float(x) for x in np.percentile(v, [25, 75]))
        # fitted only if the steps mostly agree in sign AND the interquartile
        # range excludes zero; otherwise the record does not constrain it
        if agree < 0.7:
            status = "unfitted: inconsistent sign"
        elif q1 * q3 <= 0:
            status = "unfitted: interquartile range spans zero"
        else:
            status = "fitted"
        gains[ch] = {"gain_K_per_drive": round(med if status == "fitted" else 0.0, 5),
                     "iqr": [round(q1, 5), round(q3, 5)], "n": int(len(v)),
                     "sign_agreement": round(agree, 2), "status": status}
    tau = float(np.median(taus)) if taus else 300.0
    return {
        "provenance": {
            "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "method": "median sign-aligned, pre-step-detrended response to isolated microwave RF transitions",
            "cooldowns": [c.name for c in cds], "transitions_used": len(used),
            "transitions": used[:200],
            "drive_definition": DRIVE_DEFINITION,
        },
        "thermal": {
            "tau_s": round(tau, 1), "tau_n": len(taus),
            "channels": gains,
        },
    }


def write(params: dict, path: Path = PARAMS_FILE) -> Path:
    header = ("# Target-model parameters fitted from past cool-downs by\n"
              "#   python -m cooldown_sim fit-params\n"
              "# Derived numbers only (no raw data).  'assumed' sections are NOT fitted:\n"
              "# no record exists for them; they make the closed loop respond plausibly\n"
              "# and are reported as assumptions in every session report.\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(params, fh, sort_keys=False, width=100)
    return path


#: Sections NOT fitted from the record.  Each value states its basis; every
#: session report lists them as assumptions.
DNP = {
    "material": "irradiated d-butanol",
    "P_max": 0.498,
    "tau_up_s": 2450.0,
    "T1_P_s": 11591.0,
    "Q_heal_tau_s": 240.0,
    "T1_Q_apparent_s": 6473.0,
    "lobe_width_GHz": 0.2,
    "optimum_offset_GHz": 0.0,
    "basis": {
        "P_max, tau_up_s": "Spin-1-Rates-Fit results/fit_asym_pq.json steady_state.Pn and "
                           "legacy_R_T1.tau (Dec 2025 d-butanol spin-up, B=5.077 T, T=0.997 K)",
        "T1_P_s": "Spin-1-Rates-Fit results/t1_analysis.json T1_P (microwave-off relaxation)",
        "T1_Q_apparent_s": "Spin-1-Rates-Fit results/t1_analysis.json T1_Q_apparent -- NOT a "
                           "model input: it is reproduced by Q tracking Q_B(P(t)) as P relaxes "
                           "(Q ~ P^2 decays at ~T1_P/2); kept as a cross-check",
        "Q_heal_tau_s": "ASSUMED: time for Q to return to the single-temperature curve after it "
                        "is pushed off it (ssRF burns); 4 min, the McClellan test-cooldown "
                        "generator's hole-healing default. Short compared with tau_up, so "
                        "TE-start spin-ups stay on the Boltzmann curve (Spin-1-Rates-Fit finding)",
        "optimum_offset_GHz": "best positive P observed at the free-electron frequency "
                              "28.025 GHz/T x B within the 0.1 GHz binning: 138.4 GHz at 4.94 T "
                              "(2025-11, 2026-06), 142.3 GHz at 5.08 T (2025-12)",
        "lobe_width_GHz": "ASSUMED: lobe shape not resolvable from the record (operating "
                          "frequencies were rarely varied). Width 0.2 GHz gives the few-percent "
                          "positive P seen 0.3-0.4 GHz below the optimum; the negative lobe is "
                          "placed on the high-frequency side because none appears below it",
        "Q relaxation": "ASSUMED single-spin-temperature target Q_B(P) = 2 - sqrt(4 - 3 P^2), "
                        "approached with Q_heal_tau_s (P and Q are separate state variables)",
    },
}

#: Heat bookkeeping (watts) -- measured, not fitted from transitions.
HEAT = {
    "mw_W_at_full_drive": 0.062,
    "W_per_slm": 0.0595,
    "flow_tau_s": 15.0,
    "basis": {
        "mw_W_at_full_drive": "MEASURED: 2024-12-17 23:33-23:56 logbook (UNH_Poltarg_Cooldown_Notes): "
                              "UCA toggled 5 V <-> 0 V gave a ~1 slm Roots-flow differential, "
                              "60-62 mW absorbed cold (Farooq: 16.06 SLPM/W). Sept 2024 with the "
                              "old stick was higher. Automated step fits cannot see it (flows "
                              "around logged transitions are dominated by other operations).",
        "W_per_slm": "16.8 slpm per W at 1 K (C. Keith, PSTP 2024; Spin-1-Rates-Fit "
                     "holeburn/dec15_thermal.py) = 59.5 mW/slm; first-principles latent heat "
                     "62.5 mW/slm",
        "flow_tau_s": "Roots-flow impulse response 10-15 s (Spin-1-Rates-Fit afp_holeburn_technote, "
                      "Dec-15-2025 isolated 10-s burns)",
    },
}

ASSUMED = {
    "cup": {
        "gain_K_per_W": 10.2, "tau_s": 730.0,
        "basis": "PARTLY FITTED: cup thermometers (T1/T2/T3) are much more sensitive to microwaves than the "
                 "helium bath (lab knowledge). Gain from the FITTED T3 step response "
                 "(+0.635 K per full-drive transition, n=7, IQR excludes zero) divided by "
                 "the measured 62 mW; T1 steps (+0.25..+0.62 K) agree. tau = median T1 rise "
                 "time (n=2): weakly constrained.",
    },
    "bath": {
        "gain_K_per_W": 2.3, "tau_s": 730.0,
        "basis": "ASSUMED: the bath (vapor-pressure thermometer, cell pressure) responds much "
                 "less than the cup. Median vapor-pressure step +0.144 K per full drive "
                 "(inconsistent sign, n=13) over 62 mW.",
    },
    "coldboard": {
        "to": "bath",
        "basis": "ASSUMED: coldboard heat goes into the bath (raises bath and, through it, "
                 "the cup) and into boil-off; no record exists. NEGLECTED in normal running: "
                 "the cold-board supply biases the varactors and no heating from it has been "
                 "noticed (Ells, 2026-10-15), so coldboard_heat_W stays 0 W unless a scenario "
                 "or Sim Control injects heat as a test fault.",
    },
    "quench": {
        "fridge_target_K": 25.0, "magnet_target_K": 15.0, "lattice_target_K": 4.5,
        "rise_tau_s": 60.0, "recovery_tau_s": 900.0, "T1_quenched_s": 30.0,
        "cell_target_Torr": 760.0, "ovc_target_Torr": 1.0e-3,
        "basis": "ASSUMED quench-like warm-up for exercising interlocks and alarms; not a "
                 "model of a real quench.",
    },
}


def build_params(fitted: dict) -> dict:
    return {**fitted, "dnp": DNP, "heat": HEAT, "assumed": ASSUMED}
