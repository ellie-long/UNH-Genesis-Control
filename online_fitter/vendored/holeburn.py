"""Hole-burnt / non-Boltzmann deuteron analysis (Dissertation Sec. 4.4).

Out-of-equilibrium signals cannot use the ratio method (a single ``r`` no longer
describes the lineshape). This module reproduces the validated routine from
``deuts/DeuteronAnalysisComponentsSimpleXi_HB.py``, cleaned up:

  1. equilibrium_reference -- fit an equilibrium signal with the material's
     lineshape constants fixed, floating omegD, r, phase and the cubic wing.
  2. differential_binning -- with everything fixed, fit a per-frequency-bin r.
     For an equilibrium signal r is flat; for a hole-burnt signal it varies.
  3. detect_bumps -- two-pass: where the per-bin r exceeds the equilibrium r, the
     feature is a "bump" (mirror of a burnt hole on the opposite spin-flip curve)
     and is refit with the bump model (Eq. 4.13).
  4. area_polarizations -- reconstruct the component spin-flip curves bin-by-bin
     and integrate them to get P and Q via the area method (Eq. 4.11/4.12):
        C = P_eq / integral(S_eq);  P = C*(I+ + I-);  Q = C*(I+ - I-).

All polarizations out of equilibrium come from the area method; the ratio method
is only used on the equilibrium reference to set the calibration constant C.

Phase note (Sec. 4.4): area-method Q is sensitive to phase. If |phase| > ~1 deg
the components are still built at the fitted phase (the complex Dulya form), which
is correct; for best accuracy tune the signal near real before measuring.
"""

import numpy as np
from scipy.optimize import curve_fit

import deuteron_model as dm


