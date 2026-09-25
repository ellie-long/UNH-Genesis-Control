"""Signal-driven classification of NMR lines.

The NMR signal itself is the primary indicator of what a line is; instrument
metadata (NMR Status, Holeburning Power, Phase Tune) only adjusts the thresholds.
These are pure functions over arrays so they're easy to test and tune.

Diagnostics, computed on the wing-flattened (baseline + wing-cubic subtracted)
signal and on the residual of an equilibrium fit:

* SNR        -- amplitude / wing-noise; below a floor there is no real signal.
* hole_score -- the largest *localized*, coherent residual (a burnt hole leaves a
                narrow dip + a mirror bump that survive smoothing; white noise
                averages away). High -> hole-burnt.
* asym_score -- a *broad* left/right residual imbalance the single-r equilibrium
                shape can't produce. High (without a localized hole) -> frequency
                hopping (the two component curves are polarized differently).

`classify(...)` turns the scores (plus metadata priors) into a label:
no_signal / equilibrium / hole_burnt / freq_hopping.
"""

import numpy as np


def wing_flatten(freqs, signal, wing=25):
    """Subtract a cubic fit to the flat wings (first/last `wing` points)."""
    freqs = np.asarray(freqs, dtype=float)
    signal = np.asarray(signal, dtype=float)
    n = len(freqs)
    mask = (np.arange(n) < wing) | (np.arange(n) > n - wing)
    coeffs = np.polyfit(freqs[mask], signal[mask], 3)
    return signal - np.polyval(coeffs, freqs)


def wing_noise(signal, wing=25):
    """Robust noise estimate from the (signal-free) wings."""
    signal = np.asarray(signal, dtype=float)
    edges = np.concatenate([signal[:wing], signal[-wing:]])
    # MAD-based, robust to the occasional spike.
    return 1.4826 * np.median(np.abs(edges - np.median(edges))) or np.std(edges) or 1e-12


def amplitude(flat):
    """Robust peak-to-peak of the flattened signal (5th-95th percentile span)."""
    flat = np.asarray(flat, dtype=float)
    return float(np.percentile(flat, 99) - np.percentile(flat, 1))


def _smooth(resid, window):
    if window > 1:
        return np.convolve(resid, np.ones(window) / window, mode="same")
    return resid


def hole_score(resid, fit, window=15):
    """Localized dip of the data *below* the equilibrium fit, as a fraction of the
    fit's peak. Scale-invariant: a clean equilibrium fit scores ~a few percent
    regardless of polarization; a burnt hole removes a chunk of a peak, so the
    smoothed residual dips to a large negative fraction of the peak.

    Returns max(-smoothed_residual) / peak_fit (>=0); ~0.03-0.08 for clean
    equilibrium, much larger where a hole has depleted the signal.
    """
    resid = np.asarray(resid, dtype=float)
    peak = float(np.max(np.abs(np.asarray(fit, dtype=float)))) or 1e-12
    smooth = _smooth(resid, window)
    return float(max(0.0, -np.min(smooth)) / peak)


def hole_bump(freqs, resid, fit, omegD, window=15, half=20):
    """Physical hole signature: a dip plus a mirror BUMP of ~half its area on the
    opposite side of the Larmor frequency omegD (Dissertation Fig 4.23).

    Returns (depth_frac, bump_ratio):
      depth_frac = hole depth / peak fit (same as hole_score).
      bump_ratio = (positive residual area near the mirror frequency) /
                   (negative residual area at the hole). A real burnt hole gives
                   ~0.5; an artifact (a "King", a fit wiggle) has no matching
                   mirror bump and gives ~0.
    """
    freqs = np.asarray(freqs, dtype=float)
    resid = np.asarray(resid, dtype=float)
    peak = float(np.max(np.abs(np.asarray(fit, dtype=float)))) or 1e-12
    smooth = _smooth(resid, window)
    n = len(freqs)
    step = (freqs[-1] - freqs[0]) / n

    ih = int(np.argmin(smooth))                 # hole = deepest dip
    depth = -smooth[ih]
    if depth <= 0:
        return 0.0, 0.0
    ib = int(np.argmin(np.abs(freqs - (2.0 * omegD - freqs[ih]))))   # mirror bin

    def _slice(c):
        return slice(max(0, c - half), min(n, c + half + 1))

    hole_area = -np.minimum(smooth[_slice(ih)], 0.0).sum() * step    # negative part
    bump_area = np.maximum(smooth[_slice(ib)], 0.0).sum() * step     # positive part
    bump_ratio = float(bump_area / hole_area) if hole_area > 0 else 0.0
    return float(depth / peak), bump_ratio


