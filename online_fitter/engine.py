"""Online fit orchestration.

A slim, live analogue of ``main_fit.analyze_line`` + ``main_fit.calibrate`` without
the cool-down-CSV / golden-window machinery. It holds the current material, the
current lineshape constants + area calibration constant ``CC``, and prebuilt
``FastModel`` engines, and routes each incoming baseline-subtracted sweep to the
appropriate vendored fit by the operator-chosen mode.

Physics/units:
  * frequencies in MHz; the model omegD (Larmor) ~ 32-33 MHz for deuterons.
  * P = vector polarization, Q = tensor polarization, both reported in PERCENT.
  * P_area / Q_eq are the DATA area-method values (cubic-wing-flattened absorptive
    area x CC, then Q_eq = 2 - sqrt(4 - 3P^2)); P_fit / Q_fit come from the fitted
    asymmetry r (ratio method), or from (rN, rP) for frequency hopping.

All heavy physics lives in the vendored McClellan modules; this file is pure
orchestration + state. It is CPU curve-fit work on ~few-hundred-point lines
(real-time, per line) -- deliberately NOT GPU.

Reference: Optimizied-McClellan-Fits/fitter/main_fit.py (analyze_line, _fit_window,
epoch_engines), commit 32e75b5.
"""
import threading
from collections import deque

import numpy as np

# numpy 2.0 renamed np.trapz -> np.trapezoid and removed the old name. Alias it back so this repo
# AND the vendored modules keep working on numpy>=2 (required on Python 3.13, which has no numpy 1.x
# wheels). Harmless on numpy 1.x, where np.trapz already exists.
if not hasattr(np, "trapz") and hasattr(np, "trapezoid"):
    np.trapz = np.trapezoid

from . import vendored  # noqa: F401  -- side effect: puts the vendored dir on sys.path
import deuteron_model as dm
import fit_deuterons as fd
import holeburn


def _r_from_P(P):
    """The asymmetry r with vector polarization P (inverse of ratio_P), 0 <= P < 1."""
    return (P + np.sqrt(4.0 - 3.0 * P * P)) / (2.0 * (1.0 - P))


# --- Local overrides for the FULL fit (Update Constants), applied WITHOUT editing the vendored
# file so re-vendoring stays a clean copy. Only the full open fit is affected; the per-line fast
# fit uses its own _FREE_BOUNDS (r already in [0.3, 4.0]).
FULL_FIT_R_MAX = 4.0             # widen the full-fit r upper bound 3.0 -> 4.0 (P ceiling 61.5% -> 71.4%)
FULL_FIT_R0 = _r_from_P(0.20)    # full-fit r initial guess = r at P = 20%  (~1.356)

_orig_fitter_spec = fd.fitter_spec


def _fitter_spec_wide_r(model, with_cube, centfreq, phase_lim=2.5):
    """Wrap the vendored fitter_spec to raise the r upper bound to FULL_FIT_R_MAX. fit_signal looks
    up ``fitter_spec`` in the fit_deuterons module globals at call time, so replacing that attribute
    (below) transparently widens the bound for the full open fit."""
    func, names, p0, lo, hi = _orig_fitter_spec(model, with_cube, centfreq, phase_lim=phase_lim)
    if "r" in names:
        hi = list(hi)
        hi[names.index("r")] = FULL_FIT_R_MAX
    return func, names, p0, lo, hi


fd.fitter_spec = _fitter_spec_wide_r

# ---------------------------------------------------------------------------
# Small pure helpers copied VERBATIM from main_fit.py (commit 32e75b5) so this
# repo need not import main_fit (which pulls uncertainty_core/paths/triage). Keep
# in sync if main_fit changes them.
# ---------------------------------------------------------------------------
SPAN_NWING = {0.4: 70}                                   # wing bins/side per Freq Span (MHz); 0.4 = d-butanol
BASE_KEYS = {"double": ("omegD", "omegQ1", "omegQ2", "A1", "eta1", "eta2", "K", "scale", "phase"),
             "single": ("omegD", "omegQ1", "A1", "eta1", "scale", "phase")}


def nwing_for(span, W):
    """Wing bins/side for the area-method cubic, per Freq Span (MHz)."""
    if span in SPAN_NWING:
        return SPAN_NWING[span]
    if span and span > 0.3:
        return max(20, round((0.5 - 0.14 / span) * W))
    return max(20, round(0.15 * W))


