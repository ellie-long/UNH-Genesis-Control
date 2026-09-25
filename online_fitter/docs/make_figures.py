"""Generate the operator-guide screenshots into online_fitter/docs/figures/.

Uses the real test-data replay if present (test_data/**/data_record_*.csv), else a synthetic
deuteron line, so it runs on any clone. Headless (offscreen Qt) -- no display needed.

    python online_fitter/docs/make_figures.py
"""
import glob
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # render without a display
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)                                # so "import online_fitter" works as a script

import numpy as np
from pyqtgraph.Qt import QtWidgets

from online_fitter import engine as E, server as S, gui

FIGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "figures")
MATERIAL = "d-butanol"
T0 = 1_750_000_000.0            # fixed base time so the strip x-axis is reproducible


N_SWEEPS = 60                  # total sweeps to stream (fast; enough to fill the strip)


def load_sweeps():
    """Return (list of sweep dicts, source label).

    Synthetic by default (fast + reproducible + present on any clone): a real Dulya double-bond
    deuteron line spinning up (r climbs then plateaus), with realistic noise. Set OF_FIGS_REAL=1 to
    instead replay the gitignored test_data/**/data_record_*.csv (slow: it parses the whole ~145 MB
    file). The synthetic line is the same physics model the fitter uses, so the screenshots are
    representative of live data."""
    if os.environ.get("OF_FIGS_REAL") == "1":
        matches = glob.glob(os.path.join(_ROOT, "test_data", "**", "data_record_*.csv"), recursive=True)
        if matches:
            from online_fitter import replay_source
            print(f"loading real test data (slow): {os.path.basename(matches[0])} ...")
            sweeps = [s for s in replay_source.iter_sweeps(matches[0], statuses={"Polarization"})
                      if np.isfinite(np.asarray(s["baseline_sub"], float)).all()]
            if len(sweeps) >= 12:
                return sweeps[:N_SWEEPS], "test data (2026-06-17 cool-down)"
    from online_fitter.vendored import deuteron_model as dm
    eng = E.FitEngine(material=MATERIAL)
    cf, span, npts = 32.09, 0.40, 451
    f = cf + span * (np.arange(npts) / npts - 0.5)
    b = dict(eng._base); b["omegD"] = cf
    rng = np.random.default_rng(1)
    out = []
    for i in range(N_SWEEPS):
        r = 1.2 + 0.95 * (1.0 - np.exp(-i / 12.0))       # spin-up: r climbs and plateaus
        sig = dm.model_double(f, b["omegD"], b["omegQ1"], b["omegQ2"], b["A1"], b["eta1"],
                              b["eta2"], r, b["K"], b["scale"], b["phase"])
        sig = sig + 0.02 * np.max(np.abs(sig)) * rng.standard_normal(npts)
        out.append({"ts": None, "centfreq_mhz": cf, "span_mhz": span,
                    "raw": sig * 1.5 + 3.0, "baseline_sub": sig})
    print("using synthetic data")
    return out, "representative example data (Dulya lineshape)"


def push(win, state, eng, sw, i):
    f = np.asarray(sw["baseline_sub"], float)
    freqs = sw["centfreq_mhz"] + sw["span_mhz"] * (np.arange(f.size) / f.size - 0.5)
    res = eng.fit_line(freqs, sw["baseline_sub"], E.MODE_EQUILIBRIUM,
                       span=sw["span_mhz"], centfreq=sw["centfreq_mhz"])
    state.push_result({"type": "sweep_result", "ts": sw.get("ts"), "recv_time": T0 + i * 20.0,
                       "mode": E.MODE_EQUILIBRIUM, "freqs": freqs,
                       "raw": np.asarray(sw["raw"], float), "baseline_sub": f, "result": res})


def main():
    os.makedirs(FIGDIR, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    sweeps, source = load_sweeps()

    eng = E.FitEngine(material=MATERIAL)
    state = S.SharedState(eng, mode=E.MODE_EQUILIBRIUM)
    win = gui.NMRWindow(state)
    win.material_combo.setCurrentText(MATERIAL)
    win.setFixedSize(1500, 860)
    win.show()

    # stream the spin-up so the equilibrium buffer fills at the high-P plateau, then calibrate on it
    warm = min(45, len(sweeps))
    for i in range(warm):
        push(win, state, eng, sweeps[i], i)
        win._drain()
    eng.update_constants()                          # average the buffered plateau sweeps (avg_signal=None)

    # stream the rest so the P/Q strip shows the full spin-up trend
    for i in range(warm, len(sweeps)):
        push(win, state, eng, sweeps[i], i)
        win._drain()
    app.processEvents()

    # figure 1: overview (left = Raw NMR)
    win._left_view = 0
    win.btn_left_view.setText("⟲ Raw NMR")
    win._refresh_left_view()
    app.processEvents()
    win.grab().save(os.path.join(FIGDIR, "fig_overview.png"))

    # figure 2: left = Averaged + Fit for Constants (the fit-quality check)
    win._left_view = 2
    win.btn_left_view.setText("⟲ Averaged + Fit")
    win._refresh_left_view()
    app.processEvents()
    win.grab().save(os.path.join(FIGDIR, "fig_avgfit.png"))

    # figure 3: readout panel close-up (constants, CC, CC_DAQ, P/Q)
    win._side_panel.grab().save(os.path.join(FIGDIR, "fig_readout.png"))

    print(f"wrote figures to {FIGDIR} (source: {source})")
    for n in ("fig_overview.png", "fig_avgfit.png", "fig_readout.png"):
        p = os.path.join(FIGDIR, n)
        print(f"  {n}: {'ok' if os.path.exists(p) else 'MISSING'}")


if __name__ == "__main__":
    main()