def body_excursion(dev, dmdr0, freqs, omegD, half_width, frac=0.2, window=11):
    """Largest smoothed excursion of a per-bin deviation, evaluated ONLY inside the
    signal body: |f - omegD| <= half_width AND where the model is sensitive
    (|dmodel/dr| significant), so wing noise / plateaus cannot fake a feature.

    ``half_width`` should use the PRIMARY quadrupole frequency (the dominant bond):
    a secondary bond with a larger omegQ but a tiny amplitude only adds far-wing
    bins where the per-bin polarization is ill-determined and produces edge
    artifacts."""
    dev = np.asarray(dev, dtype=float)
    freqs = np.asarray(freqs, dtype=float)
    peak = float(np.max(np.abs(dmdr0))) or 1e-12
    body = (np.abs(dmdr0) >= frac * peak) & (np.abs(freqs - omegD) <= half_width)
    masked = np.where(body, dev, 0.0)
    smooth = np.convolve(masked, np.ones(window) / window, mode="same")
    return float(np.max(np.abs(np.where(body, smooth, 0.0))))


def asymmetry_score(freqs, resid, fit, omegD):
    """Broad left/right residual-area imbalance, as a fraction of the total fit area.

    Frequency hopping scales the two spin-flip curves differently, so a single-r
    equilibrium fit leaves net residual area on one side. Scale-invariant.
    """
    freqs = np.asarray(freqs, dtype=float)
    resid = np.asarray(resid, dtype=float)
    fit = np.asarray(fit, dtype=float)
    left = resid[freqs < omegD].sum()
    right = resid[freqs >= omegD].sum()
    total = np.abs(fit).sum() or 1e-12
    return float(abs(left - right) / total)


# Calibrated on known-equilibrium (Dec-12 spin-up) vs known-hole-burnt
# (ExampleCSV_HB1): clean equilibrium scores hole~0.01-0.02 / asym~0.001; a burnt
# hole scores hole~0.2 / asym~0.05. The shape metrics are only reliable once the
# signal is well-formed (high SNR); near-zero-polarization lines have almost no
# signal so their residual-to-peak ratio is meaningless -> treat as equilibrium.
def pspread_threshold(eq_P, frac=0.06, floor=0.012):
    """Hole-detection threshold on the per-bin P-spread, scaled to the line's
    polarization. A hole depletes a FRACTION of the available polarization, so the
    threshold tracks ``frac * |P|`` (more sensitive to small burns at low P than a
    flat absolute cut), but never drops below ``floor`` -- the per-bin P estimate
    has a noise floor that does not vanish at low P, and a sub-floor threshold
    would flag noise."""
    P = abs(eq_P) if eq_P is not None else 0.5
    return max(floor, frac * P)


def classify(snr, hole_depth, bump_ratio, asym_score, pspread=0.0, *, eq_P=None,
             hb_power=None, snr_min=4.0, snr_reliable=80.0, p_min=0.04,
             pspread_frac=0.06, pspread_floor=0.012, hole_min=0.10,
             asym_min=0.02, hb_power_on=-20.0):
    """Label a line from its diagnostic scores and (optional) metadata priors.

    The diagnostics are only meaningful for a well-formed, polarized signal:
      * below snr_min there is no signal;
      * a weak signal (low SNR) or a near-zero polarization (|P| < p_min) has
        nothing to burn a hole into, so it is equilibrium (this is the regime where
        the "Kings" background -- ~3 evenly-spaced external peaks at TE/low-P --
        could otherwise masquerade as a hole).

    The PRIMARY hole signature is ``pspread`` -- the per-frequency-bin vector
    polarization varies where a hole has been burnt but is constant for an
    equilibrium signal. Polarization (not r) is used because P depletion is the
    physical effect of a hole; r would not work, since dP/dr changes ~5x over the
    P range. The threshold is scaled to the line's |P| (see pspread_threshold) so
    it tracks the depletion fraction -- catching small burns at low P -- with an
    absolute floor against the noise. It is robust because a single-r fit can
    absorb a hole by shifting r, weakening residual-based tests; a strong localized
    residual dip (hole_depth) is a backup trigger. The classifier favors recall;
    the ratio-vs-area cross-check in the output lets a user reject false positives.
    """
    if snr < snr_min:
        return "no_signal"
    if snr < snr_reliable or (eq_P is not None and abs(eq_P) < p_min):
        return "equilibrium"
    if pspread >= pspread_threshold(eq_P, pspread_frac, pspread_floor) or hole_depth >= hole_min:
        return "hole_burnt"
    if asym_score >= asym_min:
        return "freq_hopping"
    return "equilibrium"