def wing_area(signal, freqs, nwing=70):
    """Area of the absorptive signal after a cubic fit to the signal-free wings
    (outer ``nwing`` bins/side, first/last bin trimmed) is subtracted.
    Returns (area, flattened_signal, cube_params).

    (Copied from main_fit.py, with one local addition: nwing and the fit degree are
    clamped to the array length so a short/degenerate sweep degrades gracefully instead
    of indexing out of bounds. For a real ~450-point sweep this changes nothing.)"""
    s = np.asarray(signal, float); f = np.asarray(freqs, float); n = len(s)
    nwing = max(2, min(int(nwing), (n - 1) // 2))    # never index past the array
    idx = np.r_[1:nwing, n - nwing:n - 1]
    x = (f - f.mean()) / (0.5 * (f[-1] - f[0]) or 1.0)
    c = np.polyfit(x[idx], s[idx], min(3, len(idx) - 1))
    flat = s - np.polyval(c, x)
    return float(np.trapz(flat, f)), flat, c


def daq_area(signal, span, npts):
    """The LabVIEW DAQ's area: a plain rectangular sum of the (baseline-subtracted) y-values times
    the frequency-bin width fstep = span/npts. No wing subtraction, no trapezoid -- exactly what the
    DAQ does, so a CC calibrated against THIS area can be pasted straight into the DAQ."""
    n = int(npts)
    if not span or n <= 0:
        return np.nan
    fstep = float(span) / n
    return fstep * float(np.sum(np.asarray(signal, float)))


def split_comps(comps, W):
    """Map the model's per-(bond, branch) components to four arrays:
    (primary+, primary-, secondary+, secondary-). Primary = C (C-D) or X (single-bond N-D);
    secondary = O (O-D), zeros if the model has no second bond. epsi +1 = I+, -1 = I-."""
    z = np.zeros(W)
    prim = "C" if ("C", 1) in comps else "X"
    g = lambda key: np.asarray(comps.get(key, z), float)
    return g((prim, 1)), g((prim, -1)), g(("O", 1)), g(("O", -1))
# --------------------------- end copied helpers ---------------------------

# Operator fitting modes (set in the GUI).
MODE_EQUILIBRIUM = "equilibrium"
MODE_SSRF = "ssRF"
MODE_FREQHOP = "freq_hopping"
MODE_BASELINE = "baseline"
MODE_NO_NMR = "no_nmr"
ALL_MODES = (MODE_EQUILIBRIUM, MODE_SSRF, MODE_FREQHOP, MODE_BASELINE, MODE_NO_NMR)

# Friendly material name -> MATERIALS preset used as the seed / initial guess.
MATERIAL_ALIASES = {
    "d-butanol": "irradiated d-butanol (Dec 2025, 13th/15th)",
    "d-propanediol": "trityl-doped d-propanediol (Oct 2021)",
    "ND3": "deuterated ammonia (Dec 2024)",
}
MATERIAL_NAMES = tuple(MATERIAL_ALIASES.keys())

EQ_BUFFER_DEFAULT = 10          # rolling # of equilibrium sweeps averaged for Update Constants
HB_WING = 25                    # wing bins/side for the hole-burnt cubic (holeburn.run default)
R_GRID_MAX = 3.5                # r-grid ceiling in holeburn.differential_binning_fast (r_max default)
# Physical-P sanity guards. Deuteron vector polarization readily reaches ~50%+, so we do NOT flag
# on a P threshold near 50 (that would reject good data). Instead:
#   * the EQUILIBRIUM single-r fast fit is "railed" when r hits its bound (-> P=71%): caught by the
#     r-at-bound test, which never touches a genuine high-P line (its r sits well below the bound);
#   * ANY |P| > 100% is physically impossible (spin-1 ceiling) and is dropped (e.g. an off-window
#     area-method blow-up). ssRF/freq-hop P come from the area method, so only this ceiling applies.
R_FAST_MAX = 4.0        # upper bound on r in the fast fit -> ratio_P(4.0) = +71.4%
R_FAST_MIN = 1.0 / R_FAST_MAX   # 0.25 -> ratio_P(1/r) = -ratio_P(r), so this mirrors R_FAST_MAX: -71.4%
P_ABS_MAX = 100.0       # |P| cannot exceed 100% (fully polarized spin-1)
# Half-width (kHz) of the Larmor region used to read r_eq from the CURRENT hole-burnt line.
# The RF manipulation avoids crossing the Larmor frequency omegD (crossing causes AFP-type
# effects), so |f - omegD| < this stays at EQUILIBRIUM -- its per-bin r is the true r_eq for
# this line, which tracks drift better than importing r_eq from a separate equilibrium line.
# Set to 0 to fall back to the reference (Update Constants) r_eq.
LARMOR_KHZ_DEFAULT = 10.0


def _nan(W):
    return np.full(W, np.nan)


class FitEngine:
    """Live fitter state. One instance per running GUI; ``fit_line`` is called from
    the server thread, ``set_material``/``update_constants`` from the GUI thread --
    all state mutations are serialized by an internal lock (FastModel/curve_fit are
    not re-entrant, mirroring view.py's _FIT_LOCK)."""

    def __init__(self, material="d-butanol", eq_buffer=EQ_BUFFER_DEFAULT,
                 larmor_khz=LARMOR_KHZ_DEFAULT):
        self._lock = threading.RLock()
        self.larmor_khz = float(larmor_khz)       # Larmor-region half-width for line r_eq (0 -> use ref r_eq)
        self._eq_sigs = deque(maxlen=eq_buffer)   # recent equilibrium baseline-subtracted sweeps
        self.freqs = None
        self._span = None
        self._centfreq = None
        self._last_avg = None                     # last averaged spectrum used by update_constants
        self._redchi = None                       # reduced chi2 of the last Update Constants full fit
        self.const_source = "none"                # 'preset' after set_material, 'fit' after update_constants
        self.set_material(material)

    # -- configuration -------------------------------------------------------
    def set_material(self, name):
        """Select the material; seed the lineshape constants from its MATERIALS preset.
        The seeded constants let equilibrium fits run immediately; ``update_constants``
        later refines them from a live spin-up and establishes the area CC."""
        with self._lock:
            preset_name = MATERIAL_ALIASES.get(name, name)
            if preset_name not in dm.MATERIALS:
                raise ValueError(f"unknown material {name!r}; known: {list(MATERIAL_ALIASES)}")
            preset = dm.MATERIALS[preset_name]
            self.material = name
            self.preset_name = preset_name
            self.model = preset.get("model", "double")
            base = {k: float(preset[k]) for k in BASE_KEYS[self.model] if k != "phase"}
            base["phase"] = float(np.deg2rad(preset.get("phase_deg", 0.0)))
            base["r"] = 1.5
            self._base = base
            self._scale = base["scale"]
            self._phase = base["phase"]
            self._CC = None                        # unknown until update_constants (area P needs a golden CC)
            self._CC_DAQ = None                    # DAQ-style CC (rect-sum area, yields P%); paste into LabVIEW
            self._eq_params = None                 # full-fit params (incl cubic) once available
            self._hb = None                        # (ref, eq_flat, fm_hb) for ssRF; needs a full fit
            self._uc_view = None                   # last Update-Constants averaged data + fit (for the left-panel view)
            self._eq_sigs.clear()
            self.const_source = "preset"
            self._fm_eq = self._fm_nc = self._cerr = None
            if self.freqs is not None:
                self._rebuild_models()

    def set_larmor_khz(self, khz):
        """Set the Larmor-region half-width (kHz) used to read r_eq from each hole-burnt
        line. 0 disables it (fall back to the reference r_eq from Update Constants)."""
        with self._lock:
            self.larmor_khz = max(0.0, float(khz))

    def set_freq_axis(self, freqs, span=None, centfreq=None):
        """Set/replace the frequency axis (MHz) and rebuild the FastModels. Clears the
        equilibrium average buffer if the point count changes."""
        with self._lock:
            freqs = np.asarray(freqs, float)
            changed = (self.freqs is None or self.freqs.size != freqs.size
                       or not np.allclose(self.freqs, freqs))
            if not changed:
                if span is not None:
                    self._span = span
                if centfreq is not None:
                    self._centfreq = centfreq
                return
            if self.freqs is not None and self.freqs.size != freqs.size:
                self._eq_sigs.clear()              # different width -> old averages are meaningless
            self.freqs = freqs
            self._span = span if span is not None else float((freqs[-1] - freqs[0]) * freqs.size / (freqs.size - 1))
            self._centfreq = centfreq if centfreq is not None else float(freqs.mean())
            # Data-driven Larmor seed: the preset omegD is field-specific and wrong at another
            # field, so before Update Constants seed omegD from the window centre (matching
            # McClellan's preset_to_p0). Only while still on preset constants.
            if self.const_source == "preset":
                self._base["omegD"] = self._centfreq
            self._rebuild_models()

    def _rebuild_models(self):
        """(Re)build the equilibrium and no-cube FastModels from the current constants."""
        self._fm_eq = dm.FastModel(self.freqs, self._base, self.model, with_cube=True)
        self._fm_nc = dm.FastModel(self.freqs, self._base, self.model, with_cube=False)
        self._cerr = {k: 0.0 for k in list(self._base) + ["omegD"]}
        # Rebuild the hole-burnt reference if we have a full fit's cubic params.
        p = self._eq_params
        if (p is not None and all(k in p for k in ("x3", "x2", "x1", "x0"))
                and self._last_avg is not None and len(self._last_avg) == self.freqs.size):
            self._build_hb_ref(self._last_avg, p)

    def _build_hb_ref(self, avg, params):
        ref = dict(model=self.model,
                   **{k: float(params[k]) for k in BASE_KEYS[self.model] + ("r", "x3", "x2", "x1", "x0")})
        eq_flat = np.asarray(avg, float) - dm.cubic(self.freqs, params["x3"], params["x2"],
                                                    params["x1"], params["x0"])
        fm_hb = dm.FastModel(self.freqs, ref, model=self.model, with_cube=False)
        self._hb = (ref, eq_flat, fm_hb)

    # -- Update Constants (full open fit) ------------------------------------
    def update_constants(self, avg_signal=None, cancel_event=None):
        """Full open fit over an averaged equilibrium spectrum -> refresh ALL lineshape
        constants, rebuild the FastModels, and set the area calibration constant
        ``CC = ratio_P(r) / wing_area(avg)``. This is the 'Update Constants' action.

        The heavy full fit runs WITHOUT holding the engine lock, so the server keeps
        fitting incoming sweeps (with the current constants) throughout -- only the final
        constant swap is locked. This is what keeps the LabVIEW acquisition / mock streamer
        from stalling (and timing out) during the ~seconds-long fit.

        ``cancel_event`` (a threading.Event) lets the GUI's Stop button abort: curve_fit
        itself cannot be interrupted, but if the event is set by the time the fit returns the
        constants are LEFT UNCHANGED (returns ``{"cancelled": True}`` instead of swapping).

        ``avg_signal`` overrides the internal rolling average of recent equilibrium
        sweeps. Returns a summary dict for the GUI. Mirrors main_fit._fit_window.
        """
        # 1. snapshot inputs under the lock (fast)
        with self._lock:
            if self.freqs is None:
                raise RuntimeError("no frequency axis yet -- receive at least one sweep first")
            if avg_signal is None:
                if not self._eq_sigs:
                    raise RuntimeError("no equilibrium sweeps buffered yet -- run in Equilibrium mode first")
                avg = np.mean(np.asarray(self._eq_sigs, float), axis=0)
            else:
                avg = np.asarray(avg_signal, float)
            freqs = self.freqs
            model = self.model
            span = self._span
            centfreq = self._centfreq
            preset_name = self.preset_name
            n_avg = len(self._eq_sigs)

        # 2. heavy work WITHOUT the lock -- server keeps serving with the old constants.
        # Seed the full open fit the McClellan way (preset_to_p0): omegD from the window centre
        # (data-driven -- field-specific), r=1.5, and every other constant from the material's
        # historical preset. curve_fit then floats them all from that starting point.
        _func, names, _p0, _lo, _hi = fd.fitter_spec(model, True, centfreq, phase_lim=fd.PHASE_LIM)
        p0_override = fd.preset_to_p0(names, dm.MATERIALS[preset_name], centfreq)
        p0_override["r"] = FULL_FIT_R0          # seed r at P=20% (not the generic 1.5), per request
        res = fd.fit_signal(freqs, avg, model, True, centfreq=centfreq,
                            p0_override=p0_override, phase_lim=fd.PHASE_LIM)
        p = res["params"]
        area, _flat, _c = wing_area(avg, freqs, nwing_for(span, len(freqs)))
        CC = (res["P"] / area) if area else np.nan
        # DAQ-style calibration: rectangular sum x fstep, scaled so CC_DAQ * (fstep*sum(y)) = P in %.
        darea = daq_area(avg, span, len(freqs))
        CC_DAQ = (100.0 * res["P"] / darea) if darea else np.nan
        base = {k: float(p[k]) for k in BASE_KEYS[model]}
        base["r"] = 1.5
        fm_eq = dm.FastModel(freqs, base, model, with_cube=True)
        fm_nc = dm.FastModel(freqs, base, model, with_cube=False)
        cerr = {k: 0.0 for k in list(base) + ["omegD"]}
        ref = dict(model=model, **{k: float(p[k]) for k in BASE_KEYS[model] + ("r", "x3", "x2", "x1", "x0")})
        eq_flat = np.asarray(avg, float) - dm.cubic(freqs, p["x3"], p["x2"], p["x1"], p["x0"])
        fm_hb = dm.FastModel(freqs, ref, model=model, with_cube=False)

        # Aborted while the (uninterruptible) fit was running -> leave the constants untouched.
        if cancel_event is not None and cancel_event.is_set():
            return {"cancelled": True}

        # 3. atomic swap under the lock (fast) -- refuse if the axis changed under us
        with self._lock:
            if self.freqs is None or self.freqs.size != freqs.size:
                raise RuntimeError("frequency axis changed during Update Constants -- retry")
            self._base = base
            self._scale = base["scale"]
            self._phase = base["phase"]
            self._CC = CC
            self._CC_DAQ = CC_DAQ
            # snapshot the averaged fit for the left-panel "Averaged + Fit" view -- cube-removed frame
            # (data - cubic, model without cubic, and the per-nucleus components) so it mirrors the
            # Processed NMR panel.
            Pp, Pm, Sp, Sm = split_comps(fm_eq.components(p["r"], p["scale"], p["phase"]), len(freqs))
            self._uc_view = {"freqs": np.asarray(freqs, float).copy(),
                             "data": np.asarray(res["signal_flat"], float),   # averaged data, cube-removed
                             "fit": np.asarray(res["fit"], float),            # model fit (no cube)
                             "cPp": Pp, "cPm": Pm, "cSp": Sp, "cSm": Sm,
                             "P": 100.0 * res["P"], "r": p["r"],
                             "red_chisq": res.get("red_chisq"), "n_avg": n_avg}
            self._eq_params = dict(p)
            self._last_avg = avg
            self._fm_eq = fm_eq
            self._fm_nc = fm_nc
            self._cerr = cerr
            self._hb = (ref, eq_flat, fm_hb)
            self._redchi = res.get("red_chisq")
            self.const_source = "fit"
        return dict(P=100 * res["P"], Q=100 * dm.ratio_Q(p["r"]), r=p["r"],
                    CC=CC, red_chisq=res.get("red_chisq"), n_avg=n_avg,
                    params={k: float(p[k]) for k in p})

    # -- per-line fit --------------------------------------------------------
    def fit_line(self, freqs, baseline_sub, mode, span=None, centfreq=None):
        """Route one baseline-subtracted sweep to its fit by ``mode`` and return a
        result dict (same shape as view.py:do_fit, plus 'freqs'/'fit'). Values in %."""
        with self._lock:
            self.set_freq_axis(freqs, span=span, centfreq=centfreq)
            sig = np.asarray(baseline_sub, float)
            freqs = self.freqs
            W = len(freqs)
            nwing = nwing_for(self._span, W)

            base_out = dict(fit_type=mode, mode=mode, freqs=freqs,
                            P_area=np.nan, Q_eq=np.nan, P_fit=np.nan, Q_fit=np.nan, r=np.nan,
                            cPp=_nan(W), cPm=_nan(W), cSp=_nan(W), cSm=_nan(W))

            # Display-only modes: no polarization, just show the (flattened) line.
            if mode == MODE_NO_NMR:
                base_out.update(fit_type=MODE_NO_NMR, flat=sig, fit=_nan(W))
                return base_out
            if mode == MODE_BASELINE:
                # Show the baseline-subtracted line EXACTLY as LabVIEW sent it -- no cubic-wing
                # subtraction -- so the operator can verify the raw vs baseline_sub wiring directly.
                base_out.update(fit_type=MODE_BASELINE, flat=sig, fit=_nan(W))
                return base_out

            # Area method (needs a calibration CC from Update Constants).
            area, wflat, _cube = wing_area(sig, freqs, nwing)
            if self._CC is not None and np.isfinite(self._CC):
                P_area_frac = self._CC * area
                base_out["P_area"] = 100 * P_area_frac
                base_out["Q_eq"] = 100 * dm.Q_equilibrium(P_area_frac)
            base_out["flat"] = wflat                # replaced below by the fit-consistent flat where available

            if mode == MODE_EQUILIBRIUM:
                self._eq_sigs.append(sig.copy())    # feed the rolling average for Update Constants
                const = {**self._base, "scale": self._scale, "r": 1.5, "phase": self._phase}
                # r bounds made symmetric in P: [1/R_FAST_MAX, R_FAST_MAX] -> P in [-71.4%, +71.4%]
                # (the vendored default lower bound 0.3 only reached -65.5%).
                res = fd.fit_signal_fast(self._fm_eq, sig, const, self._cerr, free=("r", "phase"),
                                         bounds_override={"r": (R_FAST_MIN, R_FAST_MAX)})
                p = res["params"]
                Pp, Pm, Sp, Sm = split_comps(self._fm_eq.components(p["r"], p["scale"], p["phase"]), W)
                base_out.update(fit_type="equilibrium", P_fit=100 * res["P"], Q_fit=100 * dm.ratio_Q(p["r"]),
                                r=p["r"], phase_deg=float(np.degrees(p["phase"])),
                                red_chisq=res.get("red_chisq"),
                                flat=res["signal_flat"], fit=res["fit"],
                                cPp=Pp, cPm=Pm, cSp=Sp, cSm=Sm)
                return self._pclamp(base_out)

            if mode == MODE_SSRF:
                if self._hb is None:
                    base_out.update(error="ssRF needs an equilibrium reference -- run Update Constants first",
                                    flat=wflat, fit=_nan(W))
                    return base_out
                ref, eq_flat, fm_hb = self._hb
                hb_flat = holeburn.wing_subtract(freqs, sig, HB_WING)
                # Pass 1: per-bin r with the reference r_eq. The interior rZ is set by matching the
                # data to the lineshape and is independent of r_eq; only the endpoints and the bump
                # threshold use it -- so this pass just gives us rZ to read the Larmor region from.
                rZ0, _s0, _b0 = holeburn.differential_binning_fast(freqs, hb_flat, ref, fm=fm_hb)
                # Read r_eq from THIS line's unperturbed Larmor region (|f - omegD| <= half-width);
                # the manipulation never crosses omegD, so that region stays at equilibrium.
                half = self.larmor_khz / 1000.0                 # kHz -> MHz
                mask = np.abs(freqs - ref["omegD"]) <= half
                r_eq_line = float(np.median(rZ0[mask])) if (half > 0 and mask.sum() >= 1) else float(ref["r"])
                # Pass 2: recompute with the line's own r_eq driving bump detection + the cc calibration.
                ref2 = dict(ref, r=r_eq_line)
                rZ, r_stc, is_bump = holeburn.differential_binning_fast(freqs, hb_flat, ref2, fm=fm_hb)
                area = holeburn.area_polarizations(freqs, eq_flat, ref2, rZ, r_stc, is_bump)
                Pp, Pm, Sp, Sm = split_comps(area["components"], W)
                base_out.update(fit_type="hole-burnt", P_fit=100 * area["P"], Q_fit=100 * area["Q"],
                                r=r_eq_line, r_eq_line=r_eq_line, r_eq_ref=float(ref["r"]),
                                phase_deg=float(np.degrees(ref["phase"])),
                                n_larmor=int(mask.sum()), flat=hb_flat, fit=area["hb_fit"],
                                cPp=Pp, cPm=Pm, cSp=Sp, cSm=Sm)
                # STALE-REFERENCE GUARD: the per-bin r inverts on a GRID capped near r~3.5. If the
                # reference's SCALE is weaker than this line (wrong epoch/tune, or Update Constants on
                # an early low-P sweep), the fit saturates and can't reach the data peak. (r_eq from the
                # Larmor region fixes r_eq drift, but not a wrong scale.) Flag it.
                peak_data = float(np.nanmax(np.abs(hb_flat))) if np.size(hb_flat) else 0.0
                peak_fit = float(np.nanmax(np.abs(area["hb_fit"]))) if np.size(area["hb_fit"]) else 0.0
                if r_eq_line >= 0.98 * R_GRID_MAX:
                    base_out["warning"] = (
                        f"Larmor r_eq railed at the grid ceiling (r={R_GRID_MAX:.1f}) -- the equilibrium "
                        f"reference SCALE is too weak/stale, so P is not trustworthy. Re-run Update "
                        f"Constants on the equilibrium this manipulation started from (same tune/mode).")
                elif peak_data > 0 and peak_fit < 0.8 * peak_data:
                    base_out["warning"] = (
                        f"hole-burnt fit reaches only {100*peak_fit/peak_data:.0f}% of the data peak -- "
                        f"the equilibrium reference SCALE looks too weak/stale. Re-run Update Constants on "
                        f"the equilibrium this manipulation started from (same tune/mode).")
                return self._pclamp(base_out)

            if mode == MODE_FREQHOP:
                cc = self._CC if (self._CC is not None and np.isfinite(self._CC)) else np.nan
                res = fd.fit_freqhop(self._fm_nc, sig, {"scale": self._scale}, self._phase, cc)
                Pp, Pm, Sp, Sm = split_comps(res["components"], W)
                base_out.update(fit_type="freq-hopping", P_fit=100 * res["P"], Q_fit=100 * res["Q"],
                                r=res["rP"], rN=res["rN"], rP=res["rP"],
                                phase_deg=float(np.degrees(self._phase)),
                                flat=res["flat"], fit=res["fit"], cPp=Pp, cPm=Pm, cSp=Sp, cSm=Sm)
                return self._pclamp(base_out)

            raise ValueError(f"unknown mode {mode!r}")

    def _pclamp(self, out):
        """Flag/NaN physically-impossible P/Q so junk lines don't spike the strip or the readout,
        and attach a warning. Preserves any warning already set (e.g. the ssRF stale-reference note).

        Two INDEPENDENT checks -- neither flags a genuine high polarization (~50%+ is achievable):
          * equilibrium single-r fit RAILED (r pinned at its fast-fit bound) -> P meaningless;
          * ANY |P| > 100% (the spin-1 ceiling) -> impossible (e.g. an area-method blow-up)."""
        msgs = []
        if out.get("fit_type") == "equilibrium":
            r = out.get("r")
            # Railed at EITHER bound (symmetric): r >= 0.98*R_FAST_MAX (+P rail) or r <= its reciprocal
            # 1/(0.98*R_FAST_MAX) (-P rail). ratio_P(1/r) = -ratio_P(r), so both map to |P| ~ 71%.
            if r is not None and np.isfinite(r) and (r >= 0.98 * R_FAST_MAX or r <= 1.0 / (0.98 * R_FAST_MAX)):
                pf = out.get("P_fit")
                ptxt = "" if pf is None or not np.isfinite(pf) else f", P_fit={pf:.0f}%"
                msgs.append(f"single-r fit railed (r={r:.3f} at a bound{ptxt}) -- the fixed scale looks "
                            f"wrong; re-run Update Constants on a clean equilibrium plateau.")
                out["P_fit"] = np.nan
                out["Q_fit"] = np.nan
        for pkey, qkey in (("P_fit", "Q_fit"), ("P_area", "Q_eq")):
            v = out.get(pkey)
            if v is not None and np.isfinite(v) and abs(v) > P_ABS_MAX:
                msgs.append(f"{pkey}={v:.0f}% exceeds the 100% physical ceiling -- off-window or a bad "
                            f"calibration; value dropped.")
                out[pkey] = np.nan
                out[qkey] = np.nan
        if msgs and not out.get("warning"):
            out["warning"] = " ".join(msgs)
        return out

    # -- introspection -------------------------------------------------------
    def status(self):
        with self._lock:
            return dict(material=self.material, model=self.model, const_source=self.const_source,
                        CC=self._CC, has_hb_ref=self._hb is not None,
                        npts=(None if self.freqs is None else int(self.freqs.size)),
                        n_eq_buffered=len(self._eq_sigs))

    def update_view(self):
        """Thread-safe snapshot of the last Update Constants (or imported) averaged data + total fit,
        for the left-panel Avg / Avg+Fit view. Returns None until a fit/import has happened. Keys:
        freqs, avg (baseline-sub averaged data), fit (total fit incl cubic), flat, P, r, red_chisq."""
        with self._lock:
            v = self._uc_view
            if v is None:
                return None
            return {k: (x.copy() if isinstance(x, np.ndarray) else x) for k, x in v.items()}

    def constants_snapshot(self):
        """Thread-safe snapshot of the current lineshape constants + CC + calibration source,
        for the readout panel (copy/paste). ``constants`` holds omegD/omegQ../A../eta../K/scale/
        phase/r as currently used by the fast fits."""
        with self._lock:
            base = dict(self._base) if self._base else {}
            if self._eq_params and "r" in self._eq_params:
                base["r"] = float(self._eq_params["r"])   # golden (reference) r, not the 1.5 fast-fit seed
            return dict(material=self.material, model=self.model, source=self.const_source,
                        CC=self._CC, CC_DAQ=self._CC_DAQ, redchi=self._redchi, constants=base)

    # -- export / import the calibration constants ---------------------------
    def export_constants(self):
        """Return a plain-dict payload of the current calibration -- everything import_constants()
        needs to reload the exact post-Update-Constants state (full fit params, CC, averaged
        spectrum, freq window). Returns None if nothing is calibrated yet (still on preset). The
        caller adds the post-analysis reference values (P/Q/r/...); those are NOT needed to reload."""
        with self._lock:
            if self._eq_params is None or self._CC is None or self.freqs is None:
                return None
            return {
                "material": self.material,
                "preset": self.preset_name,
                "model": self.model,
                "centfreq_mhz": float(self._centfreq),
                "span_mhz": float(self._span),
                "npts": int(self.freqs.size),
                "CC": float(self._CC),
                "CC_DAQ": None if self._CC_DAQ is None else float(self._CC_DAQ),
                "red_chisq": None if self._redchi is None else float(self._redchi),
                "params": {k: float(v) for k, v in self._eq_params.items()},
                "avg_signal": (None if self._last_avg is None
                               else [float(x) for x in np.asarray(self._last_avg, float)]),
            }

    def import_constants(self, payload):
        """Install calibration constants previously produced by export_constants() -- reproducing
        exactly the engine state a fresh Update Constants would (base constants, CC, FastModels, and
        the hole-burn reference), so live fitting continues as if that fit had just run. Any
        post-analysis reference values in the file are ignored. Returns a short summary dict.

        NOTE: CC carries the Δf of the exporting sweep (area = ∫ over frequency), so a file is only
        strictly valid for the same span/npts it was made at; a differing live window still fits, but
        area-method P is only exact when span/npts match."""
        model = payload["model"]
        if model not in BASE_KEYS:
            raise ValueError(f"unknown model {model!r} in constants file")
        params = {k: float(v) for k, v in payload["params"].items()}
        missing = [k for k in BASE_KEYS[model] + ("r", "x3", "x2", "x1", "x0") if k not in params]
        if missing:
            raise ValueError(f"constants file is missing required params: {missing}")
        centfreq = float(payload["centfreq_mhz"])
        span = float(payload["span_mhz"])
        npts = int(payload["npts"])
        freqs = centfreq + span * (np.arange(npts) / npts - 0.5)   # same axis protocol.freq_axis builds
        avg = (np.asarray(payload["avg_signal"], float)
               if payload.get("avg_signal") is not None else None)
        if avg is not None and avg.size != npts:
            raise ValueError(f"avg_signal length {avg.size} != npts {npts} in constants file")
        CC = float(payload["CC"])
        with self._lock:
            self.material = payload.get("material", self.material)
            self.preset_name = payload.get("preset", self.preset_name)
            self.model = model
            self.freqs = freqs
            self._span = span
            self._centfreq = centfreq
            base = {k: float(params[k]) for k in BASE_KEYS[model]}
            base["r"] = 1.5                            # fast-fit seed; golden r lives in _eq_params
            self._base = base
            self._scale = base["scale"]
            self._phase = base["phase"]
            self._CC = CC
            # DAQ CC: prefer the value in the file; else recompute it from the stored avg the DAQ way.
            if payload.get("CC_DAQ") is not None:
                self._CC_DAQ = float(payload["CC_DAQ"])
            elif avg is not None:
                darea = daq_area(avg, span, npts)
                self._CC_DAQ = (100.0 * dm.ratio_P(params["r"]) / darea) if darea else np.nan
            else:
                self._CC_DAQ = None
            self._eq_params = params
            self._last_avg = avg
            self._redchi = None if payload.get("red_chisq") is None else float(payload["red_chisq"])
            self._eq_sigs.clear()                      # buffered sweeps may be from another setup
            self.const_source = "imported"
            self._rebuild_models()                     # builds fm_eq/fm_nc/cerr + hole-burn ref
            # rebuild the left-panel averaged-data + fit view from the imported params (if avg present)
            if avg is not None:
                fm = dm.FastModel(freqs, {k: float(params[k]) for k in BASE_KEYS[model]},
                                  model, with_cube=False)
                fit_clean = fm.real(params["r"], params["scale"], params["phase"])
                cube = dm.cubic(freqs, params["x3"], params["x2"], params["x1"], params["x0"])
                Pp, Pm, Sp, Sm = split_comps(
                    fm.components(params["r"], params["scale"], params["phase"]), len(freqs))
                self._uc_view = {"freqs": np.asarray(freqs, float).copy(),
                                 "data": avg - cube, "fit": np.asarray(fit_clean, float),
                                 "cPp": Pp, "cPm": Pm, "cSp": Sp, "cSm": Sm,
                                 "P": 100.0 * dm.ratio_P(params["r"]), "r": float(params["r"]),
                                 "red_chisq": self._redchi, "n_avg": None}
            else:
                self._uc_view = None
            return dict(material=self.material, model=model, CC=CC, r=params["r"],
                        red_chisq=self._redchi, npts=npts, has_hb_ref=self._hb is not None)
