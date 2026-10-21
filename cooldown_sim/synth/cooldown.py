"""Generate a synthetic cool-down record in memory (no data files needed).

A designed, seeded day in the 2026-06 layout, sampled every ``CADENCE_S``:

    0:00  cold and steady at 4.939 T, microwaves off, thermal equilibrium
    1:00  microwave RF ON at the DNP optimum (free-electron frequency), +10 dB,
          UCA at 5 V (no power)
    1:05  UCA lowered to 0 V (full power) -- the history rule for an RF-on
          period no logbook covers (``uca_history.UNLOGGED_DELAY_S``)
          -> P spins up with the target model's own DNP parameters
    4:00  three logged ssRF burns (blast_log) at the deuteron Larmor frequency
    5:00  microwaves OFF -> relaxation
    7:00  end

Every channel wanders like the real instruments (random walks with the
amplitudes the McClellan test-cooldown generator measured on 2026-06), so
nothing is frozen.  The recorded "online P" is the model's own analytic
trajectory, so a replay that repeats history reproduces it.

Faults for scenarios (``faults=``):
    "data_gap"   no rows for 10 minutes from 3:00
    "glitches"   isolated NaN rows on thermometers and pressures

Times start at ``T0`` (2000-01-01 00:00 lab time, like the McClellan
generator's TEST-01 day) unless another start is given.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..data.blast_log import Burn
from ..data.records import SlowTable
from ..data.timeline import Timeline
from ..model import physics as ph
from ..model.fit_params import drive
from ..model.target import load_params
from ..model.uca_history import UNLOGGED_DELAY_S
from ..timeparse import parse_lab_time

T0 = parse_lab_time("2000-01-01 00:00")
CADENCE_S = 20.0
B_T = 4.939
MW_ON_H, MW_OFF_H, END_H = 1.0, 5.0, 7.0
#: full power from here: RF ON + the unlogged-period UCA delay (the history rule)
MW_POWER_H = MW_ON_H + UNLOGGED_DELAY_S / 3600.0
BURN_H = (4.0, 4.1, 4.2)

#: channel -> (typical value, random-walk step per row, lower clip, upper clip)
#: (from the McClellan generator's WANDER table, real 2026-06 behaviour)
WANDER = {
    "F1": (150.0, 0.2, 80, 300), "F2": (120.0, 0.2, 60, 300), "F3": (8.2, 0.01, 7, 9.5),
    "F4": (6.7, 0.005, 6.5, 7), "F5": (6.8, 0.01, 6, 7.5), "F6": (5.8, 0.01, 4.5, 7),
    "F7": (6.0, 0.005, 5.5, 6.5), "F8": (5.2, 0.01, 4, 6.5), "F9": (4.4, 0.002, 4.3, 4.5),
    "F10": (4.6, 0.003, 4.4, 4.8), "F11": (1.77, 0.001, 1.7, 2.25),
    "M1": (54.0, 0.05, 4, 180), "M2": (4.3, 0.005, 4.0, 5.8), "M3": (4.43, 0.002, 4.3, 4.6),
    "M4": (4.12, 0.002, 4.0, 4.3), "M5": (4.04, 0.002, 3.9, 4.2),
    "T1": (1.20, 0.0005, 1.10, 1.35), "T3": (1.25, 0.0005, 1.15, 1.40),
    "vp_temp": (1.18, 0.0004, 1.10, 1.30), "roots_pressure": (0.35, 0.002, 0.2, 0.6),
    "ionivac": (4e-7, 2e-9, 2e-7, 3e-5), "cold_cathode": (2e-7, 1e-9, 1e-7, 1e-5),
    "fringe_g": (2.44, 0.0005, 2.40, 2.48), "fridge_he_level": (12.0, 0.01, 4, 19),
    "magnet_lhe_level": (26.0, 0.002, 20, 31),
}

#: Added with the optional cryo IOCs (left-Roots and separator flowmeters).
#: Drawn from a SEPARATE generator so every channel above keeps its values.
#: The history's microwave boil-off stays on roots_flow_r; the left line is
#: an independent ~3 slm background (2026-06 itself has no left-flow record),
#: and the separator ~51.5 slm as on 2026-06-17.
WANDER_EXTRA = {
    "roots_flow_l": (3.0, 0.02, 2.0, 4.0), "separator_flow": (51.5, 0.02, 45.0, 55.0),
}
#: offset of the extra channels' seed from the record's seed
EXTRA_SEED_OFFSET = 7919


@dataclass
class SyntheticCooldown:
    timeline: Timeline
    burns: list[Burn]
    events: list[tuple[float, str]] = field(default_factory=list)
    t0: float = T0
    t1: float = T0


def _walk(rng, n, typ, step, lo, hi):
    x = typ + np.cumsum(rng.normal(0.0, step, n))
    return np.clip(x, lo, hi)


def make(seed: int = 0, t0: float = T0, faults: tuple[str, ...] = (), params: dict | None = None
         ) -> SyntheticCooldown:
    rng = np.random.default_rng(seed)
    p = params or load_params()
    dnp = p["dnp"]
    t = t0 + np.arange(0.0, END_H * 3600.0, CADENCE_S)
    n = len(t)
    h = (t - t0) / 3600.0
    num: dict[str, np.ndarray] = {}
    for ch, (typ, step, lo, hi) in WANDER.items():
        num[ch] = _walk(rng, n, typ, step, lo, hi)

    mw_on = (h >= MW_ON_H) & (h < MW_OFF_H)
    f_opt = ph.electron_larmor_GHz(B_T) + dnp["optimum_offset_GHz"]
    num["mw_rf_status"] = mw_on.astype(float)
    num["mw_freq_ghz"] = np.where(mw_on, f_opt, np.nan)
    num["mw_power_db"] = np.where(mw_on, 10.0, 0.0)
    num["central_field"] = np.full(n, B_T)
    num["magnet_current"] = np.full(n, B_T / 0.1027405)
    num["ps_current"] = num["magnet_current"].copy()
    # microwave heat on the cup and bath (the model's own gains and drive at f_opt)
    # once the UCA is down, so history is self-consistent
    mw_power = (h >= MW_POWER_H) & (h < MW_OFF_H)
    q_mw = p["heat"]["mw_W_at_full_drive"] * drive(1.0, 10.0, f_opt) * mw_power
    num["T1"] = num["T1"] + p["assumed"]["cup"]["gain_K_per_W"] * q_mw
    num["T3"] = num["T3"] + p["assumed"]["cup"]["gain_K_per_W"] * q_mw
    num["vp_temp"] = num["vp_temp"] + p["assumed"]["bath"]["gain_K_per_W"] * q_mw
    # helium-4 vapor pressure of the bath (Torr), ~0.6 Torr at 1.2 K
    num["manometer_hi"] = 0.62 * np.exp(10.6 * (1 / 1.2 - 1 / num["vp_temp"]))
    num["manometer_lo"] = num["manometer_hi"] * (1 + rng.normal(0, 1e-3, n))
    num["roots_flow_r"] = 9.0 + q_mw / p["heat"]["W_per_slm"] + rng.normal(0, 0.1, n)
    rng_extra = np.random.default_rng(seed + EXTRA_SEED_OFFSET)
    for ch, (typ, step, lo, hi) in WANDER_EXTRA.items():
        num[ch] = _walk(rng_extra, n, typ, step, lo, hi)

    # recorded online P: the model's analytic trajectory (TE -> spin-up -> relax)
    P_te = ph.te_polarization(B_T, 1.2)[0]
    P = np.empty(n)
    P_on_end = None
    for i, hh in enumerate(h):
        if hh < MW_POWER_H:
            P[i] = P_te
        elif hh < MW_OFF_H:
            P[i] = dnp["P_max"] + (P_te - dnp["P_max"]) * math.exp(-(hh - MW_POWER_H) * 3600 / dnp["tau_up_s"])
        else:
            if P_on_end is None:
                P_on_end = P[i - 1]
            P[i] = P_te + (P_on_end - P_te) * math.exp(-(hh - MW_OFF_H) * 3600 / dnp["T1_P_s"])
    num["online_p"] = 100.0 * P + rng.normal(0, 0.05, n)
    num["nmr_centfreq"] = np.full(n, round(ph.deuteron_larmor_MHz(B_T), 3))
    num["nmr_span"] = np.full(n, 0.4)

    text = {
        "vp_range": np.array(["Hi"] * n, dtype=object),
        "vme_mode": np.array(["Operating"] * n, dtype=object),
        "nmr_status": np.array(["Polarization"] * n, dtype=object),
    }
    keep = np.ones(n, bool)
    events = [(t0 + MW_ON_H * 3600, "microwave RF ON (optimum), UCA 5 V"),
              (t0 + MW_POWER_H * 3600, "UCA 0 V: full power"), (t0 + MW_OFF_H * 3600, "microwaves OFF")]
    if "data_gap" in faults:
        gap = (h >= 3.0) & (h < 3.0 + 10 / 60)
        keep &= ~gap
        events.append((t0 + 3 * 3600, "DAQ gap: 10 min with no rows"))
    if "glitches" in faults:
        idx = rng.choice(n, size=max(3, n // 200), replace=False)
        for ch in ("F1", "F5", "M2", "roots_pressure", "manometer_hi"):
            num[ch][idx] = np.nan
        events.append((t0, f"{len(idx)} glitch rows (NaN) on F1/F5/M2/pressures"))
    table = SlowTable(t=t[keep], numeric={k: v[keep] for k, v in num.items()},
                      text={k: v[keep] for k, v in text.items()}, sources=[f"synthetic seed {seed}"])

    larmor = ph.deuteron_larmor_MHz(B_T)
    burns = [Burn(start=t0 + bh * 3600, end=t0 + bh * 3600 + 2, centre_mhz=round(larmor, 3),
                  fm_dev_khz=10.0, step_db=98.0, sweep_min_dbm=-120.0, sweep_max_dbm=-22.0,
                  dwell_ms=10.0, status="Success", source="synthetic") for bh in BURN_H]
    events += [(b.start, f"logged ssRF burn at {b.centre_mhz:.3f} MHz") for b in burns]
    return SyntheticCooldown(Timeline(table), burns, sorted(events), t0, float(t[-1]))
