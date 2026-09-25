"""Interactive deuteron NMR fitter.

Run it:   python fitter/fit_deuterons.py

Fits deuteron NMR signals and reports, per signal:
  timestamp, vector polarization P, tensor polarization Q, then every fit
  parameter (with curve_fit uncertainties), the area-method P/Q, and reduced chi2.

Handles three input situations:
  * a pre-flattened "flats" file (e.g. 2025_Dec_12_SpinUpFlats.csv) -> fit each row
  * a raw cooldown -> pick NMR lines + baselines to average (a subset), or the
    whole cooldown grouped in N-line blocks
  * out-of-equilibrium signals -> frequency-hopping or hole-burnt (area method)

Every run also writes a re-usable processed-signals cache (in the flats format),
so re-fitting later is fast and never needs to re-touch the raw file.

The heavy lifting lives in deuteron_model.py / holeburn.py / cooldown_io.py; this
file is the orchestration + I/O + prompts. The non-interactive functions
(fit_signal, fit_flats_file, build_subset_signal, ...) are importable for testing.
"""

import csv
import json
import os

import numpy as np
from scipy.optimize import curve_fit, least_squares

import deuteron_model as dm
import cooldown_io as io
import holeburn
import classify


# ---------------------------------------------------------------------------
# Fit configuration: model + parameter names + bounds
# ---------------------------------------------------------------------------
# Parameter name order for each (model, with_cube). phase is fit/stored in radians
# and reported in degrees.
_CUBE_NAMES = ["x3", "x2", "x1", "x0"]

# The lineshape constants fixed during a fast (r-only) fit, per model.
CONST_KEYS = {
    "double": ["omegD", "omegQ1", "omegQ2", "A1", "eta1", "eta2", "K", "scale", "phase"],
    "single": ["omegD", "omegQ1", "A1", "eta1", "scale", "phase"],
}


def fitter_spec(model, with_cube, centfreq, phase_lim=2.5):
    """Return (func, names, p0, lo, hi) for curve_fit.

    Bounds/initial guesses are ported from the original scripts
    (DeuteronAnalysisComponentsAllXi.py and ..._Flat.py), with omegD recentred on
    the data's central frequency.

    ``phase_lim`` (rad) bounds the phase to +/-phase_lim. The default +/-2.5 rad
    (+/-143 deg) is wide; pass a tighter value (e.g. 0.6 rad ~ 34 deg) when fitting a
    reference whose phase must be physically near zero -- the wide bound otherwise
    admits a sign-flipped degenerate solution (phase pinned at -143 deg, negative P)
    that fits the data comparably and corrupts the established constants."""
    big = 1e5
    if model == "double":
        names = ["omegD", "omegQ1", "omegQ2", "A1", "eta1", "eta2", "r", "K",
                 "scale", "phase"]
        #        omegD            omegQ1  omegQ2  A1      eta1    eta2   r    K     scale phase
        p0 = [centfreq, 0.021711, 0.0262, 0.025, 0.05, 0.18, 1.5, 0.06, 0.004, 0.0]
        lo = [centfreq - 0.1, 0.0212, 0.024, 0.0001, 0.0001, 0.001, 0.1, 0.03, 0, -phase_lim]
        hi = [centfreq + 0.1, 0.022, 0.030, 0.1, 0.12, 0.5, 3, 0.25, 0.02, phase_lim]
        base = dm.model_double
        cube = dm.model_double_cube
    elif model == "single":
        names = ["omegD", "omegQ1", "A1", "eta1", "r", "scale", "phase"]
        p0 = [centfreq, 0.0273, 0.038, 0.11, 1.5, 0.001, 0.0]
        lo = [centfreq - 0.1, 0.025, 0.0001, 0.0001, 0.1, 0, -phase_lim]
        hi = [centfreq + 0.1, 0.030, 0.1, 0.3, 3, 0.02, phase_lim]
        base = dm.model_single
        cube = dm.model_single_cube
    else:
        raise ValueError(f"unknown model {model!r}")

    if with_cube:
        names = names + _CUBE_NAMES
        p0 = p0 + [0.0, 0.0, 0.0, 0.0]
        lo = lo + [-big, -big, -big, -big]
        hi = hi + [big, big, big, big]
        func = cube
    else:
        func = base
    return func, names, p0, lo, hi


def preset_to_p0(spec_names, preset, centfreq):
    """Build a p0 from a MATERIALS preset, falling back to spec defaults.

    omegD is ALWAYS seeded from the window centre (``centfreq``), never from the
    preset: the Larmor frequency moves with the field (e.g. 33.18 MHz on Dec-13 vs
    31.70 MHz on Dec-15/16), so the preset's omegD is wrong at a different field and,
    being outside the tight omegD bounds, would clip to the wrong end and collapse
    the fit to P~0. The window centre is the correct seed (it is also the operator's
    convention; see ind_line_analysis + docs/nmr_operations.tex)."""
    vals = {"omegD": centfreq, "r": 1.5}
    vals.update({k: v for k, v in preset.items() if k in spec_names and k != "omegD"})
    if "phase_deg" in preset:
        vals["phase"] = np.radians(preset["phase_deg"])
    return vals


