"""Round trip: emulator -> real driver -> IOC PV -> archiver CSV.

* **device channels** -- every probe sample of an emulator-fed reading PV
  must equal a value the emulator served shortly before, within that
  instrument's wire precision.  This proves the unchanged drivers decoded
  every reading correctly (calibration, units, status, NaN handling).
* **archive** -- every archived row must be a value the PV actually took
  (probe) at about that wall time.  This proves the archiver records what the
  IOCs publish, in the run directory.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .. import conversions
from ..profile import GeneratedStack
from ..pvlist import device_channel_pvs
from . import Check, loaders

#: kind -> (relative tolerance, factor applied to the served value)
TOLERANCE = {
    "ls224": (3e-5, 1.0),                          # 7-digit ohms through the inverse curve
    "mks902b": (6e-4, 1.0),                        # '%.3E' on the wire
    "tpg262": (6e-5, conversions.TORR_TO_MBAR),    # '%.4E' mbar on the wire
    "ionivac": (conversions.IONIVAC_REL_QUANTUM * 1.05, 1.0),
    "ls455": (1e-5, 1.0),                          # '%+.5E' on the wire
}

#: how far back (wall s) a probe sample may look for the served value
WINDOW_S = 8.0
MIN_MATCH_FRACTION = 0.99


def _settings(spec) -> dict:
    with open(Path(spec.folder) / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)[spec.ioc]


def device_channels(stack: GeneratedStack, run: Path, known_clamps: dict | None = None) -> list[Check]:
    served = loaders.served(run)
    probe = loaders.probe(run)
    probe = probe[probe["connected"] == 1]
    checks = []
    for spec in stack.iocs:
        kind = (spec.emulator or {}).get("kind")
        if kind not in TOLERANCE:
            continue
        rel, factor = TOLERANCE[kind]
        for pv, (inst, ch) in device_channel_pvs(spec, _settings(spec)).items():
            s = served[(served["instrument"] == inst) & (served["channel"] == ch)]
            p = probe[probe["pv"] == pv]
            checks.append(_match(pv, kind, s, p, rel, factor))
    return checks


def _match(pv, kind, served: pd.DataFrame, probe: pd.DataFrame, rel: float, factor: float) -> Check:
    if probe.empty:
        return Check(f"roundtrip {pv}", "FAIL", "no PV updates recorded")
    sw = served["wall"].to_numpy()
    sv = served["value"].to_numpy() * factor
    order = np.argsort(sw)
    sw, sv = sw[order], sv[order]
    matched, total, misses = 0, 0, []
    walls, nums = probe["wall"].to_numpy(), probe["num"].to_numpy()
    # the first update after IOC start can predate the sim clock window -> skip
    # it, unless it is the only one (a PV that never changes, e.g. a channel the
    # record has no value for, publishes NaN once and holds it)
    if len(walls) > 1:
        walls, nums = walls[1:], nums[1:]
    for wall, num in zip(walls, nums):
        total += 1
        lo = np.searchsorted(sw, wall - WINDOW_S)
        hi = np.searchsorted(sw, wall + (0.5 if len(probe) > 1 else WINDOW_S))
        cand = sv[lo:hi]
        if np.isnan(num):
            ok = bool(np.isnan(cand).any())
        else:
            fin = cand[np.isfinite(cand)]
            ok = bool(len(fin)) and bool(np.any(np.abs(fin - num) <= rel * np.maximum(np.abs(fin), 1e-30)))
        if ok:
            matched += 1
        elif len(misses) < 5:
            near = cand[np.isfinite(cand)]
            misses.append(f"wall {wall:.1f}: PV {num!r}, served "
                          f"{(near[-3:] if len(near) else cand[-3:]).tolist()}")
    frac = matched / total if total else 0.0
    verdict = "PASS" if total and frac >= MIN_MATCH_FRACTION else "FAIL"
    summary = f"{matched}/{total} PV updates match a served value (tol {rel:.1e} rel)"
    if total == 1 and matched and np.isnan(nums[-1]):
        summary = "record has no value: PV published invalid (NaN) as the instrument would"
    return Check(f"roundtrip {pv}", verdict, summary, misses,
                 {"kind": kind, "fraction": frac, "n": total})


def archive(run: Path, expected_pvs: list[str]) -> list[Check]:
    probe = loaders.probe(run)
    probe = probe[probe["connected"] == 1]
    files = loaders.archive_files(run)
    checks = []
    archived_pvs = 0
    for pv in expected_pvs:
        paths = files.get(loaders.safe_name(pv))
        p = probe[probe["pv"] == pv]
        if not paths:
            if not p.empty:
                checks.append(Check(f"archive {pv}", "FAIL", "PV was live but nothing was archived"))
            continue
        archived_pvs += 1
        a = loaders.archive_series(paths)
        pw, pv_str, pnum = p["wall"].to_numpy(), p["value"].to_numpy(), p["num"].to_numpy()
        bad = []
        for wall, val, num in zip(a["wall"], a["value"], a["num"]):
            lo, hi = np.searchsorted(pw, wall - 3.0), np.searchsorted(pw, wall + 3.0)
            # also the value the PV held at that moment: the last update at or
            # before it, or -- if the archiver subscribed before the probe did --
            # the first value the probe saw (a monitor's initial value is the
            # current one, unchanged since)
            current = max(np.searchsorted(pw, wall, side="right") - 1, 0)
            idx = sorted(set(range(lo, hi)) | ({current} if len(pw) else set()))
            ok = any(pv_str[i] == val for i in idx)
            if not ok and np.isfinite(num):
                c = pnum[idx]
                c = c[np.isfinite(c)]
                ok = bool(len(c)) and bool(np.any(np.abs(c - num) <= 1e-9 * np.maximum(np.abs(c), 1.0)))
            if not ok and len(bad) < 5:
                bad.append(f"{wall:.1f}: archived {val!r} not seen on the PV")
            elif not ok:
                bad.append("")
        n_bad = len(bad)
        checks.append(Check(f"archive {pv}", "PASS" if n_bad == 0 else "FAIL",
                            f"{len(a) - n_bad}/{len(a)} archived rows match the PV",
                            [b for b in bad if b]))
    checks.insert(0, Check("archive coverage", "INFO",
                           f"{archived_pvs} of {len(expected_pvs)} expected PVs archived"))
    return checks
