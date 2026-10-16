"""Fit the thermal-response parameters of the target model from past cool-downs.

No measured fridge time constants exist, so the thermal response to
microwaves is inferred from the record at the moments the microwave POWER
changed.  That is the UCA, not the RF switch: RF is switched ON with the UCA
at 5 V (no power) and the operator lowers it by hand later, so an RF on/off
flip is usually not a power step at all.  The steps used are the
logbook-STATED UCA changes (``uca_logbook.yaml`` via ``uca_history``) while
RF is on, plus the record's own UCA steps in the era when the DAQ logged the
real UCA (2020-2021).  Inferred logbook entries shape the history drive but
never select a step.  A step qualifies when

* the setting before it is known (a logbook entry, the 5 V of the RF-ON
  procedure, or the record) -- not the unlogged-period fallback;
* the drive changes by >= ``MIN_DRIVE_STEP`` and no other change (UCA, RF
  ON/OFF) lies within ``MIN_ISOLATION_S`` either side.  (The midnight guard of
  the RF method is not needed: it protected against the DAQ's spurious RF
  status flips at day-file boundaries, which no longer define a step.)

Logbook times are when the entry was written: the action is typically 0-4
min earlier, occasionally up to a minute later.  The cup thermometers respond
within ~20 s, so each logbook step is placed at the largest one-sample jump
of T1 (else T3) within [t - ``LOG_LEAD_S``, t + ``LOG_LAG_S``] (the absolute
jump: the expected sign plays no part); the step is then measured as the
median after it minus the pre-step level (linear trend extrapolated when the
quiet stretch before is >= 10 min, else its median), each window kept
``WINDOW_GUARD_S`` clear of the step and of its neighbours.

For each channel:

* step size per unit microwave drive -- the median over all qualifying steps
  (sign-aligned by the drive change), with its interquartile range;
* time constant -- the median 1/e rise time of the T1 (cup) response, from
  steps with a quiet ``STABLE_S`` after them;
* a channel is used only if >= ``MIN_STEPS`` steps agree in sign at least
  70 % of the time AND their interquartile range excludes zero; otherwise its
  gain is 0 and it is flagged ``unfitted``.

``method="rf"`` (``fit-params --method rf``) keeps the earlier fit to isolated
RF on/off transitions (``STABLE_S`` either side) for comparison only.

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

The record's ``UCA (V)`` column has not been the real UCA since about 2022 (a
manual supply drives it), so the history UCA comes from the logbooks
(``uca_history``: 5 V at RF ON until the logbook says it was lowered; RF OFF
resets; unlogged RF-on periods reach full power after 5 min).  The vendor
curves describe the chain as repaired in 2025-10; earlier cool-downs are
assumed to share its frequency response.  Gains are K per unit drive.

Output: ``cooldown_sim/model/model_params.yaml`` (derived numbers only,
with provenance -- never raw data).
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from ..data import records
from ..data.timeline import Timeline
from . import uca_history

PARAMS_FILE = Path(__file__).with_name("model_params.yaml")
STABLE_S = 1800.0
MIDNIGHT_GUARD_S = 600.0
MIN_STEPS = 3
#: a UCA step needs this long free of other changes on both sides
MIN_ISOLATION_S = 240.0
#: a logbook entry is written up to this long after the action ...
LOG_LEAD_S = 240.0
#: ... or up to this long before it
LOG_LAG_S = 90.0
#: measurement windows stay this far from the (placed) step and its neighbours
WINDOW_GUARD_S = 40.0
#: smallest UCA drive step fitted: a partial step (e.g. 1 V -> 2 V, 0.09) is within the noise
MIN_DRIVE_STEP = 0.25
#: folders whose files duplicate another cool-down (would double-count steps)
DUPLICATES = {"2026-07-Cooldown"}
CHANNELS = ["T1", "T2", "T3", "vp_temp"] + [f"F{i}" for i in range(1, 12)] + [f"M{i}" for i in range(1, 6)]
COLUMNS = (["mw_rf_status", "mw_power_db", "mw_freq_ghz", "uca_v", "manometer_hi", "manometer_lo",
            "manometer"] + CHANNELS)
#: cup thermometers used to place a logbook step in time
CUP = ("T1", "T3")


DRIVE_DEFINITION = ("RF_on x saturation(P_dB) x uca_fraction(V_UCA, f) x freq_factor(f); "
                    "saturation 1 for P_dB >= -5 (ASSUMED 10**((P_dB+5)/10) below); UCA and "
                    "frequency response from vendor test data for the repaired AMC (2025-10), "
                    "freq_factor 1 at 140 GHz; history UCA from the cool-down logbooks "
                    "(uca_logbook.yaml: 5 V at RF ON until lowered, RF OFF resets, unlogged "
                    "RF-on periods full power after 5 min; the record's UCA column in 2020-2021)")

#: Vaunix setpoint (dB) from which the AMC chain is saturated (full power)
SATURATION_DB = -5.0
#: UCA voltage with no microwave output (also the RF-enable transition value)
UCA_SAFE_V = uca_history.UCA_SAFE_V
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
          uca_V: float = 0.0) -> float:
    """Microwave power as a fraction of full output at 140 GHz (see module doc)."""
    if not (math.isfinite(rf_on) and rf_on > 0.5):
        return 0.0
    return saturation(power_db) * uca_fraction(uca_V, freq_GHz) * freq_factor(freq_GHz)


def hist_freq(tl: Timeline, t: float) -> float:
    """Last logged in-band microwave frequency within FREQ_HOLD_S before t (GHz), or NaN."""
    _ts, vs = tl.samples("mw_freq_ghz", t - FREQ_HOLD_S, t)
    good = (vs > 100) & (vs < 200)
    return float(vs[good][-1]) if good.any() else float("nan")


def hist_uca(tl: Timeline, t: float) -> uca_history.Setting:
    """History UCA setting at t (logbook timeline; see ``uca_history``)."""
    return uca_history.for_timeline(tl).setting(t)


def hist_drive(tl: Timeline, t: float) -> float:
    """Drive the record says the microwaves had at t: RF status, Vaunix power and
    frequency from the record, the UCA from the logbook timeline (``hist_uca``)."""
    return drive(tl.value_at("mw_rf_status", t), tl.value_at("mw_power_db", t), hist_freq(tl, t),
                 hist_uca(tl, t).uca_V)


def _near_midnight(t: float) -> bool:
    d = _dt.datetime.fromtimestamp(t, records.lab_tz())
    s = d.hour * 3600 + d.minute * 60 + d.second
    return s < MIDNIGHT_GUARD_S or s > 86400 - MIDNIGHT_GUARD_S


def transitions(tl: Timeline) -> list[tuple[float, float]]:
    """(time, drive change) for isolated RF transitions -- the earlier method,
    kept for comparison (``method="rf"``): it takes every RF flip as a full
    power step, which the UCA procedure makes untrue."""
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


METHODS = {
    "uca": "median sign-aligned, pre-step-detrended response to isolated logbook-stated (and "
           "2020-2021 record) UCA power steps while RF is on, placed on the cup response",
    "rf": "median sign-aligned, pre-step-detrended response to isolated microwave RF transitions "
          "(earlier method, every RF flip taken as a full-power step)",
}


@dataclass(frozen=True)
class Step:
    """One microwave power step to fit: placed time, drive change, measurement windows."""

    t: float                            # step time (placed on the cup response for logbook steps)
    t_log: float                        # logbook / record time it came from
    dd: float                           # drive change (after - before)
    pre: tuple[float, float]            # pre-step window (epoch s)
    post: tuple[float, float]           # post-step window
    quiet_after_s: float                # time to the next change
    label: str


def place(tl: Timeline, lo: float, hi: float) -> float:
    """Midpoint of the largest one-sample jump of the first cup thermometer with
    data in [lo, hi] (NaN if none) -- where a logbook step really happened."""
    for ch in CUP:
        t, v = tl.samples(ch, lo - 60.0, hi)
        ok = v < 100.0                                   # drop the DAQ's out-of-range sentinels
        t, v = t[ok], v[ok]
        if len(t) < 3:
            continue
        tm = 0.5 * (t[1:] + t[:-1])
        d = np.where((tm >= lo) & (tm <= hi), np.abs(np.diff(v)), -1.0)
        if d.max() >= 0:
            return float(tm[int(np.argmax(d))])
    return float("nan")


def uca_steps(tl: Timeline, bases=("stated", "record")) -> list[Step]:
    """Isolated microwave power steps at UCA changes while RF is on (module doc)."""
    hist = uca_history.for_timeline(tl)
    changes = hist.changes()
    # place every logbook change, so neighbours are known as well as the step itself
    when: dict[float, tuple[float, float]] = {}         # logbook t -> (earliest, latest) real time
    for c in changes:
        if c.exact:
            when[c.t] = (c.t, c.t)
            continue
        tp = place(tl, max(c.t - LOG_LEAD_S, c.prev_t + 30.0), min(c.t + LOG_LAG_S, c.next_t - 30.0))
        when[c.t] = (tp, tp) if math.isfinite(tp) else (c.t - LOG_LEAD_S, c.t + LOG_LAG_S)
    out = []
    for c in changes:
        if c.basis not in bases or c.before not in ("stated", "inferred", "procedure", "record"):
            continue
        lo, hi = when[c.t]
        if lo != hi:                                     # could not be placed
            continue
        ts = lo
        prev = when.get(c.prev_t, (c.prev_t, c.prev_t))[1]
        nxt = when.get(c.next_t, (c.next_t, c.next_t))[0]
        if ts - prev < MIN_ISOLATION_S or nxt - ts < MIN_ISOLATION_S:
            continue
        f = hist_freq(tl, ts + 60.0)
        p_db = tl.value_at("mw_power_db", ts)
        dd = drive(1.0, p_db, f, c.v_after) - drive(1.0, p_db, f, c.v_before)
        if abs(dd) < MIN_DRIVE_STEP:
            continue
        post_end = min(nxt - WINDOW_GUARD_S, ts + STABLE_S - 100.0)
        post_start = ts + (600.0 if post_end - ts >= 1200.0 else WINDOW_GUARD_S)
        out.append(Step(ts, c.t, dd, (max(prev + WINDOW_GUARD_S, ts - 1500.0), ts - WINDOW_GUARD_S),
                        (post_start, post_end), nxt - ts,
                        f"{c.basis} {c.v_before:.2f}->{c.v_after:.2f} V"))
    return out


def rf_steps(tl: Timeline) -> list[Step]:
    """The earlier method's steps (isolated RF flips at full drive), as Steps."""
    return [Step(ts, ts, dd, (ts - 1500.0, ts - 30.0), (ts + 600.0, ts + 1700.0), STABLE_S, "RF flip")
            for ts, dd in transitions(tl)]