# ---------------------------------------------------------------------------
# Core fit of a single (already baseline-subtracted) signal
# ---------------------------------------------------------------------------
def fit_signal(freqs, signal, model, with_cube, centfreq=None, p0_override=None,
               maxfev=10000, phase_lim=2.5):
    """Fit one signal. Returns a result dict with params, errors, P/Q, area P/Q,
    reduced chi2, the model curve, and the cube-subtracted data (for plotting/cache).

    ``phase_lim`` (rad) bounds the fitted phase; tighten it (e.g. 0.6) for a
    reference fit to avoid the sign-flipped degenerate solution (see fitter_spec).
    """
    freqs = np.asarray(freqs, dtype=float)
    signal = np.asarray(signal, dtype=float)
    if centfreq is None:
        centfreq = freqs[len(freqs) // 2]
    func, names, p0, lo, hi = fitter_spec(model, with_cube, centfreq, phase_lim=phase_lim)
    if p0_override is not None:
        p0 = [float(np.clip(p0_override.get(n, d), l, h))
              for n, d, l, h in zip(names, p0, lo, hi)]

    # phase MULTI-START: a reference can be real (~0) OR imaginary (~+/-90 deg); a single phase=0
    # start can't climb to an imaginary basin, so seed from real and both imaginary quadrants and keep
    # the lowest-residual fit. Seeds are clipped to the phase bound (which excludes the +/-180 deg
    # sign-flip basin -- see _FREE_BOUNDS). This is per-epoch (cheap), unlike the per-line fast fit.
    iph = names.index("phase")
    seeds, seen = [], set()
    for s in (p0[iph], 0.0, np.pi / 2, -np.pi / 2):
        s = float(np.clip(s, lo[iph], hi[iph]))
        if round(s, 3) not in seen:
            seen.add(round(s, 3)); seeds.append(s)
    best = None
    for s in seeds:
        pp = list(p0); pp[iph] = s
        try:
            po, pc = curve_fit(func, freqs, signal, p0=pp, bounds=(lo, hi), maxfev=maxfev,
                               ftol=1e-4, xtol=1e-4)   # loosened: imaginary goldens otherwise grind
        except Exception:
            continue
        cost = float(np.sum((signal - func(freqs, *po)) ** 2))
        if best is None or cost < best[0]:
            best = (cost, po, pc)
    if best is None:                                       # every seed failed -> last-ditch single fit
        popt, pcov = curve_fit(func, freqs, signal, p0=p0, bounds=(lo, hi), maxfev=maxfev,
                               ftol=1e-4, xtol=1e-4)
    else:
        popt, pcov = best[1], best[2]
    perr = np.sqrt(np.abs(np.diag(pcov)))
    params = dict(zip(names, popt))
    errors = dict(zip(names, perr))

    # Standardise param keys so the area method and output are model-agnostic.
    apar = dict(params)
    if model == "single":
        apar.setdefault("omegQ1", params["omegQ1"])

    r = params["r"]
    sigma_r = errors["r"]
    P = dm.ratio_P(r)
    Q = dm.ratio_Q(r)
    P_err = abs(dm.ratioPderiv(r)) * sigma_r
    Q_err = abs(dm.ratioQderiv(r)) * sigma_r

    area_P, area_Q, _cc = dm.area_polarizations(freqs, apar, model=model)

    fit_full = func(freqs, *popt)
    if with_cube:
        cube = dm.cubic(freqs, params["x3"], params["x2"], params["x1"], params["x0"])
        signal_flat = signal - cube
        fit_clean = fit_full - cube
    else:
        signal_flat = signal
        fit_clean = fit_full
    resid = signal_flat - fit_clean
    data_std = np.mean(np.abs(resid)) or 1.0
    red_chisq = dm.chisq_red(signal_flat, fit_clean, data_std)

    return dict(model=model, with_cube=with_cube, names=names,
                params=params, errors=errors, cov=np.asarray(pcov, float),
                P=P, P_err=P_err, Q=Q, Q_err=Q_err,
                area_P=area_P, area_Q=area_Q,
                red_chisq=red_chisq, fit=fit_clean, signal_flat=signal_flat,
                resid=resid)


# ---------------------------------------------------------------------------
# Fast fit: constants fixed, float only r (+ optionally scale/phase + cubic)
# ---------------------------------------------------------------------------
# Default free set follows the Dissertation: over a spin-up only r changes, so
# fixing the constants AND scale/phase makes low-P fits well-conditioned (r and
# scale are otherwise degenerate at low P) and ~100x faster.
_FAST_FREE_DEFAULT = ("r",)

# OPEN PHASE -- the single source of truth, used CONSISTENTLY everywhere phase is bounded or gated
# (this module's fits, cascade classification + gates, reference3 scans, main_fit reference).
# "imaginary" lines are equilibrium lines at a large phase ANGLE (NOT Phase Tune, a voltage knob --
# see memory phase-angle-vs-phase-tune); one cool-down (d-propanediol, Diss. Fig 4.6) swaps
# real<->imaginary line-to-line, so phase floats. 0=real, +/-90deg=pure imaginary. Bound = +/-125deg:
# the largest phase in the dissertation is -109.1deg (off-tune, Fig 4.1) -- a small buffer past it,
# while staying SHORT OF +/-180deg, the sign-FLIP basin the lab never operates in, which would ALIAS
# a genuine negative-P line (r<1) with a flipped positive one (the P sign must live in r, not a flip).
PHASE_LIM_DEG = 125.0
PHASE_LIM = np.deg2rad(PHASE_LIM_DEG)

_FREE_BOUNDS = {  # (lo, hi, p0) for the fast free parameters
    "r": (0.3, 4.0, 1.5),
    "scale": (0.0, 0.02, 0.004),
    "phase": (-PHASE_LIM, PHASE_LIM, 0.0),
}


def _cube_basis(fm):
    """Cubic design matrix D (n x 4) and its pseudo-inverse, cached on the model.
    The optimal cubic for any residual g is D @ (Dpinv @ g) -- an instant linear
    least-squares solve, so the cubic can be profiled out of the fit.

    The frequency is CENTRED and SCALED to ~[-1, 1] before forming [u^3, u^2, u, 1].
    The raw [f^3, f^2, f, 1] basis over a ~0.4 MHz span at f~31.7 is catastrophically
    ill-conditioned (cond ~1e12), so its pseudo-inverse is imprecise -- the profiled
    cubic then misbehaves and a free phase wanders into a wrong minimum (curved wings,
    bogus phase; see docs/nmr_operations.tex "Fitting pitfalls"). u-space spans the
    same cubic, so signal_flat is unchanged for a well-fit line; the conditioning just
    buys the numerical precision that lets the fast (profiled) fit match the explicit
    simultaneous fit. NB: the reported cubic coefficients are then in normalised
    frequency u (the background curve, signal_flat, is unaffected)."""
    if not hasattr(fm, "_cube_D"):
        f = np.asarray(fm.freqs, dtype=float)
        s = 0.5 * (f[-1] - f[0]) or 1.0
        u = (f - f.mean()) / s
        fm._cube_D = np.vstack([u ** 3, u ** 2, u, np.ones_like(u)]).T
        fm._cube_pinv = np.linalg.pinv(fm._cube_D)
    return fm._cube_D, fm._cube_pinv


def fit_signal_fast(fast_model, signal, const, const_err=None, free=_FAST_FREE_DEFAULT,
                    p0_override=None, maxfev=2000, bounds_override=None):
    """Fit a signal with constants fixed (via a prebuilt FastModel).

    ``free`` lists which of r/scale/phase float. If the FastModel was built
    with_cube, the cubic background is *profiled out* analytically: because it is
    linear in its coefficients, the optimal cubic for any trial of the nonlinear
    parameters is an instant least-squares projection, so only r (and any free
    scale/phase) are searched. This is ~10x faster than fitting the cubic jointly
    and gives identical results. Returns the same result-dict shape as fit_signal.

    ``bounds_override`` maps a free-parameter name to a (lo, hi) tuple, replacing
    the default _FREE_BOUNDS for that parameter -- e.g. {"phase": (-0.6, 0.6)} to
    keep a reference pre-fit out of the sign-flipped ~ -143 deg phase basin.
    """
    signal = np.asarray(signal, dtype=float)
    fm = fast_model
    nonlin = list(free)                         # r, optionally scale/phase (never cube)
    fixed = {"r": const.get("r", 1.5), "scale": const["scale"],
             "phase": const.get("phase", 0.0)}
    bo = bounds_override or {}
    lo, hi, p0 = [], [], []
    for n in nonlin:
        l, h, d = _FREE_BOUNDS[n]
        if n in bo:
            l, h = bo[n]
        lo.append(l); hi.append(h)
        p0.append(d if p0_override is None else float(np.clip(p0_override.get(n, d), l, h)))

    def shape(vals):
        kw = dict(zip(nonlin, vals))
        return fm.real(kw.get("r", fixed["r"]), kw.get("scale", fixed["scale"]),
                       kw.get("phase", fixed["phase"]))

    if fm.with_cube:
        D, Dpinv = _cube_basis(fm)

        def resid(vals):                        # residual orthogonal to the cubic
            g = signal - shape(vals)
            return g - D @ (Dpinv @ g)
    else:
        def resid(vals):
            return signal - shape(vals)

    if "phase" in nonlin:                       # real<->imaginary BASIN pick. scale + cubic are
        iph = nonlin.index("phase")             # profiled out, so each phase seed is one cheap
        seeds = np.linspace(lo[iph], hi[iph], 13)   # residual eval. Choose the lowest-cost basin so a
        sv = list(p0)                           # line that swapped to imaginary isn't stranded near
        ir = nonlin.index("r") if "r" in nonlin else None
        if ir is not None:
            # Scan BOTH r-sign basins x phase, take the global lowest finite cost, so a real NEGATIVE line
            # (r<1, phase~0) is not mis-fit as positive-at-a-near-flip-phase. The positive branch is SEEDED
            # FROM THE WARM-START r (continuity -> identical to the old phase-only scan whenever the line is
            # positive, since the warm-start basin then wins); the opposite-sign default is the alternative.
            # Universal: +/- P switching is routine, and a steady-positive spin-up is unchanged.
            warm = p0[ir]; alt = 0.6 if warm >= 1.0 else 1.6
            best = None
            for rs in (warm, alt):
                sv[ir] = rs
                for ph in seeds:
                    sv[iph] = ph; rr = resid(sv); c = float(rr @ rr)
                    if np.isfinite(c) and (best is None or c < best[0]):
                        best = (c, rs, ph)
            if best is not None:
                p0[ir] = best[1]; p0[iph] = best[2]
        else:                                   # no r free: phase-only scan
            def _seedcost(ph):
                sv[iph] = ph; rr = resid(sv); return float(rr @ rr)
            p0[iph] = float(min(seeds, key=_seedcost))

    # ftol/xtol loosened from the 1e-8 default: on noisy/wiggly lines the fit otherwise grinds
    # thousands of iterations chasing precision far below the ~1e-2 noise floor (the 92-min/2845-line
    # 2024-09 runaway). 1e-4 stops when improvement is negligible -- same answer, far fewer evals.
    sol = least_squares(resid, p0, bounds=(lo, hi), max_nfev=maxfev, ftol=1e-4, xtol=1e-4)
    popt = sol.x
    fitted = dict(zip(nonlin, popt))
    # Parameter uncertainties from the Gauss-Newton covariance at the solution.
    try:
        dof = max(1, len(signal) - len(popt))
        cov = np.linalg.inv(sol.jac.T @ sol.jac) * (2.0 * sol.cost / dof)
        perr = np.sqrt(np.abs(np.diag(cov)))
    except np.linalg.LinAlgError:
        perr = np.zeros(len(popt))
    fitted_err = dict(zip(nonlin, perr))

    r = fitted.get("r", fixed["r"])
    scale = fitted.get("scale", fixed["scale"])
    phase = fitted.get("phase", fixed["phase"])
    if fm.with_cube:
        cube_coeffs = Dpinv @ (signal - shape(popt))    # [x3, x2, x1, x0]
        cube = fm._cube_D @ cube_coeffs
    else:
        cube_coeffs, cube = (0.0, 0.0, 0.0, 0.0), 0.0

    params = dict(const)
    errors = {k: (const_err or {}).get(k, 0.0) for k in const}
    for n in nonlin:
        params[n] = fitted[n]
        errors[n] = fitted_err[n]
    params.setdefault("r", fixed["r"])
    params.setdefault("scale", fixed["scale"])
    params.setdefault("phase", fixed["phase"])
    if fm.with_cube:
        for c, v in zip(_CUBE_NAMES, cube_coeffs):
            params[c], errors[c] = v, 0.0

    sigma_r = errors.get("r", 0.0)
    P, Q = dm.ratio_P(r), dm.ratio_Q(r)
    P_err = abs(dm.ratioPderiv(r)) * sigma_r
    Q_err = abs(dm.ratioQderiv(r)) * sigma_r
    area_P, area_Q = fm.area(r, scale, phase)

    fit_clean = shape(popt)
    signal_flat = signal - cube
    resid_v = signal_flat - fit_clean
    data_std = np.mean(np.abs(resid_v)) or 1.0

    names = fitter_spec(fm.model, fm.with_cube, fm.omegD)[1]
    return dict(model=fm.model, with_cube=fm.with_cube, names=names,
                params=params, errors=errors,
                P=P, P_err=P_err, Q=Q, Q_err=Q_err,
                area_P=area_P, area_Q=area_Q,
                red_chisq=dm.chisq_red(signal_flat, fit_clean, data_std),
                fit=fit_clean, signal_flat=signal_flat, resid=resid_v)


# ---------------------------------------------------------------------------
# Output: results CSV + reusable flats cache + provenance sidecar
# ---------------------------------------------------------------------------
def _results_header(names):
    # P/Q are the ratio method; P_area/Q_area are the self-consistent area method.
    # In equilibrium P_area == P (ratio) and Q_area ~ Q; large divergence (or a
    # large reduced chi^2) flags that a single-r (ratio) description is inadequate.
    cols = ["timestamp", "P(%)", "P_err(%)", "Q(%)", "Q_err(%)", "Q_eq(%)",
            "P_area(%)", "Q_area(%)", "reduced_chisq"]
    for n in names:
        label = "phase(deg)" if n == "phase" else n
        cols += [label, label + "_err"]
    return cols


def _results_row(timestamp, res):
    row = [timestamp,
           100 * res["P"], 100 * res["P_err"], 100 * res["Q"], 100 * res["Q_err"],
           100 * dm.Q_equilibrium(res["P"]),
           100 * res["area_P"], 100 * res["area_Q"], res["red_chisq"]]
    for n in res["names"]:
        v = res["params"][n]
        e = res["errors"][n]
        if n == "phase":
            v, e = np.degrees(v), np.degrees(e)
        row += [v, e]
    return row


def write_results(path, names, rows):
    """Write the fit-results CSV (one row per fitted signal)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(_results_header(names))
        w.writerows(rows)


def write_flats_cache(path, freqs, entries):
    """Write processed (baseline + cubic subtracted) signals in the flats format
    so they can be re-fit later without re-processing the raw file.

    ``entries`` is a list of (timestamp, P_percent, signal_array).
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        w.writerow(["Timestamp", "P", "Data"])
        w.writerow(list(np.asarray(freqs, dtype=float)))
        for timestamp, P, sig in entries:
            w.writerow([timestamp, P] + list(np.asarray(sig, dtype=float)))


def write_provenance(path, info):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2, default=str)