# ---------------------------------------------------------------------------
# Step 1: equilibrium reference fit (fix material constants, float omegD/r/phase/cubic)
# ---------------------------------------------------------------------------
def equilibrium_reference(freqs, eq_signal, const, model="double",
                          r_bounds=(1.0, 3.0), phase_bounds_deg=(-180.0, 180.0)):
    """Fit an equilibrium signal to set the reference (rX, omegD, phase, cubic).

    ``const`` holds the FIXED lineshape constants:
      double -> omegQ1, omegQ2, A1, eta1, eta2, K, scale
      single -> omegQ1 (=omegQ), A1, eta1, scale
    Returns a ``ref`` dict with all constants plus fitted omegD, r, phase, and the
    cubic coefficients, the wing-subtracted equilibrium signal, and its fit.
    """
    freqs = np.asarray(freqs, dtype=float)
    eq_signal = np.asarray(eq_signal, dtype=float)
    centfreq = freqs[len(freqs) // 2]
    big = 1e5

    if model == "single":
        def f(freq, omegD, r, phase, x3, x2, x1, x0):
            return dm.model_single_cube(freq, omegD, const["omegQ1"], const["A1"],
                                        const["eta1"], r, const["scale"], phase,
                                        x3, x2, x1, x0)
    else:
        def f(freq, omegD, r, phase, x3, x2, x1, x0):
            return dm.model_double_cube(freq, omegD, const["omegQ1"], const["omegQ2"],
                                        const["A1"], const["eta1"], const["eta2"], r,
                                        const["K"], const["scale"], phase,
                                        x3, x2, x1, x0)

    lo = [centfreq - 0.5, r_bounds[0], np.radians(phase_bounds_deg[0]), -big, -big, -big, -big]
    hi = [centfreq + 0.5, r_bounds[1], np.radians(phase_bounds_deg[1]), big, big, big, big]
    p0 = [centfreq, max(r_bounds[0], min(r_bounds[1], 1.5)), 0.0, 0, 0, 0, 0]
    popt, _ = curve_fit(f, freqs, eq_signal, p0=p0, bounds=(lo, hi), maxfev=100000)

    omegD, rX, phase, x3, x2, x1, x0 = popt
    cube = dm.cubic(freqs, x3, x2, x1, x0)
    ref = dict(const)
    ref.update(model=model, omegD=omegD, r=rX, phase=phase,
               x3=x3, x2=x2, x1=x1, x0=x0)
    eq_flat = eq_signal - cube
    eq_fit = _full_model(freqs, ref, rX)
    return ref, eq_flat, eq_fit


def _full_model(freqs, ref, r):
    """Real lineshape (with Xi, no cubic) at asymmetry ``r`` using ref constants."""
    if ref["model"] == "single":
        return dm.model_single(freqs, ref["omegD"], ref["omegQ1"], ref["A1"],
                               ref["eta1"], r, ref["scale"], ref["phase"])
    return dm.model_double(freqs, ref["omegD"], ref["omegQ1"], ref["omegQ2"],
                          ref["A1"], ref["eta1"], ref["eta2"], r, ref["K"],
                          ref["scale"], ref["phase"])


# ---------------------------------------------------------------------------
# Step 2: wing-fit cubic subtraction for the hole-burnt signal
# ---------------------------------------------------------------------------
def wing_subtract(freqs, signal, wing=25):
    """Fit a cubic to the flat wings (first/last ``wing`` points) and subtract it.

    The standard in-fit cubic can distort a hole-burnt signal, so HB signals use
    a wing fit instead (Sec. 4.4 / SimpleXi_HB). Returns the flattened signal.
    """
    freqs = np.asarray(freqs, dtype=float)
    signal = np.asarray(signal, dtype=float)
    n = len(freqs)
    mask = (np.arange(n) < wing) | (np.arange(n) > n - wing)
    # The cubic is linear in its coefficients -> ordinary least squares (polyfit),
    # which is exact and instant (no iterative curve_fit).
    coeffs = np.polyfit(freqs[mask], signal[mask], 3)
    return signal - np.polyval(coeffs, freqs)


# ---------------------------------------------------------------------------
# Step 3: per-bin r (differential binning) + bump detection
# ---------------------------------------------------------------------------
def _single_r_model(ref):
    """Closure: real lineshape as a function of (freq, r) with ref constants fixed."""
    def model(freq, r):
        return _full_model(np.asarray(freq, dtype=float), ref, r)
    return model


def _bump_model(ref):
    """Closure: bump lineshape as a function of (freq, r_stc) at fixed r_eq=ref['r']."""
    r_eq = ref["r"]
    if ref["model"] == "single":
        def model(freq, r_stc):
            freq = np.asarray(freq, dtype=float)
            base = dm.model_single(freq, ref["omegD"], ref["omegQ1"], ref["A1"],
                                   ref["eta1"], r_eq, ref["scale"], ref["phase"])
            # single-bond rev model: reuse the double rev with K=0 is awkward;
            # build directly from component_signed_rev over the one bond.
            rev_eq = dm.component_signed_rev(freq, -1, ref["omegD"], ref["omegQ1"],
                                             ref["A1"], ref["eta1"], r_eq, 1.0,
                                             ref["scale"], ref["phase"]) \
                + dm.component_signed_rev(freq, 1, ref["omegD"], ref["omegQ1"],
                                          ref["A1"], ref["eta1"], r_eq, 1.0,
                                          ref["scale"], ref["phase"])
            rev_stc = dm.component_signed_rev(freq, -1, ref["omegD"], ref["omegQ1"],
                                              ref["A1"], ref["eta1"], r_stc, 1.0,
                                              ref["scale"], ref["phase"]) \
                + dm.component_signed_rev(freq, 1, ref["omegD"], ref["omegQ1"],
                                          ref["A1"], ref["eta1"], r_stc, 1.0,
                                          ref["scale"], ref["phase"])
            return base + 0.5 * rev_eq - 0.5 * rev_stc
    else:
        def model(freq, r_stc):
            freq = np.asarray(freq, dtype=float)
            base = dm.model_double(freq, ref["omegD"], ref["omegQ1"], ref["omegQ2"],
                                   ref["A1"], ref["eta1"], ref["eta2"], r_eq,
                                   ref["K"], ref["scale"], ref["phase"])
            rev_eq = dm.model_double_rev(freq, ref["omegD"], ref["omegQ1"], ref["omegQ2"],
                                         ref["A1"], ref["eta1"], ref["eta2"], r_eq,
                                         ref["K"], ref["scale"], ref["phase"])
            rev_stc = dm.model_double_rev(freq, ref["omegD"], ref["omegQ1"], ref["omegQ2"],
                                          ref["A1"], ref["eta1"], ref["eta2"], r_stc,
                                          ref["K"], ref["scale"], ref["phase"])
            return base + 0.5 * rev_eq - 0.5 * rev_stc
    return model


def differential_binning(freqs, hb_flat, ref, r_max=3.5, bump_max=3.0):
    """Per-bin asymmetry r across a hole-burnt signal (Sec. 4.4 / SimpleXi_HB).

    For each interior bin i, fit r using three points -- the (near-zero) first and
    last bins and bin i -- so r is whatever a reconstructed equilibrium signal
    would need at that frequency. Then two-pass bump handling: where r > r_eq,
    refit that bin with the bump model. Endpoints are pinned to r_eq.

    Returns (rZ, r_stc, is_bump) arrays of length len(freqs).
    """
    freqs = np.asarray(freqs, dtype=float)
    hb_flat = np.asarray(hb_flat, dtype=float)
    n = len(freqs)
    r_eq = ref["r"]
    line_model = _single_r_model(ref)
    bump_model = _bump_model(ref)

    rZ = np.full(n, r_eq, dtype=float)
    r_stc = np.full(n, r_eq, dtype=float)
    is_bump = np.zeros(n, dtype=bool)

    f_ends = np.array([freqs[0], freqs[n - 1]])
    for i in range(1, n - 1):
        three_f = np.array([freqs[0], freqs[i], freqs[n - 1]])
        three_d = np.array([hb_flat[0], hb_flat[i], hb_flat[n - 1]])
        try:
            popt, _ = curve_fit(line_model, three_f, three_d, p0=[r_eq],
                                bounds=([1.0], [r_max]), maxfev=10000)
            rZ[i] = popt[0]
        except RuntimeError:
            rZ[i] = r_eq
        if rZ[i] > r_eq:           # pass 2: this bin is a bump
            is_bump[i] = True
            try:
                popt, _ = curve_fit(bump_model, three_f, three_d, p0=[r_eq],
                                    bounds=([1.0], [bump_max]), maxfev=10000)
                r_stc[i] = popt[0]
            except RuntimeError:
                r_stc[i] = rZ[i]
        else:
            r_stc[i] = rZ[i]
    return rZ, r_stc, is_bump


def differential_binning_fast(freqs, hb_flat, ref, fm=None, r_max=3.5, n_grid=800):
    """Vectorized per-bin r via r-grid inversion (matches differential_binning).

    Instead of a 1-parameter curve_fit at every bin (~N fits), precompute the
    equilibrium lineshape on a fine grid of r and, for each bin, pick the r whose
    model value is closest to the data. ~100-1000x faster; used for whole-cooldown
    hole-burnt fitting. Bump bins (r > r_eq) are inverted against the bump model.
    """
    freqs = np.asarray(freqs, dtype=float)
    hb_flat = np.asarray(hb_flat, dtype=float)
    n = len(freqs)
    r_eq = ref["r"]
    scale, phase = ref["scale"], ref["phase"]
    if fm is None:
        fm = dm.FastModel(freqs, ref, model=ref["model"], with_cube=False)

    rg = np.linspace(1.0, r_max, n_grid)
    # Table of the equilibrium lineshape over the r-grid: shape (n_grid, n).
    line_tbl = np.array([fm.real(r, scale, phase) for r in rg])
    kbest = np.argmin(np.abs(line_tbl - hb_flat[None, :]), axis=0)
    rZ = rg[kbest]
    rZ[0] = rZ[-1] = r_eq                      # pin the (near-zero) end bins

    is_bump = rZ > r_eq
    r_stc = rZ.copy()
    if is_bump.any():
        rgb = np.linspace(1.0, r_max, n_grid)
        bump_tbl = np.array([fm.bump(r_eq, rs, scale, phase) for rs in rgb])
        kb = np.argmin(np.abs(bump_tbl - hb_flat[None, :]), axis=0)
        r_stc = np.where(is_bump, rgb[kb], rZ)
    return rZ, r_stc, is_bump


# ---------------------------------------------------------------------------
# Step 4: reconstruct components bin-by-bin and apply the area method
# ---------------------------------------------------------------------------
def area_polarizations(freqs, eq_flat, ref, rZ, r_stc, is_bump):
    """Area-method P and Q for a hole-burnt signal (Eq. 4.11/4.12).

    Reconstructs each component spin-flip curve bin-by-bin: in non-bump bins the
    component uses the per-bin r (spin-temperature consistency); in bump bins it
    uses the bump model. Integrates the four components and combines:
        C    = ratio_P(r_eq) / integral(eq_flat)
        P_HB = C*(I+ + I-),  Q_HB = C*(I+ - I-)

    Returns a dict with P, Q (hole-burnt, area method), the equilibrium area Q,
    the calibration C, and the reconstructed full HB fit + component curves.
    """
    freqs = np.asarray(freqs, dtype=float)
    n = len(freqs)
    bond_list = dm.bonds(ref["model"], ref["omegQ1"], ref.get("omegQ2", 0.0),
                         ref["A1"], ref["eta1"], ref.get("eta2", 0.0),
                         ref.get("K", 0.0))

    # Equilibrium components (constant r_eq) and hole-burnt components (per bin).
    eq_comp = {}
    hb_comp = {}
    for omegQ, A, eta, k, label in bond_list:
        for epsi in (-1, 1):
            key = (label, epsi)
            eq_comp[key] = dm.component_signed(freqs, epsi, ref["omegD"], omegQ, A,
                                               eta, ref["r"], k, ref["scale"], ref["phase"])
            # Per-bin reconstruction: vectorized normal part + bump corrections.
            comp = dm.component_signed(freqs, epsi, ref["omegD"], omegQ, A, eta,
                                       rZ, k, ref["scale"], ref["phase"])
            if is_bump.any():
                bump_vals = dm.bump_component(freqs, epsi, ref["omegD"], omegQ, A, eta,
                                              ref["r"], r_stc, k, ref["scale"], ref["phase"])
                comp = np.where(is_bump, bump_vals, comp)
            hb_comp[key] = comp

    cc = dm.ratio_P(ref["r"]) / dm.riemann_sum(freqs, eq_flat)

    def integ(comp):
        return dm.riemann_sum(freqs, comp)

    I_plus = sum(integ(hb_comp[(lbl, 1)]) for _, _, _, _, lbl in bond_list)
    I_minus = sum(integ(hb_comp[(lbl, -1)]) for _, _, _, _, lbl in bond_list)
    E_plus = sum(integ(eq_comp[(lbl, 1)]) for _, _, _, _, lbl in bond_list)
    E_minus = sum(integ(eq_comp[(lbl, -1)]) for _, _, _, _, lbl in bond_list)

    hb_fit = sum(hb_comp.values())
    return dict(
        P=cc * (I_plus + I_minus),
        Q=cc * (I_plus - I_minus),
        eq_area_Q=cc * (E_plus - E_minus),
        CC=cc,
        hb_fit=np.asarray(hb_fit, dtype=float),
        components=hb_comp,
    )


# ---------------------------------------------------------------------------
# Solver: reuse one equilibrium reference + precomputed grids across many HB lines
# ---------------------------------------------------------------------------
class HoleBurnSolver:
    """Fast repeated hole-burnt fitting against a fixed equilibrium reference.

    Precomputes the FastModel, the equilibrium calibration, and the r-grid tables
    once; then ``solve(hb_signal)`` returns area-method P/Q for each line in ~ms.
    Use one solver per (segment / phase group) of a cooldown.
    """

    def __init__(self, freqs, ref, eq_flat, wing=25, r_max=3.5, n_grid=800):
        self.freqs = np.asarray(freqs, dtype=float)
        self.ref = ref
        self.wing = wing
        self.scale, self.phase = ref["scale"], ref["phase"]
        self.r_eq = ref["r"]
        self.fm = dm.FastModel(self.freqs, ref, model=ref["model"], with_cube=False)
        self.cc = dm.ratio_P(self.r_eq) / dm.riemann_sum(self.freqs, eq_flat)
        self.rg = np.linspace(1.0, r_max, n_grid)
        self.line_tbl = np.array([self.fm.real(r, self.scale, self.phase) for r in self.rg])
        self.bump_tbl = np.array([self.fm.bump(self.r_eq, rs, self.scale, self.phase)
                                  for rs in self.rg])
        self._bonds = dm.bonds(ref["model"], ref["omegQ1"], ref.get("omegQ2", 0.0),
                               ref["A1"], ref["eta1"], ref.get("eta2", 0.0), ref.get("K", 0.0))

    def _invert(self, table, target):
        return self.rg[np.argmin(np.abs(table - target[None, :]), axis=0)]

    def solve(self, hb_signal):
        """Return dict(P, Q, rZ, r_stc, is_bump, hb_flat, hb_fit) for one HB line."""
        hb_flat = wing_subtract(self.freqs, hb_signal, self.wing)
        rZ = self._invert(self.line_tbl, hb_flat)
        rZ[0] = rZ[-1] = self.r_eq
        is_bump = rZ > self.r_eq
        r_stc = np.where(is_bump, self._invert(self.bump_tbl, hb_flat), rZ)

        normal = self.fm.components(rZ, self.scale, self.phase)
        if is_bump.any():
            bumped = self.fm.bump_components(self.r_eq, r_stc, self.scale, self.phase)
            comps = {k: np.where(is_bump, bumped[k], normal[k]) for k in normal}
        else:
            comps = normal
        I_plus = sum(dm.riemann_sum(self.freqs, comps[(lbl, 1)]) for *_, lbl in self._bonds)
        I_minus = sum(dm.riemann_sum(self.freqs, comps[(lbl, -1)]) for *_, lbl in self._bonds)
        hb_fit = sum(comps.values())
        return dict(P=self.cc * (I_plus + I_minus), Q=self.cc * (I_plus - I_minus),
                    rZ=rZ, r_stc=r_stc, is_bump=is_bump, hb_flat=hb_flat, hb_fit=hb_fit)


# ---------------------------------------------------------------------------
# Top-level convenience
# ---------------------------------------------------------------------------
def run(freqs, eq_signal, hb_signal, const, model="double", wing=25,
        r_bounds=(1.0, 3.0), phase_bounds_deg=(-180.0, 180.0), fast=True,
        ref=None, eq_flat=None, fm=None):
    """Full hole-burnt analysis from baseline-subtracted eq and HB signals.

    ``fast`` uses the vectorized r-grid inversion (default) rather than a
    per-bin curve_fit -- ~100-1000x faster with effectively identical P/Q.
    A precomputed equilibrium reference (``ref``, ``eq_flat``) and ``fm``
    (FastModel) can be supplied to reuse across many HB lines in a cooldown.

    Returns a result dict: equilibrium ratio-method P/Q, hole-burnt area-method
    P/Q, the reference parameters, per-bin r, and the reconstructed HB fit.
    """
    freqs = np.asarray(freqs, dtype=float)
    if ref is None:
        ref, eq_flat, _eq_fit = equilibrium_reference(
            freqs, eq_signal, const, model, r_bounds, phase_bounds_deg)
    eq_fit = _full_model(freqs, ref, ref["r"])
    hb_flat = wing_subtract(freqs, hb_signal, wing)
    if fast:
        if fm is None:
            fm = dm.FastModel(freqs, ref, model=ref["model"], with_cube=False)
        rZ, r_stc, is_bump = differential_binning_fast(freqs, hb_flat, ref, fm=fm)
    else:
        rZ, r_stc, is_bump = differential_binning(freqs, hb_flat, ref)
    area = area_polarizations(freqs, eq_flat, ref, rZ, r_stc, is_bump)

    return dict(
        ref=ref,
        eq_P=dm.ratio_P(ref["r"]),
        eq_Q=dm.ratio_Q(ref["r"]),
        eq_flat=eq_flat,
        eq_fit=eq_fit,
        hb_flat=hb_flat,
        hb_P=area["P"],
        hb_Q=area["Q"],
        hb_area_eq_Q=area["eq_area_Q"],
        hb_fit=area["hb_fit"],
        components=area["components"],          # {(label, epsi): curve} -- C/O bonds, +/- branches
        rZ=rZ,
        r_stc=r_stc,
        is_bump=is_bump,
        CC=area["CC"],
    )