def step_size(tl: Timeline, ch: str, s: Step) -> float:
    """Response of ``ch`` to step ``s``: post-window median minus the pre-step level
    (its linear trend extrapolated when the pre window is >= 10 min, else its median)."""
    tb, vb = tl.samples(ch, *s.pre)
    ta, va = tl.samples(ch, *s.post)
    if len(tb) < 3 or len(ta) < 3:
        return float("nan")
    if s.pre[1] - s.pre[0] >= 600.0 and len(tb) >= 8:
        slope, icpt = np.polyfit(tb - s.t, vb, 1)
        return float(np.median(va - (slope * (ta - s.t) + icpt)))
    return float(np.median(va) - np.median(vb))


def fit(data_root: Path, cooldowns: list[str] | None = None, method: str = "uca") -> dict:
    """Fit the thermal response; ``method`` "uca" (logbook UCA steps) or "rf"
    (the earlier RF-flip method, for comparison only)."""
    if method not in ("uca", "rf"):
        raise ValueError(f"method {method!r} not in ('uca', 'rf')")
    cds = [records.resolve_cooldown(c, data_root) for c in cooldowns] if cooldowns else \
        [p for p in records.list_cooldowns(data_root)
         if p.name[:4] >= "2021" and "Subset" not in p.name and p.name not in DUPLICATES]
    steps: dict[str, list[float]] = {c: [] for c in CHANNELS}
    taus: list[float] = []
    used = []
    for cd in cds:
        tl = Timeline(records.read_cooldown(cd, COLUMNS))
        for s in (uca_steps(tl) if method == "uca" else rf_steps(tl)):
            used.append(f"{cd.name} {records.format_epoch(s.t)} ({s.t - s.t_log:+.0f} s) "
                        f"{s.label} drive {s.dd:+.2f}")
            for ch in CHANNELS:
                v = step(tl, ch, s.t) if method == "rf" else step_size(tl, ch, s)
                if math.isfinite(v):
                    steps[ch].append(v / s.dd)
            if s.quiet_after_s >= STABLE_S:
                tau = rise_time(tl, "T1", s.t)
                if math.isfinite(tau) and (30.0 if method == "rf" else 0.0) < tau < STABLE_S:
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
            "method": METHODS[method],
            "cooldowns": [c.name for c in cds], "steps_used": len(used),
            "steps": used[:200],
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
                              "old stick was higher. Consistent with the logbook UCA timeline "
                              "(RF on throughout; the toggles match the T1 steps within a minute; "
                              "each a 0.98 full-drive step at 139.27 GHz). The record's Roots "
                              "Flow L differential is ~0.85 slm for the first toggle but only "
                              "0.2-0.5 slm for the later 4-6 min ones (to be reviewed).",
        "W_per_slm": "16.8 slpm per W at 1 K (C. Keith, PSTP 2024; Spin-1-Rates-Fit "
                     "holeburn/dec15_thermal.py) = 59.5 mW/slm; first-principles latent heat "
                     "62.5 mW/slm",
        "flow_tau_s": "Roots-flow impulse response 10-15 s (Spin-1-Rates-Fit afp_holeburn_technote, "
                      "Dec-15-2025 isolated 10-s burns)",
    },
}

