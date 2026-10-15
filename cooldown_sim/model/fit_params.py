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

Microwave drive is a dimensionless, *uncalibrated* proxy:
``drive = RF_on * 10**((P_dB - 10)/10)`` (1.0 = RF on at the usual 10 dB
setting).  Gains are therefore K per unit drive, not K/W.

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
CHANNELS = ["T1", "vp_temp"] + [f"F{i}" for i in range(1, 12)] + [f"M{i}" for i in range(1, 6)]
COLUMNS = ["mw_rf_status", "mw_power_db"] + CHANNELS


def drive(rf_on: float, power_db: float) -> float:
    if not (math.isfinite(rf_on) and rf_on > 0.5):
        return 0.0
    p = power_db if math.isfinite(power_db) and -60 <= power_db <= 30 else 10.0
    return 10.0 ** ((p - 10.0) / 10.0)


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
        d_before = drive(rf[i], tl.value_at("mw_power_db", ts - 60))
        d_after = drive(rf[i + 1], tl.value_at("mw_power_db", ts + 60))
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
            "drive_definition": "RF_on * 10**((P_dB - 10)/10); uncalibrated, 1.0 = RF on at 10 dB",
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

ASSUMED = {
    "lattice": {
        "gain_K_per_drive": 0.4, "tau_s": 730.0,
        "basis": "ASSUMED: the record does not constrain the microwave thermal response "
                 "(fitted T1 sign agreement ~0.6). Anchored on the two cleanest isolated "
                 "transitions, 2026-06-17 10:41 (on, T1 +0.62 K) and 2026-06-18 18:49 "
                 "(off, T1 -0.36 K); tau from the median T1 rise time (n=2).",
    },
    "coldboard": {
        "gain_K_per_W": 0.3, "tau_s": 300.0,
        "basis": "ASSUMED: no coldboard heat-load record exists. Applied to the lattice "
                 "(cup) temperature only.",
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
    return {**fitted, "dnp": DNP, "assumed": ASSUMED}
