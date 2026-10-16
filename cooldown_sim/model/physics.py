"""Pure physics functions for the simulated polarized target.

Units: frequencies in MHz unless named ``_GHz``, fields in T, temperatures in
K, times in s, polarizations as FRACTIONS (not percent) unless named ``_pct``.
Gyromagnetic ratios are gamma/(2 pi) in MHz/T.

P (vector) and Q (tensor) are separate state variables throughout.  The only
place they are tied is :func:`q_boltzmann`, the single-spin-temperature
relation, used as the relaxation *target* of Q -- that assumption is named
wherever it is used.
"""

from __future__ import annotations

import math

import numpy as np

#: deuteron gamma/(2 pi) [MHz/T]  (CODATA 2018: 6.535 903 65 MHz/T)
GAMMA_D_MHZ_PER_T = 6.53590365
#: free-electron g_e mu_B / h [GHz/T]  (g = 2.002 319; 28.024 951 4 GHz/T)
GAMMA_E_GHZ_PER_T = 28.0249514
#: h / k_B [K/MHz]  (6.626 070 15e-34 J s / 1.380 649e-23 J/K = 4.799 243e-5 K/MHz)
H_OVER_K_K_PER_MHZ = 4.799243073e-5


def deuteron_larmor_MHz(B_T: float) -> float:
    """Deuteron Larmor frequency [MHz] at field ``B_T`` [T]."""
    return GAMMA_D_MHZ_PER_T * B_T


def electron_larmor_GHz(B_T: float) -> float:
    """Free-electron EPR frequency [GHz] at field ``B_T`` [T] (g = 2.0023)."""
    return GAMMA_E_GHZ_PER_T * B_T


def te_polarization(B_T: float, T_K: float) -> tuple[float, float]:
    """Thermal-equilibrium spin-1 polarizations (P, Q) at field B [T], lattice T [K].

    Boltzmann populations p_m ~ exp(m y), m = -1, 0, +1, with
    y = h nu_L / (k_B T) (Zeeman only; the quadrupole splitting is << k_B T):
        P = p_+ - p_- = 2 sinh y / (1 + 2 cosh y)
        Q = 1 - 3 p_0 = (2 cosh y - 2) / (1 + 2 cosh y)
    Returns fractions.  E.g. 5 T, 1 K -> P ~ 1.1e-3.
    """
    if not (T_K > 0 and math.isfinite(B_T)):
        return float("nan"), float("nan")
    y = H_OVER_K_K_PER_MHZ * deuteron_larmor_MHz(B_T) / T_K
    y = max(min(y, 700.0), -700.0)          # cosh overflows past ~710 (T below ~2 uK at 5 T)
    d = 1.0 + 2.0 * math.cosh(y)
    return 2.0 * math.sinh(y) / d, (2.0 * math.cosh(y) - 2.0) / d


def q_boltzmann(P: float) -> float:
    """Tensor polarization of a spin-1 system at ONE spin temperature with vector
    polarization P (fractions):  Q = 2 - sqrt(4 - 3 P^2).
    [Same relation as deuteron_model.Q_equilibrium; single-temperature ASSUMPTION.]
    |P| is clipped to 1 so an unphysical input cannot produce Q > 1."""
    P = max(min(P, 1.0), -1.0)
    return 2.0 - math.sqrt(max(4.0 - 3.0 * P * P, 0.0))


def project_physical(P: float, Q: float) -> tuple[float, float, bool]:
    """Clip (P, Q) into the spin-1 region where all populations lie in [0, 1]:
    |P| <= 1 and 3|P| - 2 <= Q <= 1.  Returns (P, Q, clamped)."""
    P2 = max(min(P, 1.0), -1.0)
    Q2 = max(min(Q, 1.0), 3.0 * abs(P2) - 2.0)
    return P2, Q2, (P2 != P or Q2 != Q)


def populations(P: float, Q: float) -> tuple[float, float, float]:
    """(p_-1, p_0, p_+1) from (P, Q):  p_0 = (1 - Q)/3,  p_+-1 = ((2 + Q)/3 +- P)/2."""
    p0 = (1.0 - Q) / 3.0
    s = (2.0 + Q) / 3.0
    return (s - P) / 2.0, p0, (s + P) / 2.0


def asymmetry_ratios(P: float, Q: float) -> tuple[float, float]:
    """(rN, rP) = (p_0/p_-1, p_+1/p_0): the two-transition intensity ratios used by
    deuteron_model.model_double_nonbolt.  Inverse of ratio_P/Q_nonbolt."""
    pm, p0, pp = populations(P, Q)
    eps = 1e-12
    return p0 / max(pm, eps), pp / max(p0, eps)


def physical(P: float, Q: float) -> bool:
    """All three populations in [0, 1]."""
    return all(-1e-9 <= p <= 1 + 1e-9 for p in populations(P, Q))


