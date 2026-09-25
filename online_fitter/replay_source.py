"""Read a previous cool-down's raw ``data_record_*.csv`` and yield per-sweep payloads
that emulate what LabVIEW's VME Loop sends live: the raw digitized sweep AND the
Q-curve/pedestal baseline-subtracted line.

The baseline subtraction reproduces the offline pipeline exactly
(``fit_deuterons.baseline_for_row``, which picks the appropriate Baseline run per row),
which is the stand-in for what ``VMEQCorrect.vi`` does on the live DAQ. Used by both the
Python mock client and the replay-file maker.
"""
from . import vendored  # noqa: F401  -- bootstrap sys.path for the vendored modules
import cooldown_io as io
import fit_deuterons as fd

import numpy as np

# Amplifier-mode gain -> "Operating" amplitude, matching main_fit.VME_GAIN. Live, the
# amplifier mode is fixed and this constant folds into scale/CC, but for a faithful
# replay of a recorded cool-down we apply it so the emulated line matches the DAQ.
VME_GAIN = {"Operating": 1.0, "Phase": 17.05}


def iter_sweeps(path, statuses=None, start=0, stop=None, stride=1):
    """Yield dicts {ts, centfreq_mhz, span_mhz, raw, baseline_sub, status, online_p}
    for each sweep row of ``path``.

    Args:
        path: data_record_*.csv path.
        statuses: if given, only rows whose NMR Status is in this set (e.g. {"Polarization"}).
        start, stop, stride: row slicing.
    """
    cd = io.read_cooldown(path)
    bruns = [r for r in io.detect_runs(cd, {"Baseline"}) if r.status == "Baseline"]
    vme = cd.vme_mode
    n = len(cd.timestamps)
    stop = n if stop is None else min(stop, n)
    for i in range(start, stop, stride):
        status = cd.status[i]
        if statuses is not None and status not in statuses:
            continue
        if not np.isfinite(cd.span[i]) or not np.isfinite(cd.centfreq[i]):
            continue
        mode = (vme[i] if vme is not None else "") or "Operating"
        gain = VME_GAIN.get(mode, 1.0)
        raw = np.asarray(cd.sweeps[i], float)
        baseline_sub = (raw - fd.baseline_for_row(cd, bruns, i)) * gain
        op = None
        if cd.online_p is not None and np.isfinite(cd.online_p[i]):
            op = float(cd.online_p[i])
        yield {
            "ts": cd.timestamps[i].strip(),
            "centfreq_mhz": float(cd.centfreq[i]),
            "span_mhz": float(cd.span[i]),
            "raw": raw * gain,             # raw shown as-is on the DAQ panel (same gain as the sub line)
            "baseline_sub": baseline_sub,
            "status": status,
            "online_p": op,
        }