# ---------------------------------------------------------------------------
# Non-interactive conveniences (also used by tests/verification)
# ---------------------------------------------------------------------------
def establish_reference(freqs, signal, model, with_cube, centfreq=None, preset=None,
                        maxfev=100000, phase_lim=0.6):
    """Full free-parameter fit of one high-P signal to fix the lineshape constants.

    Returns (const, const_err, reference_result). The constants are then held
    fixed while only r (+ cubic) is fit on every other signal -- far faster and,
    crucially, well-conditioned at low polarization (Dissertation Sec. 4.6).

    ``maxfev`` can be lowered when this is used only to RANK candidate references
    (a rough P/phase is enough); garbage lines otherwise burn the full iteration
    budget without converging. ``phase_lim`` (rad) defaults to a TIGHT +/-0.6 rad
    (~34 deg): a reference's phase is physically near zero, and the wide default
    bound admits a sign-flipped degenerate fit (phase ~ -143 deg, negative P) that
    corrupts the constants.
    """
    p0 = None
    if preset:
        p0 = preset_to_p0(fitter_spec(model, with_cube,
                                      centfreq or freqs[len(freqs) // 2])[1], preset,
                          centfreq or freqs[len(freqs) // 2])
    ref = fit_signal(freqs, signal, model, with_cube, centfreq, p0_override=p0,
                     maxfev=maxfev, phase_lim=phase_lim)
    const = {k: ref["params"][k] for k in CONST_KEYS[model]}
    const_err = {k: ref["errors"][k] for k in CONST_KEYS[model]}
    return const, const_err, ref


def fit_flats_file(path, model="double", indices=None, reference_index=None):
    """Fit rows of a pre-flattened file with fast (r-only) fits.

    A reference row (highest recorded P, or the last row) is fit with all
    parameters free to fix the constants; every selected row then fits r only.
    Returns (freqs, selected_rows, results, reference_result).
    """
    freqs, rows = io.read_flats(path)
    sel = list(range(len(rows))) if indices is None else list(indices)
    centfreq = freqs[len(freqs) // 2]

    if reference_index is None:
        Ps = [r["P"] if r["P"] is not None else -1.0 for r in rows]
        reference_index = int(np.argmax(Ps)) if max(Ps) >= 0 else len(rows) - 1
    const, const_err, ref = establish_reference(
        freqs, rows[reference_index]["signal"], model, False, centfreq)

    fm = dm.FastModel(freqs, const, model, with_cube=False)
    results, prev = [], None
    for i in sel:
        res = fit_signal_fast(fm, rows[i]["signal"], const, const_err,
                              free=("r",), p0_override=prev)
        prev = res["params"]
        results.append(res)
    return freqs, [rows[i] for i in sel], results, ref


def _run_settings(cooldown, run):
    """Representative (centfreq, span) NMR window of a run (median over its rows)."""
    cf = np.nanmedian(cooldown.centfreq[run.start:run.stop])
    sp = np.nanmedian(cooldown.span[run.start:run.stop])
    return cf, sp


def _run_scale(cooldown, run):
    """Representative (VME Mode, IF Atten) of a run -- the scale/gain-defining settings the
    baseline must share with the line (VME Mode is the ~17x amplifier; IF Atten changes the
    gain too). Phase Tune is a second-order scale effect, not matched here."""
    from collections import Counter
    modes = [str(cooldown.vme_mode[i]).strip() for i in range(run.start, run.stop)
             if str(cooldown.vme_mode[i]).strip()]
    mode = Counter(modes).most_common(1)[0][0] if modes else ""
    ia = np.nanmedian(cooldown.if_atten[run.start:run.stop]) if cooldown.if_atten is not None else np.nan
    return mode, ia


def _row_time_distance(run, row_index):
    """Row-index distance from ``row_index`` to a run (0 if inside)."""
    if run.start <= row_index < run.stop:
        return 0
    return run.start - row_index if row_index < run.start else row_index - run.stop + 1


def baseline_for_row(cooldown, baseline_runs, row_index, trim=1,
                     tol_cf=0.02, tol_sp=0.01):
    """Trimmed-average baseline for the line at ``row_index``, matched to the line's
    NMR window.

    A baseline is the background for a given (centfreq, span) window, so it must be
    matched by those settings -- NOT by the field (which is deliberately shifted to
    move the signal off-screen during a baseline) and not merely by time. The nearest
    baseline run in time whose window matches the line's (within ``tol_cf``/``tol_sp``)
    is used, searching BOTH before and after the line (a matching baseline sometimes
    follows the data). If no run matches the window, the nearest run's average is
    resampled onto the line's frequency grid (the background is a function of absolute
    frequency). See docs/nmr_operations.tex."""
    if not baseline_runs:
        return np.zeros(cooldown.width)
    cf0, sp0 = cooldown.centfreq[row_index], cooldown.span[row_index]
    m0 = str(cooldown.vme_mode[row_index]).strip() if cooldown.vme_mode else ""
    ia0 = cooldown.if_atten[row_index] if cooldown.if_atten is not None else np.nan
    # nearest in time first; prefer a preceding run on ties (stable vs old behavior)
    ordered = sorted(baseline_runs,
                     key=lambda r: (_row_time_distance(r, row_index), r.start > row_index))
    if not (np.isfinite(cf0) and np.isfinite(sp0)):  # unknown window -> nearest run as-is
        return io.average_baseline(cooldown, ordered[0], trim=trim)

    def _scale_ok(r):
        # the baseline must share the line's SCALE: same VME Mode (the ~17x amplifier) and
        # IF Atten. Only constrain on a setting when both sides know it (avoids over-filtering
        # on a stray unfilled glitch); a mismatched-scale baseline is ~17x off and unusable.
        rm, ria = _run_scale(cooldown, r)
        mode_ok = (not m0) or (not rm) or (m0 == rm)
        ia_ok = (not np.isfinite(ia0)) or (not np.isfinite(ria)) or abs(ia0 - ria) <= 0.5
        return mode_ok and ia_ok

    matches = [r for r in ordered
               if abs(_run_settings(cooldown, r)[0] - cf0) <= tol_cf
               and abs(_run_settings(cooldown, r)[1] - sp0) <= tol_sp and _scale_ok(r)]
    if matches:                                  # same window -> same axis, subtract directly
        return io.average_baseline(cooldown, matches[0], trim=trim)
    # no window match: resample the nearest baseline onto the line's frequency axis
    r = ordered[0]
    base = io.average_baseline(cooldown, r, trim=trim)
    cfb, spb = _run_settings(cooldown, r)
    if not (np.isfinite(cfb) and np.isfinite(spb)):
        return base
    fbase = cfb + spb * (np.arange(cooldown.width) / cooldown.width - 0.5)
    return np.interp(cooldown.freq_axis(row_index), fbase, base)


def build_subset_signal(cooldown, pol_indices, baseline_runs, trim=1):
    """Average the chosen polarization rows, subtract the nearest baseline average.

    Returns (freqs, signal, timestamp) for fitting.
    """
    sig = io.average_sweeps(cooldown, pol_indices)
    base = baseline_for_row(cooldown, baseline_runs, int(np.mean(pol_indices)), trim)
    i0 = pol_indices[len(pol_indices) // 2]
    return cooldown.freq_axis(i0), sig - base, cooldown.timestamps[i0]


# ---------------------------------------------------------------------------
# Auto pipeline: phase segments + signal-driven classification + per-region fits
# ---------------------------------------------------------------------------
HB_POWER_ON = -20.0     # Holeburning Power above this (dBm) is a "burner on" prior

# Hole-detection hysteresis: a confident (strong) P-spread cut by default, dropping
# to a sensitive cut for a window after each strong flag so a burnt hole is
# followed through its relaxation (which decays below the strong cut) without the
# sensitive cut ever flooding the unburnt equilibrium.
#
# Floors recalibrated (fitter/calib_pspread.py) on the _perbin_P_spread metric
# against logbook-confirmed data: clean equilibrium, INCLUDING fast spin-up where a
# rising P inflates the per-bin spread, tops out at ~0.033 (Dec-13 13-16h, 1898
# lines, max 0.0332). Real ssRF burns (Dec-15 evening, in logbook windows) run
# 0.12-0.72. The floors sit in that gap so spin-up no longer flags. The P-fraction
# (frac*P) almost never binds above the floor -- P-depletion by a hole is roughly
# P-independent, so a flat floor is the physically correct cut; the frac is kept
# only as a mild high-P boost.
HOLE_STRONG_FRAC = 0.06        # baseline cut: classifies a clear hole at any time
HOLE_STRONG_FLOOR = 0.05       # above the spin-up equilibrium ceiling (~0.033)
HOLE_WEAK_FRAC = 0.04          # sensitive cut: also counts inside the relaxation window
HOLE_WEAK_FLOOR = 0.04         # still above spin-up; only ever applied in an armed window
HOLE_ARM_FRAC = 0.09           # window-arming cut: a confident burn, well above any
HOLE_ARM_FLOOR = 0.10          # equilibrium/spin-up spread, so unburnt lines (incl.
                               # spin-up) never arm the sensitive relaxation window
HOLE_ARM_CONSECUTIVE = 2       # consecutive arm-level flags needed (a real burn is sustained)
HOLE_RELAX_WINDOW = 600.0      # seconds (10 min) of sensitive detection after the last arm
HOLE_RATIO_CEILING = 0.52      # a single-r ratio P above this is unphysical for an
                               # equilibrium line (the spin-up tops out ~49%); it means
                               # the single-spin-temperature fit was fooled by a burnt
                               # line -> hole_burnt (use the area method). See
                               # docs/nmr_operations.tex "Fitting pitfalls".


def phase_segments(cd, indices):
    """Split indices into contiguous runs of equal Phase-Tune voltage.

    NaN phase_tune inherits the previous valid value (so isolated unrecorded rows
    don't fragment a segment). Returns a list of (phase_tune_value, [indices]).
    """
    segs, cur, curkey, last_valid = [], None, object(), None
    for i in indices:
        v = cd.phase_tune[i]
        if not np.isnan(v):
            last_valid = round(float(v), 3)
        key = round(float(v), 3) if not np.isnan(v) else last_valid
        if key != curkey:
            cur = []
            segs.append((key, cur))
            curkey = key
        cur.append(i)
    return segs


def _ref_dict(const, model, r, phase):
    """Equilibrium-reference dict for holeburn (constants + r_eq + phase)."""
    ref = {k: const[k] for k in CONST_KEYS[model]}
    ref.update(model=model, r=r, phase=phase)
    return ref


def fit_freqhop(fm_nocube, signal_flat, const, phase, cc, p0_r=(1.5, 1.5), bounds_r=(0.3, 4.0)):
    """Frequency-hopping (two-spin-temperature) fit: independent rN (-1<->0) and rP (0<->+1),
    with the lineshape constants/phase/scale fixed. ``p0_r`` warm-starts (rN, rP) -- pass the
    single-r solution (r, r) on noisy data so the free split does not over-fit the wiggle into a
    spurious rN!=rP (the fit still moves to a genuine split where the data supports one).
    ``bounds_r`` = (lo, hi) on BOTH rN and rP -- narrow it around the single-r r on a noisy day
    (genuine two-temperature splits are small, ~0.1; a wild rN<<rP is the wiggle over-fitting).

    *** EXPERIMENTAL (2026-06-26) -- the ratio P/Q here (ratio_P_nonbolt) is a new, not-yet-
    externally-validated method; trust it only once it reproduces the dissertation's
    frequency-hopping P & Q vs time (Dec-10-2020 spin-up). The area P/Q is the established method. ***

    Returns BOTH polarization methods. The RATIO method (primary) is exact:
    P = ratio_P_nonbolt(rN, rP) = (rN*rP-1)/(rN*rP+rN+1), Q likewise -- derived from the three
    spin-state populations (the two transitions each in equilibrium with themselves but not each
    other), and reducing exactly to the ordinary single-r ratio fit when rN==rP. The AREA method
    P=cc*(I+ + I-) is kept as an independent cross-check; it agrees with the ratio method near
    equilibrium and diverges at strong hopping because its Xi=1 non-Boltzmann MODEL-component
    integral omits the 1-Q population factor (the RAW-data area itself carries no Xi
    non-linearity at zeta->0 -- see the main technote's 'Frequency hopping' section).
    """
    scale = const["scale"]
    freqs = fm_nocube.freqs

    def func(freq, rN, rP, x3, x2, x1, x0):
        return fm_nocube.real_nonbolt(rN, rP, scale, phase) + dm.cubic(freqs, x3, x2, x1, x0)

    big = 1e5
    rlo, rhi = float(bounds_r[0]), float(bounds_r[1])
    lo = [rlo, rlo, -big, -big, -big, -big]
    hi = [rhi, rhi, big, big, big, big]
    eps = 1e-3 * (rhi - rlo)
    p0 = [float(np.clip(p0_r[0], rlo + eps, rhi - eps)), float(np.clip(p0_r[1], rlo + eps, rhi - eps)), 0, 0, 0, 0]
    popt, pcov = curve_fit(func, freqs, signal_flat, p0=p0,
                           bounds=(lo, hi), maxfev=4000, ftol=1e-4, xtol=1e-4)   # loosened: same answer, far fewer evals
    rN, rP = popt[0], popt[1]
    # JOINT (rN, rP) covariance -> sigma_P/sigma_Q with the correlation (the freq-hop P depends on
    # BOTH asymmetries, so the off-diagonal cov(rN,rP) matters; see deuteron_model.propagate_2).
    cov2 = np.asarray(pcov, float)[:2, :2] if pcov is not None and np.ndim(pcov) == 2 else np.zeros((2, 2))
    P_err = dm.propagate_2(dm.ratioP_nonbolt_grad(rN, rP), cov2)
    Q_err = dm.propagate_2(dm.ratioQ_nonbolt_grad(rN, rP), cov2)
    comps = fm_nocube._components_nonbolt(rN, rP, scale, phase)
    bond_labels = [lbl for *_, lbl in fm_nocube._bonds]
    I_plus = sum(dm.riemann_sum(freqs, comps[(lbl, 1)]) for lbl in bond_labels)
    I_minus = sum(dm.riemann_sum(freqs, comps[(lbl, -1)]) for lbl in bond_labels)
    fit_curve = func(freqs, *popt)
    flat = signal_flat - dm.cubic(freqs, *popt[2:6])   # data minus the fitted cubic -> flat wings (for the viewer)
    rN_err = float(np.sqrt(abs(cov2[0, 0]))); rP_err = float(np.sqrt(abs(cov2[1, 1])))
    return dict(P=dm.ratio_P_nonbolt(rN, rP), Q=dm.ratio_Q_nonbolt(rN, rP),   # exact ratio method (primary)
                P_area=cc * (I_plus + I_minus), Q_area=cc * (I_plus - I_minus),  # area-method cross-check
                rN=rN, rP=rP, rN_err=rN_err, rP_err=rP_err, cov_rNrP=float(cov2[0, 1]),
                P_err=P_err, Q_err=Q_err, fit=fit_curve, flat=flat, components=comps)


def fit_freqhop_area(fm_nocube, signal, r_eq, scale, phase, cc):
    """LINEAR two-amplitude frequency-hopping fit (the dissertation-style area method).

    Instead of a nonlinear (rN, rP) fit -- which is ill-conditioned at the tiny-signal zero-crossing
    and runs its asymmetries to the bounds -- take the EQUILIBRIUM (single-r_eq) spin-flip component
    curves and let their two AMPLITUDES float independently:

        signal(freq) = c_plus * S_plus(freq) + c_minus * S_minus(freq) + cubic

    S_plus = sum of the +1 (0<->+1) bond components at r_eq, S_minus = sum of the -1 (-1<->0)
    components; both already carry Xi(r_eq) and the phase. c_plus, c_minus (and the cubic) are found
    by ORDINARY LEAST SQUARES -- fully linear, so nothing can run away. The two-temperature physics is
    entirely in c_plus != c_minus. Then the area method (Eq. 4.11), with the equilibrium calibration
    cc = ratio_P(r_eq)/(A_plus + A_minus) passed in:

        I_plus = c_plus * A_plus,  I_minus = c_minus * A_minus,   A = integral of the component
        P = cc * (I_plus + I_minus),   Q = cc * (I_plus - I_minus)

    P stays proportional to the lineshape area (well-behaved through the crossing); Q carries the
    non-Boltzmann behaviour. Uses the established single-r Xi(r_eq) only -- no Xi(rN,rP) needed."""
    freqs = np.asarray(fm_nocube.freqs, dtype=float)
    signal = np.asarray(signal, dtype=float)
    comps = fm_nocube._components(r_eq, scale, phase)          # equilibrium components (Xi(r_eq) inside)
    labels = [lbl for *_, lbl in fm_nocube._bonds]
    S_plus = sum(comps[(lbl, 1)] for lbl in labels)
    S_minus = sum(comps[(lbl, -1)] for lbl in labels)
    x = freqs - freqs.mean()                                  # centre for cubic conditioning
    M = np.column_stack([S_plus, S_minus, x ** 3, x ** 2, x, np.ones_like(x)])
    coef, *_ = np.linalg.lstsq(M, signal, rcond=None)
    c_plus, c_minus = float(coef[0]), float(coef[1])
    cube = M[:, 2:] @ coef[2:]
    fit = M @ coef
    A_plus = float(dm.riemann_sum(freqs, S_plus))
    A_minus = float(dm.riemann_sum(freqs, S_minus))
    I_plus, I_minus = c_plus * A_plus, c_minus * A_minus
    return dict(P=cc * (I_plus + I_minus), Q=cc * (I_plus - I_minus),
                P_area=cc * (I_plus + I_minus), Q_area=cc * (I_plus - I_minus),
                c_plus=c_plus, c_minus=c_minus, I_plus=I_plus, I_minus=I_minus,
                fit=fit, flat=signal - cube, components=comps)


def _reduced_chisq(data, fit):
    """Reduced chi-squared of a fit to the (processed) NMR signal, using the mean
    absolute residual as the per-point sigma (matches fit_signal's convention)."""
    data = np.asarray(data, dtype=float)
    fit = np.asarray(fit, dtype=float)
    std = float(np.mean(np.abs(data - fit))) or 1.0
    return dm.chisq_red(data, fit, std)


_PSPREAD_DR = 0.01
# Per-bin P-spread is measured only within the PRIMARY signal: |f-omegD| <=
# PSPREAD_BAND * omegQ1. The deuteron line spans R in [-2, 2] (6*omegQ) but a
# secondary bond (omegQ2 > omegQ1, tiny amplitude) only adds noisy far-wing bins;
# the band is shrunk slightly below 6 to avoid the soft edges too.
PSPREAD_BAND = 5.5


def _perbin_P_spread(fm, res, newton=3):
    """Localized per-bin VECTOR-POLARIZATION excursion of a fitted line.

    Solves for the effective r at each frequency bin (a few vectorized Newton
    steps from the fitted r, using the line's own phase), converts each to P via
    the ratio formula, and returns the largest localized P excursion inside the
    signal body. Polarization depletion is the physical hole signature and is
    roughly P-independent in magnitude, so one threshold works across all P --
    unlike a per-bin r spread, which scales with the strongly varying dP/dr.
    """
    p = res["params"]
    r0, scale, phase = p["r"], p["scale"], p["phase"]
    data = np.asarray(res["signal_flat"], dtype=float)
    dr = _PSPREAD_DR

    # Remove a peak-position misalignment first. A tiny frequency offset between the
    # model and the data reads as a large derivative-shaped per-bin swing at the
    # steep peaks (data ~ model - df * d(model)/df). It is a GLOBAL shift, so project
    # the d(model)/df component out of the data; a hole is local and survives.
    m0 = fm.real(r0, scale, phase)
    dmdf = np.gradient(m0, fm.freqs)
    a = float((data - m0) @ dmdf / (dmdf @ dmdf + 1e-30))
    data = data - a * dmdf

    rb = np.full(len(fm.freqs), r0, dtype=float)
    for _ in range(newton):
        m = fm.real(rb, scale, phase)
        dmdr = (fm.real(rb + dr, scale, phase) - m) / dr
        dmdr = np.where(np.abs(dmdr) < 1e-12, 1e-12, dmdr)
        rb = np.clip(rb - (m - data) / dmdr, 0.3, 6.0)

    dev = dm.ratio_P(rb) - dm.ratio_P(r0)               # per-bin P minus fit P
    dmdr0 = (fm.real(r0 + dr, scale, phase) - m0) / dr
    half_width = PSPREAD_BAND * p["omegQ1"]             # primary (dominant-bond) width
    return classify.body_excursion(dev, dmdr0, fm.freqs, p["omegD"], half_width)


def scan_equilibrium(cd, indices, sigs, freqs, fm, const, const_err, omegD, snr_min=4.0):
    """Fit each candidate line with the equilibrium model and keep only those that
    ARE clean equilibrium signals: a real NMR signal is present (SNR) and a single-r
    fit describes it (classified equilibrium -- no hole/asymmetry). Used to choose
    the lineshape-constant reference, the area-method calibration reference, and the
    phase anchors, because amplitude alone is a poor selector (a hole-burnt or
    distorted line can be large). Returns a list of dicts (i, P, phase, sec, res).
    """
    out = []
    for i in indices:
        sig = sigs[i]
        flat = classify.wing_flatten(freqs, sig)
        noise = classify.wing_noise(flat)
        snr = classify.amplitude(flat) / noise
        if snr < snr_min:
            continue
        try:
            res = fit_signal_fast(fm, sig, const, const_err, free=("r", "phase"))
        except RuntimeError:
            continue
        resid = res["signal_flat"] - res["fit"]
        hole, bump = classify.hole_bump(freqs, resid, res["fit"], omegD)
        asym = classify.asymmetry_score(freqs, resid, res["fit"], omegD)
        pspread = _perbin_P_spread(fm, res)
        if classify.classify(snr, hole, bump, asym, pspread, eq_P=res["P"],
                             pspread_frac=HOLE_STRONG_FRAC,
                             pspread_floor=HOLE_STRONG_FLOOR) == "equilibrium":
            out.append(dict(i=i, P=res["P"], phase=res["params"]["phase"],
                            sec=cd.seconds[i], res=res))
    return out


def _phase_from_scan(scan, fallback_phase):
    """Smooth phase(time) from the clean-equilibrium scan points (median-filtered)."""
    if not scan:
        return np.array([0.0, 1.0]), np.array([fallback_phase, fallback_phase])
    pts = sorted((d["sec"], d["phase"]) for d in scan)
    at = np.array([p[0] for p in pts])
    ap = np.array([p[1] for p in pts])
    if len(ap) >= 3:
        ap = np.array([np.median(ap[max(0, k - 1):k + 2]) for k in range(len(ap))])
    return at, ap


def cooldown_constants(cd, preset, model, snr_min=4.0):
    """Establish the global lineshape constants for a cool-down.

    Baseline-subtracts every polarization line, then chooses the constants
    reference as the highest-P confirmed clean equilibrium line (not the largest --
    a hole-burnt or distorted line can be big). Returns
    (const, const_err, freqs, baselines, sigs, amps, reference_result).
    """
    runs = io.detect_runs(cd, {"Baseline", "Polarization"})
    baselines = [r for r in runs if r.status == "Baseline"]
    pol_idx = [i for r in runs if r.status == "Polarization" for i in range(r.start, r.stop)]
    # Build the global frequency axis from the DOMINANT NMR window (most common
    # centfreq/span), not merely the first line -- the first line may be a transient
    # at an off-window setting. Lines at other windows are fit on their own grid
    # later (see auto_fit_cooldown's window-model cache).
    from collections import Counter
    win = Counter((round(float(cd.centfreq[i]), 4), round(float(cd.span[i]), 4))
                  for i in pol_idx if np.isfinite(cd.centfreq[i]) and np.isfinite(cd.span[i]))
    dom_i = pol_idx[0]
    if win:
        dom = win.most_common(1)[0][0]
        dom_i = next((i for i in pol_idx
                      if (round(float(cd.centfreq[i]), 4), round(float(cd.span[i]), 4)) == dom),
                     pol_idx[0])
    freqs = cd.freq_axis(dom_i)
    sigs, amps = {}, {}
    for i in pol_idx:
        s = cd.sweeps[i] - baseline_for_row(cd, baselines, i)
        sigs[i] = s
        amps[i] = float(np.percentile(s, 99) - np.percentile(s, 1))

    preset_const = {k: preset[k] for k in CONST_KEYS[model] if k in preset}
    preset_const["phase"] = np.radians(preset.get("phase_deg", 0.0))
    # Seed omegD at the dominant WINDOW CENTRE, not the preset's omegD -- the preset
    # is for a different field (e.g. Dec-13's 33.18 vs Dec-15/16's 31.70). With the
    # preset omegD the model peak sits entirely outside the sweep window, so every
    # scan fit is garbage and the "highest-P" reference is picked from noise (P~15%
    # on Dec-16). The window centre is the correct omegD prior (it is only a seed --
    # establish_reference then fits omegD freely). Same fix as preset_to_p0.
    preset_const["omegD"] = float(np.mean(freqs))
    fm0 = dm.FastModel(freqs, preset_const, model, with_cube=True)
    preset_err = {k: 0.0 for k in preset_const}
    sample = pol_idx[:: max(1, len(pol_idx) // 80)]
    scan0 = scan_equilibrium(cd, sample, sigs, freqs, fm0, preset_const, preset_err,
                             preset_const["omegD"], snr_min)

    # Reference selection. The fast scan holds omegD FIXED at the window centre, only
    # an approximation of the true Larmor; a small offset distorts the fitted P (a
    # clean ~45% line can read ~17%), so the scan's own P cannot rank candidates.
    # Instead shortlist the strongest clean-equilibrium lines by amplitude and
    # FULL-fit each (omegD + all constants + phase free) -- picking the highest
    # PHYSICAL polarization (>=15%, <=49%) at a near-zero phase (|phase|<=25 deg).
    # Magnet-ramp / field-moved garbage has large amplitude but full-fits to an
    # unphysical P or a wild phase and is rejected. The reference is also restricted
    # to a FIELD-STABLE period: the field within 0.05 T of the cool-down's dominant
    # field (excludes the 5 T excursions and ramp-downs). See docs/nmr_operations.tex.
    field = np.asarray(cd.field_t, dtype=float)
    fvals = [field[i] for i in pol_idx if i < len(field) and np.isfinite(field[i])]
    domfield = float(np.median(fvals)) if fvals else None

    def field_stable(i):
        if domfield is None or i >= len(field) or not np.isfinite(field[i]):
            return True
        return abs(field[i] - domfield) <= 0.05

    cands = [d for d in scan0 if field_stable(d["i"])] or scan0
    cands.sort(key=lambda d: amps[d["i"]], reverse=True)
    # Full-fit the strongest field-stable candidates (omegD + all constants + phase
    # free, with the phase TIGHTLY bounded so the fit cannot fall into the sign-
    # flipped -143 deg / negative-P basin) and keep the highest PHYSICAL polarization
    # (15-49% P, |phase|<=25 deg). The field filter has already removed the magnet-
    # ramp / 5 T-excursion garbage, so the top-amplitude candidates are genuine
    # high-P lines; the search early-exits once a clearly near-maximal line is found.
    best = None                                          # (const, const_err, gref, P)
    for d in cands[:6]:
        try:
            c, ce, ref = establish_reference(freqs, sigs[d["i"]], model, True, preset=preset)
        except Exception:
            continue
        P, ph = ref["P"], abs(np.degrees(ref["params"]["phase"]))
        if 0.15 <= P <= 0.49 and ph <= 25.0 and (best is None or P > best[3]):
            best = (c, ce, ref, P)
            if P >= 0.45:                                # essentially maximal; stop
                break
    if best is None:                                     # nothing physical -> best amp line
        gi = cands[0]["i"] if cands else max(sample, key=lambda i: amps[i])
        c, ce, ref = establish_reference(freqs, sigs[gi], model, True, preset=preset)
        best = (c, ce, ref, ref["P"])
    const, const_err, gref = best[0], best[1], best[2]
    return const, const_err, freqs, baselines, sigs, amps, gref


def auto_fit_cooldown(cd, model, preset, snr_min=4.0, progress=print):
    """Classify every NMR line from its signal and fit it with the right method.

    Returns (freqs, results) where each result is a dict with keys: timestamp,
    type, method, P, Q, r, snr, hole_score, asym_score, params (or None for
    no_signal). Constants are global; phase is per Phase-Tune segment; the
    hole-burnt calibration reference is the strongest non-burner line per segment.
    """
    runs = io.detect_runs(cd, {"Baseline", "Polarization"})
    pol_idx = [i for r in runs if r.status == "Polarization" for i in range(r.start, r.stop)]
    if not pol_idx:
        return None, []
    const, const_err, freqs, baselines, sigs, amps, gref = cooldown_constants(
        cd, preset, model, snr_min)
    progress(f"  constants from highest-P clean equilibrium line "
             f"(P={100*gref['P']:.1f}%): omegD={const['omegD']:.5f}")

    # FastModel bakes in the constants but takes phase as a call argument, so one
    # model serves every phase -- build once and reuse across segments/lines.
    fm_cube = dm.FastModel(freqs, const, model, with_cube=True)

    # Per-NMR-window model cache. Most lines share the dominant (centfreq, span)
    # window that `freqs`/`fm_cube` were built for; a minority sit at a different
    # window (a wider/narrower span, or a shifted centre). Those are fit on their own
    # frequency grid with the same intrinsic constants and omegD shifted by the
    # window-centre offset (omegD itself stays fixed within the fast fit). See
    # docs/nmr_operations.tex. window_model(i) -> (freqs_i, fastmodel_i, omegD_i).
    N = cd.width
    sp_ref = (freqs[-1] - freqs[0]) * N / (N - 1)
    cf_ref = float(freqs[0] + sp_ref / 2.0)
    dom_key = (round(cf_ref, 4), round(float(sp_ref), 4))
    win_cache = {dom_key: (freqs, fm_cube, float(const["omegD"]))}

    def window_model(i):
        cf_i, sp_i = float(cd.centfreq[i]), float(cd.span[i])
        if not (np.isfinite(cf_i) and np.isfinite(sp_i)):
            return win_cache[dom_key]
        key = (round(cf_i, 4), round(sp_i, 4))
        wm = win_cache.get(key)
        if wm is None:
            fw = cf_i + sp_i * (np.arange(N) / N - 0.5)
            cw = {**{k: const[k] for k in CONST_KEYS[model]},
                  "omegD": float(const["omegD"]) + (cf_i - cf_ref)}
            wm = (fw, dm.FastModel(fw, cw, model, with_cube=True), cw["omegD"])
            win_cache[key] = wm
        return wm

    global_phase = np.radians(preset.get("phase_deg", 0.0))
    results = []
    for segkey, seg in phase_segments(cd, pol_idx):
        # Scan a time-spread sample of the segment for clean equilibrium lines;
        # these set the phase drift curve AND the area-method calibration reference.
        seg_sample = seg[:: max(1, len(seg) // 80)]
        scan = scan_equilibrium(cd, seg_sample, sigs, freqs, fm_cube, const, const_err,
                                const["omegD"], snr_min)
        anchor_t, anchor_ph = _phase_from_scan(scan, global_phase)

        def phase_at(i):
            return float(np.interp(cd.seconds[i], anchor_t, anchor_ph))

        # Area-method calibration reference: the highest-P clean equilibrium line.
        if scan:
            refd = max(scan, key=lambda d: d["P"])
            ri = refd["i"]
        else:                                   # no clean equilibrium found in segment
            ri = max(seg, key=lambda i: amps[i])
        ref_const = {**{k: const[k] for k in CONST_KEYS[model]}, "phase": phase_at(ri)}
        rref = fit_signal_fast(fm_cube, sigs[ri], ref_const, const_err, free=("r",))
        ref = _ref_dict(ref_const, model, rref["params"]["r"], ref_const["phase"])
        solver = holeburn.HoleBurnSolver(freqs, ref, rref["signal_flat"])

        # Hole-burnt solver per NMR window: the dominant window uses the reference's
        # own clean-eq signal; a line at another window gets a solver built on that
        # window's grid with a synthetic equilibrium reference (the fitted reference
        # shape evaluated there). Lazily cached; off-window non-eq lines are rare.
        solver_cache = {dom_key: solver}

        def solver_for(i):
            cf_i, sp_i = float(cd.centfreq[i]), float(cd.span[i])
            if not (np.isfinite(cf_i) and np.isfinite(sp_i)):
                return solver
            key = (round(cf_i, 4), round(sp_i, 4))
            s = solver_cache.get(key)
            if s is None:
                fw, fmw, omegDw = window_model(i)
                refw = _ref_dict({**{k: const[k] for k in CONST_KEYS[model]},
                                  "omegD": omegDw, "phase": ref_const["phase"]},
                                 model, rref["params"]["r"], ref_const["phase"])
                synth = fmw.real(rref["params"]["r"], rref["params"]["scale"], ref_const["phase"])
                s = holeburn.HoleBurnSolver(fw, refw, synth)
                solver_cache[key] = s
            return s

        progress(f"  segment phase_tune={segkey}: {len(seg)} lines, "
                 f"{len(scan)} clean-eq anchors, phase "
                 f"{np.degrees(anchor_ph.min()):.1f}..{np.degrees(anchor_ph.max()):.1f}deg, "
                 f"ref P={100*rref['P']:.1f}%")

        prev = None
        armed_until_t = None        # time (s) the relaxation window was last armed
        arm_run = 0                 # consecutive arm-level flags
        for i in seg:
            sig = sigs[i]
            fi, fmi, omegDi = window_model(i)        # this line's NMR window
            flat = classify.wing_flatten(fi, sig)
            noise = classify.wing_noise(flat)
            amp = classify.amplitude(flat)
            snr = amp / noise
            ts = cd.timestamps[i]
            if snr < snr_min:
                results.append(dict(timestamp=ts, type="no_signal", method="-",
                                    P=None, Q=None, P_ratio=None, Q_ratio=None,
                                    P_area=None, Q_area=None, r=None, snr=snr,
                                    hole_score=0.0, bump_ratio=0.0, asym_score=0.0,
                                    pspread=0.0, red_chisq=None, eq_chisq=None,
                                    area_chisq=None, params=None, signal=None))
                continue
            # Phase is taken from the smooth drift curve phase_at(i) -- built by the
            # scan, which fits the phase freely on clean-equilibrium anchors, so it
            # tracks the (slowly drifting) phase without the per-line wandering that a
            # free phase causes here (3 free params + cubic find wrong minima on clean
            # lines and flood the hole flag). Fit r only (scale absorbed by r via Xi).
            line_phase = phase_at(i)
            line_const = {**{k: const[k] for k in CONST_KEYS[model]},
                          "omegD": omegDi, "phase": line_phase}
            eqres = fit_signal_fast(fmi, sig, line_const, const_err, free=("r",),
                                    p0_override=prev)
            prev = eqres["params"]
            resid = eqres["signal_flat"] - eqres["fit"]
            hole, bump_ratio = classify.hole_bump(fi, resid, eqres["fit"], omegDi)
            asym = classify.asymmetry_score(fi, resid, eqres["fit"], omegDi)
            pspread = _perbin_P_spread(fmi, eqres)
            # Hysteresis. (1) Baseline: the 6% cut classifies a clear hole anytime.
            # (2) Arming: a run of confident, above-equilibrium-ceiling flags (the 9%
            # cut) marks a real burn and opens a 10-min window. (3) In-window: the
            # sensitive 4% cut also counts, following the hole as it relaxes below the
            # 6% cut -- but only after a confirmed burn, so it can't flood equilibrium.
            t = classify.classify(snr, hole, bump_ratio, asym, pspread, eq_P=eqres["P"],
                                  hb_power=cd.hb_power[i], snr_min=snr_min,
                                  pspread_frac=HOLE_STRONG_FRAC,
                                  pspread_floor=HOLE_STRONG_FLOOR)
            # A single-r ratio P above the equilibrium ceiling is unphysical -> the
            # single-spin-temperature fit was fooled by a burnt line (the phase also
            # wanders); flag it and route to the area method.
            if eqres["P"] > HOLE_RATIO_CEILING:
                t = "hole_burnt"
            if pspread >= classify.pspread_threshold(eqres["P"], HOLE_ARM_FRAC, HOLE_ARM_FLOOR):
                arm_run += 1
                if arm_run >= HOLE_ARM_CONSECUTIVE:
                    armed_until_t = cd.seconds[i]            # arm/extend the window
            else:
                arm_run = 0
            if (t == "equilibrium" and armed_until_t is not None
                    and cd.seconds[i] - armed_until_t <= HOLE_RELAX_WINDOW
                    and classify.classify(snr, hole, bump_ratio, asym, pspread,
                                          eq_P=eqres["P"], hb_power=cd.hb_power[i],
                                          snr_min=snr_min, pspread_frac=HOLE_WEAK_FRAC,
                                          pspread_floor=HOLE_WEAK_FLOOR) == "hole_burnt"):
                t = "hole_burnt"

            # Always report the ratio method (single-r fit) and an area-method
            # cross-check. In equilibrium the area uses the self-consistent
            # per-signal calibration (area P == ratio P, area Q ~ ratio Q). Out of
            # equilibrium the single-r fit fails, so the area uses the
            # differential-binning reconstruction calibrated from the segment's
            # equilibrium reference -- and it is the recommended value.
            P_ratio, Q_ratio = eqres["P"], eqres["Q"]
            eq_chisq = eqres["red_chisq"]
            res = eqres
            res.update(snr=snr, hole_score=hole, asym_score=asym)
            if t == "equilibrium":
                # Self-consistent per-signal area (area P == ratio P, area Q ~ ratio Q).
                P_area, Q_area, area_chisq = eqres["area_P"], eqres["area_Q"], eq_chisq
                method, P, Q, chi2, proc = "ratio", P_ratio, Q_ratio, eq_chisq, eqres["signal_flat"]
            else:                            # out of equilibrium -> differential-binning area
                hb = solver_for(i).solve(sig)
                P_area, Q_area = hb["P"], hb["Q"]
                area_chisq = _reduced_chisq(hb["hb_flat"], hb["hb_fit"])
                method = "area (HB)" if t == "hole_burnt" else "area (non-eq)"
                P, Q, chi2, proc = P_area, Q_area, area_chisq, hb["hb_flat"]
            results.append(dict(timestamp=ts, type=t, method=method,
                                P=P, Q=Q, P_ratio=P_ratio, Q_ratio=Q_ratio,
                                P_area=P_area, Q_area=Q_area, r=res["params"]["r"],
                                snr=snr, hole_score=hole, bump_ratio=bump_ratio,
                                asym_score=asym, pspread=pspread, red_chisq=chi2,
                                eq_chisq=eq_chisq, area_chisq=area_chisq,
                                params=res, signal=proc))
    return freqs, results


def write_auto_results(path, freqs, results, names):
    """Write the combined auto-classification results CSV (one row per line)."""
    def pct(x):
        return "" if x is None else 100 * x

    header = ["timestamp", "type", "recommended", "P(%)", "Q(%)",
              "P_ratio(%)", "Q_ratio(%)", "P_area(%)", "Q_area(%)", "Q_eq(%)",
              "reduced_chisq", "ratio_chisq", "area_chisq",
              "r", "snr", "P_spread", "hole_depth", "bump_ratio", "asym_score"]
    for n in names:
        header.append("phase(deg)" if n == "phase" else n)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for d in results:
            row = [d["timestamp"], d["type"], d["method"],
                   pct(d["P"]), pct(d["Q"]),
                   pct(d.get("P_ratio")), pct(d.get("Q_ratio")),
                   pct(d.get("P_area")), pct(d.get("Q_area")),
                   "" if d.get("P_ratio") is None else 100 * dm.Q_equilibrium(d["P_ratio"]),
                   "" if d.get("red_chisq") is None else d["red_chisq"],
                   "" if d.get("eq_chisq") is None else d["eq_chisq"],
                   "" if d.get("area_chisq") is None else d["area_chisq"],
                   "" if d["r"] is None else d["r"],
                   d["snr"], d.get("pspread", 0.0), d["hole_score"],
                   d.get("bump_ratio", 0.0), d["asym_score"]]
            p = d["params"]
            for n in names:
                if p is None:
                    row.append("")
                else:
                    v = p["params"].get(n, "")
                    row.append(np.degrees(v) if (n == "phase" and v != "") else v)
            w.writerow(row)


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------
def _ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    ans = input(f"{prompt}{suffix}: ").strip()
    return ans if ans else (str(default) if default is not None else "")


def _ask_choice(prompt, options, default=0):
    for i, opt in enumerate(options):
        mark = " (default)" if i == default else ""
        print(f"  {i + 1}) {opt}{mark}")
    while True:
        ans = _ask(prompt, default + 1)
        try:
            k = int(ans) - 1
            if 0 <= k < len(options):
                return k
        except ValueError:
            pass
        print("  please enter a number from the list.")


def _parse_index_spec(spec, n):
    """Parse '1-50', '1,5,9', 'all' (1-based) into a sorted 0-based index list."""
    spec = spec.strip().lower()
    if spec in ("", "all"):
        return list(range(n))
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a) - 1, int(b)))
        elif part:
            out.add(int(part) - 1)
    return sorted(i for i in out if 0 <= i < n)


def _choose_model():
    materials = list(dm.MATERIALS.keys())
    print("\nTarget material / model:")
    opts = materials + ["hydrocarbon (generic double bond)", "ammonia (generic single bond)"]
    k = _ask_choice("Choose material", opts, default=0)
    if k < len(materials):
        preset = dm.MATERIALS[materials[k]]
        return preset["model"], preset
    return ("double", {}) if k == len(materials) else ("single", {})


# ---------------------------------------------------------------------------
# Interactive flows
# ---------------------------------------------------------------------------
def _output_dir(source):
    """Per-run outputs go to results/<COOLDOWN>/main/<source-name>/ -- the full-fit
    ("main") location for this cool-down (see triage/paths.py)."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "triage"))
    import paths
    base = os.path.splitext(os.path.basename(source))[0]
    outdir = os.path.join(paths.MAIN_OUT, base)
    os.makedirs(outdir, exist_ok=True)
    return outdir, base


def run_flats_interactive(path):
    print(f"\nDetected a pre-flattened (flats) file: {os.path.basename(path)}")
    model, _preset = _choose_model()
    freqs, rows = io.read_flats(path)
    spec = _ask(f"Fit how many rows? (all / N / range like 1-20) of {len(rows)}", "all")
    idx = _parse_index_spec(spec, len(rows))

    # Reference row: highest recorded P (or last) -- fit all params to fix constants.
    Ps = [r["P"] if r["P"] is not None else -1.0 for r in rows]
    ref_i = int(np.argmax(Ps)) if max(Ps) >= 0 else len(rows) - 1
    print(f"Establishing lineshape constants from row {ref_i} "
          f"(recorded P={rows[ref_i]['P']}) ...")
    centfreq = freqs[len(freqs) // 2]
    const, const_err, ref = establish_reference(freqs, rows[ref_i]["signal"], model, False, centfreq)
    _report_one("reference " + rows[ref_i]["timestamp"], ref)
    fm = dm.FastModel(freqs, const, model, with_cube=False)

    print(f"\nFast-fitting {len(idx)} signals (r only) ...")
    names = fitter_spec(model, False, centfreq)[1]
    result_rows, cache_entries = [], []
    prev = None
    for n, i in enumerate(idx, 1):
        row = rows[i]
        try:
            res = fit_signal_fast(fm, row["signal"], const, const_err, free=("r",), p0_override=prev)
        except RuntimeError as e:
            print(f"  [{n}/{len(idx)}] {row['timestamp']}: fit failed ({e})")
            continue
        prev = res["params"]
        result_rows.append(_results_row(row["timestamp"], res))
        cache_entries.append((row["timestamp"], 100 * res["P"], row["signal"]))
        if n % 50 == 0 or n == len(idx):
            print(f"  [{n}/{len(idx)}] P={100*res['P']:.2f}%  Q={100*res['Q']:.2f}%")

    _save_all(path, names, result_rows, freqs, cache_entries,
              dict(input_type="flats", model=model, rows=len(result_rows),
                   reference_row=ref_i, fit="fast (r only)"))


def run_cooldown_interactive(path):
    print(f"\nReading raw cooldown {os.path.basename(path)} ...")
    cd = io.read_cooldown(path)
    runs = io.detect_runs(cd, {"Baseline", "Polarization"})
    baselines = [r for r in runs if r.status == "Baseline"]
    pols = [r for r in runs if r.status == "Polarization"]
    print(f"  {len(cd.timestamps)} sweeps, width {cd.width}")
    print(f"  {len(baselines)} baseline runs, {len(pols)} polarization runs:")
    for r in pols:
        hb = cd.hb_power[r.start:r.stop]
        print(f"    Pol run {r.index}: {r.count:4d} lines, {cd.timestamps[r.start]} "
              f"-> {cd.timestamps[r.stop-1]}  HBpower[{np.nanmin(hb):.0f},{np.nanmax(hb):.0f}]dBm")

    model, preset = _choose_model()
    mode = _ask_choice("\nFit mode", [
        "auto (detect & fit each region: skip no-signal, equilibrium, hole-burnt, freq-hopping)",
        "equilibrium only (ratio method)",
        "hole-burnt / non-Boltzmann, pick lines manually (area method)"], default=0)

    if mode == 0:
        return _run_auto(path, cd, model, preset)
    if mode == 2:
        return _run_holeburnt_interactive(path, cd, baselines, pols, model, preset)

    scope = _ask_choice("\nScope", [
        "subset (pick lines + baselines to average)",
        "whole cooldown (grouped in N-line blocks)"], default=1)
    names = fitter_spec(model, True, cd.centfreq[pols[0].start])[1]
    result_rows, cache_entries = [], []

    if scope == 0:
        pol_spec = _ask("Which polarization line numbers to average? (e.g. 1-10) "
                        f"out of {sum(p.count for p in pols)} total", "all")
        all_pol_idx = [i for p in pols for i in range(p.start, p.stop)]
        chosen = [all_pol_idx[k] for k in _parse_index_spec(pol_spec, len(all_pol_idx))]
        freqs, signal, ts = build_subset_signal(cd, chosen, baselines)
        res = fit_signal(freqs, signal, model, with_cube=True)
        _report_one(ts, res)
        result_rows.append(_results_row(ts, res))
        cache_entries.append((ts, 100 * res["P"], res["signal_flat"]))
    else:
        nblock = int(_ask("Average every N consecutive lines (1 = each line)", 3))
        all_pol_idx = [i for p in pols for i in range(p.start, p.stop)]
        blocks = [all_pol_idx[i:i + nblock] for i in range(0, len(all_pol_idx), nblock)]

        # Reference: a high-P block near the end of the spin-up (full free fit) to
        # fix the constants; then fast-fit (r + cubic) every block.
        ref_block = blocks[int(0.95 * (len(blocks) - 1))]
        freqs, ref_sig, ref_ts = build_subset_signal(cd, ref_block, baselines)
        print(f"Establishing constants from a high-P block ({ref_ts}) ...")
        const, const_err, ref = establish_reference(freqs, ref_sig, model, True)
        _report_one("reference " + ref_ts, ref)
        fm = dm.FastModel(freqs, const, model, with_cube=True)

        print(f"\nFast-fitting {len(blocks)} blocks of up to {nblock} lines (r + cubic) ...")
        prev = None
        for n, block in enumerate(blocks, 1):
            _, signal, ts = build_subset_signal(cd, block, baselines)
            try:
                res = fit_signal_fast(fm, signal, const, const_err, free=("r",), p0_override=prev)
            except RuntimeError:
                continue
            prev = res["params"]
            result_rows.append(_results_row(ts, res))
            cache_entries.append((ts, 100 * res["P"], res["signal_flat"]))
            if n % 50 == 0 or n == len(blocks):
                print(f"  [{n}/{len(blocks)}] {ts}  P={100*res['P']:.2f}%  Q={100*res['Q']:.2f}%")
        freqs = cd.freq_axis(pols[0].start)

    _save_all(path, names, result_rows, freqs, cache_entries,
              dict(input_type="raw", model=model, mode="equilibrium",
                   fit="fast (r + cubic)", group_size=nblock if scope == 1 else None,
                   baseline_runs=[b.index for b in baselines]))


def _run_auto(path, cd, model, preset):
    from collections import Counter
    if not preset:
        print("\nAuto mode needs material constants for the hole-burnt calibration.")
        print("Pick a specific material (not the generic option) and re-run.")
        return
    print("\nClassifying and fitting every NMR line (signal-driven) ...")
    freqs, results = auto_fit_cooldown(cd, model, preset)
    if not results:
        print("No polarization lines found.")
        return
    counts = Counter(d["type"] for d in results)
    print("\nClassification summary:")
    for t in ("equilibrium", "hole_burnt", "freq_hopping", "no_signal"):
        if counts.get(t):
            print(f"  {t:13s}: {counts[t]}")

    names = fitter_spec(model, False, freqs[len(freqs) // 2])[1]
    outdir, base = _output_dir(path)
    res_path = os.path.join(outdir, base + "_auto_results.csv")
    write_auto_results(res_path, freqs, results, names)
    fitted = [d for d in results if d["type"] != "no_signal"]
    cache_path = os.path.join(outdir, base + "_auto_flats_cache.csv")
    write_flats_cache(cache_path, freqs,
                      [(d["timestamp"], 100 * d["P"], d["signal"]) for d in fitted])
    write_provenance(os.path.join(outdir, base + "_auto_provenance.json"),
                     dict(input_type="raw", model=model, mode="auto",
                          counts=dict(counts), n_lines=len(results), source=path))
    print(f"\nWrote {len(results)} classified lines ({len(fitted)} fitted) to:")
    print(f"  {res_path}")
    print(f"  {cache_path}   (re-usable flats cache)")


def _run_holeburnt_interactive(path, cd, baselines, pols, model, preset):
    print("\nHole-burnt mode: select an equilibrium reference line, then the HB line.")
    all_pol_idx = [i for p in pols for i in range(p.start, p.stop)]
    eq_spec = _ask("Equilibrium reference line number (a high-P, non-HB line)", "1")
    eq_idx = [all_pol_idx[k] for k in _parse_index_spec(eq_spec, len(all_pol_idx))]
    hb_spec = _ask("Hole-burnt line number(s) to average", "2")
    hb_idx = [all_pol_idx[k] for k in _parse_index_spec(hb_spec, len(all_pol_idx))]

    freqs, eq_sig, eq_ts = build_subset_signal(cd, eq_idx, baselines)
    _, hb_sig, hb_ts = build_subset_signal(cd, hb_idx, baselines)

    const = {k: preset[k] for k in
             (["omegQ1", "omegQ2", "A1", "eta1", "eta2", "K", "scale"]
              if model == "double" else ["omegQ1", "A1", "eta1", "scale"])
             if k in preset}
    if not const:
        print("  No material preset constants available; fit an equilibrium line first.")
        return
    res = holeburn.run(freqs, eq_sig, hb_sig, const, model=model)
    print(f"\n  Equilibrium  P={100*res['eq_P']:.2f}%  Q={100*res['eq_Q']:.2f}%")
    print(f"  Hole-burnt   P={100*res['hb_P']:.2f}%  Q={100*res['hb_Q']:.2f}% (area method)")

    outdir, base = _output_dir(path)
    rows = [[hb_ts, 100 * res["hb_P"], "", 100 * res["hb_Q"], "", 100 * res["hb_P"],
             100 * res["hb_Q"]] + [""] * 0 + [res["ref"].get("omegD", "")]]
    with open(os.path.join(outdir, base + "_holeburnt.csv"), "w", newline="",
              encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["hb_timestamp", "HB_P(%)", "", "HB_Q(%)", "", "eq_P(%)", "eq_Q(%)", "omegD"])
        w.writerow([hb_ts, 100 * res["hb_P"], "", 100 * res["hb_Q"], "",
                    100 * res["eq_P"], 100 * res["eq_Q"], res["ref"]["omegD"]])
    write_flats_cache(os.path.join(outdir, base + "_HB_flats.csv"), freqs,
                      [(eq_ts, 100 * res["eq_P"], res["eq_flat"]),
                       (hb_ts, 100 * res["hb_P"], res["hb_flat"])])
    print(f"  wrote results to {outdir}")


def _report_one(ts, res):
    print(f"\n  {ts}")
    print(f"    P = {100*res['P']:.3f} +/- {100*res['P_err']:.3f} %   "
          f"Q = {100*res['Q']:.3f} +/- {100*res['Q_err']:.3f} %")
    print(f"    Area P = {100*res['area_P']:.3f} %   Area Q = {100*res['area_Q']:.3f} %")
    print(f"    reduced chi2 = {res['red_chisq']:.3f}")
    for n in res["names"]:
        v, e = res["params"][n], res["errors"][n]
        if n == "phase":
            v, e = np.degrees(v), np.degrees(e)
        print(f"    {n:7s} = {v:.6g} +/- {e:.3g}")


def _save_all(source, names, result_rows, freqs, cache_entries, provenance):
    if not result_rows:
        print("No successful fits -- nothing written.")
        return
    outdir, base = _output_dir(source)
    res_path = os.path.join(outdir, base + "_fit_results.csv")
    cache_path = os.path.join(outdir, base + "_flats_cache.csv")
    prov_path = os.path.join(outdir, base + "_provenance.json")
    write_results(res_path, names, result_rows)
    write_flats_cache(cache_path, freqs, cache_entries)
    provenance.update(source=source, n_results=len(result_rows),
                      freq_min=float(np.min(freqs)), freq_max=float(np.max(freqs)),
                      width=len(freqs))
    write_provenance(prov_path, provenance)
    print(f"\nWrote {len(result_rows)} results to:")
    print(f"  {res_path}")
    print(f"  {cache_path}   (re-usable flats cache)")
    print(f"  {prov_path}")


def main():
    print("=" * 64)
    print(" Deuteron NMR fitter")
    print("=" * 64)
    path = _ask("Path to data file")
    # Strip a stray UTF-8 BOM (real U+FEFF, or the 3-char mojibake that appears
    # when a BOM is piped through stdin) plus surrounding quotes/whitespace.
    for bom in ("﻿", "\xef\xbb\xbf"):
        if path.startswith(bom):
            path = path[len(bom):]
    path = path.strip().strip('"').strip()
    if not os.path.isfile(path):
        print(f"File not found: {path}")
        return
    kind = io.detect_input_type(path)
    if kind == "flats":
        run_flats_interactive(path)
    else:
        run_cooldown_interactive(path)


if __name__ == "__main__":
    main()