def dnp_lobe(delta_GHz: float, width_GHz: float, neg_scale: float = 1.0, neg_side: int = 1) -> float:
    """Normalised DNP enhancement vs microwave detuning from the positive optimum.

    Derivative-of-Gaussian with its positive extremum (+1) at delta = 0 and its
    negative extremum (-1) at delta = +2 w (the high-frequency side):
        g(x) = -(x/w) exp(1/2 - x^2/(2 w^2)),  x = delta - w.
    ASSUMED shape.  Calibrated: the location of the positive optimum (the
    free-electron frequency) and the sign below it -- the record shows only
    small POSITIVE P a few hundred MHz below the optimum (138.0 GHz at
    4.94 T: +2 % to +11 %, 2025-11 and 2026-06), never a negative lobe there.
    The negative lobe's side and the width are not resolved by the record.

    Per cool-down (fitted, ``cooldown_dnp.yaml``): ``neg_side`` -1 puts the
    negative lobe on the low-frequency side instead, and ``neg_scale`` sets its
    depth relative to the positive lobe (materials and radicals differ).
    """
    if not math.isfinite(delta_GHz) or width_GHz <= 0:
        return 0.0
    x = (delta_GHz if neg_side >= 0 else -delta_GHz) - width_GHz
    g = -(x / width_GHz) * math.exp(0.5 - x * x / (2.0 * width_GHz * width_GHz))
    return g * neg_scale if g < 0 else g


# ---------------------------------------------------- He-4 vapor pressure (T90)
# The lab's reference conversion (Ells's offline t90_torr_to_k; trusted over
# the LabVIEW DAQ's online readout, whose "Lo" range reads ~1.6 mK colder):
#   1.25-5.0 K: ITS-90, H. Preston-Thomas, Metrologia 27, 3 (1990), p. 5;
#   below 1.25 K: R. J. Donnelly & C. F. Barenghi, J. Phys. Chem. Ref. Data
#   27, 1217 (1998), ln(10 P/Pa) = i0 + 5/2 ln T - L0/(R T) (their equation
#   gives 10x the pressure, hence the factor of 10).
#: Pa per Torr as the lab's code uses it
HE4_PA_PER_TORR = 133.3
_ITS90_HIGH = ((3.146631, 1.357655, 0.413923, 0.091159, 0.016349, 0.001826,
                -0.004325, -0.004973), 10.3, 1.9)          # 2.1768-5.0 K
_ITS90_LOW = ((1.392408, 0.527153, 0.166756, 0.050988, 0.026514, 0.001975,
               -0.017976, 0.005409, 0.013259), 5.6, 2.9)   # 1.25-2.1768 K
_HE4_LAMBDA_PA = 5042.3
_HE4_1P25K_PA = 114.7
_HE4_MAX_PA = 1.960e5
_DB_I0, _DB_L0, _DB_R = 12.2440, 59.83, 8.314510


def _its90(A, B, C, p_pa):
    x = (math.log(p_pa) - B) / C
    return sum(a * x ** i for i, a in enumerate(A))


def _donnelly_ln_p10(T_K):
    return _DB_I0 + 2.5 * math.log(T_K) - _DB_L0 / (_DB_R * T_K)


def he4_t90_K(p_torr: float) -> float:
    """T90 (K) of saturated liquid He-4 at vapor pressure p (Torr); nan for
    p <= 0 or non-finite (a manometer at or below zero is no reading) and
    above ~5 K (1.96e5 Pa, where the lab's code returns a 20 K flag)."""
    if not (math.isfinite(p_torr) and p_torr > 0):
        return float("nan")
    p = p_torr * HE4_PA_PER_TORR
    if p > _HE4_MAX_PA:
        return float("nan")
    if p > _HE4_LAMBDA_PA:
        return _its90(*_ITS90_HIGH, p)
    if p >= _HE4_1P25K_PA:
        return _its90(*_ITS90_LOW, p)
    # Donnelly-Barenghi, inverted by bisection (monotonic in T; the lab's code
    # uses the Lambert-W closed form, which gives the same root)
    target = math.log(10.0 * p)
    lo, hi = 0.2, 1.3
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _donnelly_ln_p10(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def he4_p90_torr(T_K: float) -> float:
    """Vapor pressure (Torr) of He-4 at T90 = T_K: the inverse of he4_t90_K."""
    if not (math.isfinite(T_K) and 0.2 < T_K < 5.0):
        return float("nan")
    if T_K < he4_t90_K(_HE4_1P25K_PA / HE4_PA_PER_TORR):
        return math.exp(_donnelly_ln_p10(T_K)) / 10.0 / HE4_PA_PER_TORR
    lo, hi = math.log(_HE4_1P25K_PA), math.log(_HE4_MAX_PA)   # ln P, bisection
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if he4_t90_K(math.exp(mid) / HE4_PA_PER_TORR) < T_K:
            lo = mid
        else:
            hi = mid
    return math.exp(0.5 * (lo + hi)) / HE4_PA_PER_TORR


def he4_vapor_pressure_ratio(T_ref_K: float, T_K: float, b_K: float = 10.6) -> float:
    """P_vap(T) / P_vap(T_ref) for liquid He-4 near 1-2 K, Clausius-Clapeyron form
    ln P = a - b/T with b ~ 10.6 K (fitted to ITS-90 values P(1.0 K)=0.120 Torr,
    P(2.0 K)=23.8 Torr; within ~1 % at those points, ~14 % high at 1.5 K: the
    effective b is 10.2 K over 1.0-1.5 K and 11.3 K over 1.5-2.0 K).  Used only
    to scale a RECORDED pressure by a modelled temperature deviation; the ratio
    error grows with the deviation (~2 % for dT <= 0.05 K, tens of % for
    dT ~ 0.4 K) -- adequate for rehearsal, not for physics."""
    if not (T_ref_K > 0 and T_K > 0):
        return 1.0
    return math.exp(b_K * (1.0 / T_ref_K - 1.0 / T_K))