#: cup response used when no cup thermometer is fitted (the RF-flip fit's T3 value)
CUP_FALLBACK = {"gain_K_per_W": 10.2, "tau_s": 730.0}
#: the record samples every 10-20 s: a faster cup response is not resolved
CUP_TAU_FLOOR_S = 20.0

ASSUMED = {
    "bath": {
        "gain_K_per_W": 2.3, "tau_s": 730.0,
        "basis": "ASSUMED: the bath (vapor-pressure thermometer, cell pressure) responds much "
                 "less than the cup. 2.3 K/W came from the earlier RF-flip fit's median "
                 "vapor-pressure step (+0.144 K per full drive, inconsistent sign, n=13) over "
                 "62 mW; the logbook UCA-step fit finds only about +0.003 K per full drive "
                 "(interquartile range spans zero), i.e. ~0.05 K/W. Kept at 2.3 K/W so "
                 "coldboard test faults stay visible: probably an overestimate for microwaves.",
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
        "cell_target_Torr": 760.0, "shells_target_Torr": 1.0e-3,
        "basis": "ASSUMED quench-like warm-up for exercising interlocks and alarms; not a "
                 "model of a real quench.",
    },
}


def cup_params(fitted: dict) -> dict:
    """Cup (target-lattice) response per watt from the fitted cup thermometer:
    T1 (the lattice temperature the model reports), else T3."""
    ch = fitted["thermal"]["channels"]
    w = HEAT["mw_W_at_full_drive"]
    tau = max(float(fitted["thermal"]["tau_s"]), CUP_TAU_FLOOR_S)
    for name in ("T1", "T3"):
        g = ch.get(name, {})
        if g.get("status") == "fitted" and g["gain_K_per_drive"] > 0:
            return {
                "gain_K_per_W": round(g["gain_K_per_drive"] / w, 2), "tau_s": round(tau, 1),
                "basis": f"PARTLY FITTED: cup thermometers (T1/T2/T3) are much more sensitive to "
                         f"microwaves than the helium bath (lab knowledge; the fit agrees). Gain "
                         f"from the FITTED {name} step response ({g['gain_K_per_drive']:+.3f} K "
                         f"per full drive at logbook UCA steps, n={g['n']}, IQR "
                         f"{g['iqr'][0]:+.3f}..{g['iqr'][1]:+.3f}) divided by the measured "
                         f"{w * 1e3:.0f} mW. tau = median T1 rise time "
                         f"({fitted['thermal']['tau_s']:.0f} s, n={fitted['thermal']['tau_n']}) "
                         f"floored at the record's ~{CUP_TAU_FLOOR_S:.0f} s sampling: an upper "
                         f"bound, the response is not resolved.",
            }
    return {**CUP_FALLBACK,
            "basis": "ASSUMED: no cup thermometer fitted; the earlier RF-flip fit's T3 response "
                     "(+0.635 K per full drive) over 62 mW, tau 730 s."}


def build_params(fitted: dict) -> dict:
    return {**fitted, "dnp": DNP, "heat": HEAT, "assumed": {"cup": cup_params(fitted), **ASSUMED}}
