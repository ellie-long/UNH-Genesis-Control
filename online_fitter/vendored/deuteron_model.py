"""Deuteron NMR lineshape model (vectorized, cleaned).

A faithful, NumPy-vectorized port of the physics in ``deuts/deuts.py``. The
lineshape is the Dulya spin-1 deuteron NMR signal: two phi-averaged spin-flip
curves (m=-1<->0 and m=0<->+1) combined via the asymmetry parameter ``r``,
scaled by the Xi(r) correction, with the absorptive and dispersive parts mixed
by a phase angle, plus an optional cubic "wing" term.

Physics reference: documentation/ComplexDeut.tex and documentation/Dissertation.pdf
(Dulya, "A line-shape analysis for spin-1 NMR signals").

Every public function here reproduces its ``deuts.py`` counterpart numerically
(verified by tests/test_regression.py). The only change is vectorization (the
phi-average inner loop is done with array broadcasting instead of a Python loop)
and the removal of dead/experimental variants.

IMPORTANT preserved quirk (do NOT "fix" without re-validating against prior
results, e.g. the published SpinUpFlats P column):
  * The phi sum has J=64 terms (j=0..63) but is divided by (J+1)=65. This
    normalization offset is absorbed into ``scale``/Xi and must be kept.

Corrected bug: the original deuts.py had a typo in the epsi==+1 intensity
weighting -- ``r**(1 - 3*theta*r)`` with a lowercase ``r`` where the documented
Dulya equation uses the frequency variable ``R``. This has been corrected to
``R`` here AND in deuts.py (theta ~ 6.6e-4 makes the effect small but nonzero
and frequency-dependent). The regression test validates against the corrected
deuts.py.
"""

import numpy as np

# Number of phi samples in the powder-pattern average (0 -> pi/2). Kept at 64,
# and the average is divided by (J + 1) to match deuts.py exactly.
_J = 64

# Xi(r) quartic-correction coefficients (the values used on the fit path in
# deuts.Xi -- NOT the slightly different deut_double_Q variant).
_XI_A = -0.38616
_XI_B = -0.60995


# ---------------------------------------------------------------------------
# Single spin-flip curves (one quadrupole angle phi)
# ---------------------------------------------------------------------------
def _phi_grid():
    """Column vector of phi samples, shape (J, 1), for broadcasting over freq."""
    return (np.pi / 2.0) * np.arange(_J) / _J


def _half_absorptive(freq, epsi, omegD, omegQ, A, eta, phi):
    """Absorptive single spin-flip curve (deuts.deut_half), Dulya Eq. 14.

    ``freq`` is shape (nfreq,), ``phi`` is shape (J, 1); result is (J, nfreq).
    """
    R = (freq - omegD) / (3.0 * omegQ)
    base = 1.0 - epsi * R - eta * np.cos(2.0 * phi)
    rhosq = np.sqrt(A ** 2 + base ** 2)           # note: "rhosq" is sqrt already
    rho = np.sqrt(rhosq)                          # (matches deuts.py naming)
    alpha = np.arccos(base / rhosq)
    Y = np.sqrt(3.0 - eta * np.cos(2.0 * phi))
    a2 = alpha / 2.0
    left = 2.0 * np.cos(a2) * (
        np.arctan((Y ** 2 - rhosq) / (2.0 * Y * rho * np.sin(a2))) + np.pi / 2.0)
    num = Y ** 2 + rhosq + 2.0 * Y * rho * np.cos(a2)
    den = Y ** 2 + rhosq - 2.0 * Y * rho * np.cos(a2)
    right = np.sin(a2) * np.log(num / den)
    return (1.0 / (2.0 * np.pi * rho)) * (left + right)


def _half_dispersive(freq, epsi, omegD, omegQ, A, eta, phi):
    """Dispersive single spin-flip curve (deuts.deut_disp_half).

    Matches deuts.py exactly: the arctan denominator uses sin(alpha/2) (as in
    the absorptive), and the log term is cos(alpha/2)*log(den/num).
    """
    R = (freq - omegD) / (3.0 * omegQ)
    base = 1.0 - epsi * R - eta * np.cos(2.0 * phi)
    rhosq = np.sqrt(A ** 2 + base ** 2)
    rho = np.sqrt(rhosq)
    alpha = np.arccos(base / rhosq)
    Y = np.sqrt(3.0 - eta * np.cos(2.0 * phi))
    a2 = alpha / 2.0
    left = 2.0 * np.sin(a2) * (
        np.arctan((Y ** 2 - rhosq) / (2.0 * Y * rho * np.sin(a2))) + np.pi / 2.0)
    num = Y ** 2 + rhosq + 2.0 * Y * rho * np.cos(a2)
    den = Y ** 2 + rhosq - 2.0 * Y * rho * np.cos(a2)
    right = np.cos(a2) * np.log(den / num)
    return (1.0 / (2.0 * np.pi * rho)) * (left + right)


# ---------------------------------------------------------------------------
# phi-averaged spin-flip curves
# ---------------------------------------------------------------------------
def _phi_average(freq, epsi, omegD, omegQ, A, eta, half_fn, phi):
    """Powder-pattern average of a half-curve over the supplied phi samples."""
    freq = np.asarray(freq, dtype=float)
    num = np.sqrt(3.0) * half_fn(freq, epsi, omegD, omegQ, A, eta, phi)
    den = np.sqrt(3.0 - eta * np.cos(2.0 * phi))
    avgtot = np.sum(num / den, axis=0)
    return avgtot / (_J + 1)


def _weight(aver, epsi, R, theta, r, dispersive, reverse):
    """Apply the r-dependent intensity weighting (Dulya 'Absorptive' eq).

    ``reverse`` swaps which spin-flip sign gets which weight (the deut_*_rev
    behaviour, used by the bump projection). ``dispersive`` adds the extra -1
    on one branch that the dispersive function carries.
    """
    pos_branch = (r ** 2 - r ** (1 - 3 * theta * R)) / (r ** (1 - theta * R))  
    neg_branch = (r ** (1 + 3 * theta * R) - 1) / (r ** (1 + theta * R))
    if not reverse:
        if epsi == 1:
            w = pos_branch
            sign = -1.0 if dispersive else 1.0
        else:
            w = neg_branch
            sign = 1.0
    else:
        # deut_*_rev: the branches are swapped between epsi signs.
        if epsi == 1:
            w = neg_branch
            sign = -1.0 if dispersive else 1.0
        else:
            w = pos_branch
            sign = 1.0
    return sign * aver * w


def phi_avg_absorptive(freq, epsi, omegD, omegQ, A, eta, r):
    """deuts.deut_phiavg -- absorptive, phi from 0..pi/2."""
    freq = np.asarray(freq, dtype=float)
    R = (freq - omegD) / (3.0 * omegQ)
    theta = omegQ / omegD
    aver = _phi_average(freq, epsi, omegD, omegQ, A, eta, _half_absorptive, _phi_grid()[:, None])
    aver = _weight(aver, epsi, R, theta, r, dispersive=False, reverse=False)
    return aver / omegQ


def phi_avg_dispersive(freq, epsi, omegD, omegQ, A, eta, r):
    """deuts.deut_disp_phiavg -- dispersive, phi from 0..pi/2."""
    freq = np.asarray(freq, dtype=float)
    R = (freq - omegD) / (3.0 * omegQ)
    theta = omegQ / omegD
    aver = _phi_average(freq, epsi, omegD, omegQ, A, eta, _half_dispersive, _phi_grid()[:, None])
    aver = _weight(aver, epsi, R, theta, r, dispersive=True, reverse=False)
    return aver / omegQ


def phi_avg_absorptive_rev(freq, epsi, omegD, omegQ, A, eta, r):
    """deuts.deut_phiavg_rev -- absorptive with swapped branches, phi 0..2pi.

    Used by the hole-burning bump projection.
    """
    freq = np.asarray(freq, dtype=float)
    R = (freq - omegD) / (3.0 * omegQ)
    theta = omegQ / omegD
    phi = (2.0 * np.pi * np.arange(_J) / _J)[:, None]
    aver = _phi_average(freq, epsi, omegD, omegQ, A, eta, _half_absorptive, phi)
    aver = _weight(aver, epsi, R, theta, r, dispersive=False, reverse=True)
    return aver / omegQ


def phi_avg_dispersive_rev(freq, epsi, omegD, omegQ, A, eta, r):
    """deuts.deut_disp_phiavg_rev -- dispersive with swapped branches, phi 0..2pi."""
    freq = np.asarray(freq, dtype=float)
    R = (freq - omegD) / (3.0 * omegQ)
    theta = omegQ / omegD
    phi = (2.0 * np.pi * np.arange(_J) / _J)[:, None]
    aver = _phi_average(freq, epsi, omegD, omegQ, A, eta, _half_dispersive, phi)
    aver = _weight(aver, epsi, R, theta, r, dispersive=True, reverse=True)
    return aver / omegQ


# ---------------------------------------------------------------------------
# Combined absorptive / dispersive curves (sum of both spin-flip signs)
# ---------------------------------------------------------------------------
def _fit_absorptive(freq, omegD, omegQ, A, eta, r):
    """deuts.deut_fit -- negative + positive absorptive spin-flip curves."""
    return (phi_avg_absorptive(freq, -1, omegD, omegQ, A, eta, r)
            + phi_avg_absorptive(freq, 1, omegD, omegQ, A, eta, r))


def _fit_dispersive(freq, omegD, omegQ, A, eta, r):
    """deuts.deut_disp_fit -- negative + positive dispersive spin-flip curves."""
    return (phi_avg_dispersive(freq, -1, omegD, omegQ, A, eta, r)
            + phi_avg_dispersive(freq, 1, omegD, omegQ, A, eta, r))


def _fit_absorptive_rev(freq, omegD, omegQ, A, eta, r):
    """deuts.deut_fit_rev -- both signs, using the branch-swapped (rev) average."""
    return (phi_avg_absorptive_rev(freq, -1, omegD, omegQ, A, eta, r)
            + phi_avg_absorptive_rev(freq, 1, omegD, omegQ, A, eta, r))


def _fit_dispersive_rev(freq, omegD, omegQ, A, eta, r):
    """deuts.deut_disp_fit_rev."""
    return (phi_avg_dispersive_rev(freq, -1, omegD, omegQ, A, eta, r)
            + phi_avg_dispersive_rev(freq, 1, omegD, omegQ, A, eta, r))


def _fit_absorptive_nonbolt(freq, omegD, omegQ, A, eta, rN, rP):
    """deuts.deut_fit_NonBolt -- independent ratios for the two spin-flip curves."""
    return (phi_avg_absorptive(freq, -1, omegD, omegQ, A, eta, rN)
            + phi_avg_absorptive(freq, 1, omegD, omegQ, A, eta, rP))


def _fit_dispersive_nonbolt(freq, omegD, omegQ, A, eta, rN, rP):
    """deuts.deut_disp_fit_NonBolt."""
    return (phi_avg_dispersive(freq, -1, omegD, omegQ, A, eta, rN)
            + phi_avg_dispersive(freq, 1, omegD, omegQ, A, eta, rP))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def cubic(freq, x3, x2, x1, x0):
    """Cubic wing/background term."""
    freq = np.asarray(freq, dtype=float)
    return x3 * freq ** 3 + x2 * freq ** 2 + x1 * freq + x0


def Xi(r):
    """deuts.Xi -- the quartic Xi(r) scaling correction (omitting the constant c)."""
    Pz = ratio_P(r)
    return _XI_A * Pz ** 4 + _XI_B * Pz ** 2 + 1.0


def ratio_P(r):
    """Vector polarization from the asymmetry parameter (deuts.ratio_method)."""
    return (r * r - 1.0) / (r * r + r + 1.0)


def ratio_Q(r):
    """Tensor polarization from the asymmetry parameter (deuts.ratio_tensor)."""
    return (r * r - 2.0 * r + 1.0) / (r * r + r + 1.0)


def ratio_P_nonbolt(rN, rP):
    """Vector polarization of a FREQUENCY-HOPPED (two-spin-temperature) deuteron line.

    *** EXPERIMENTAL (2026-06-26) -- not yet externally validated. *** The derivation and the
    self-consistency checks (exact reduction to ratio_P at rN==rP; round-trip recovery; area-method
    cross-check) all pass, but the method must still be confirmed against measured data -- it should
    reproduce the dissertation's frequency-hopping P & Q vs time for the Dec-10-2020 spin-up. Treat
    the numbers as provisional until that external validation is done.

    When DNP drives the line out of a single spin temperature, the two transitions can
    each equilibrate with THEMSELVES but not with each other: the -1<->0 pair has its own
    asymmetry rN = p0/p_{-1} and the 0<->+1 pair its own rP = p_{+1}/p0. With the three
    populations summing to 1 (p0 = rN/(rN*rP+rN+1), p_{+1}=rP*p0, p_{-1}=p0/rN), the vector
    polarization P = p_{+1}-p_{-1} is, in closed form,
        P = (rN*rP - 1) / (rN*rP + rN + 1).
    This is the RATIO-method analogue of the area method P = CC*(I+ + I-) for hopped lines,
    and it reduces EXACTLY to ratio_P(r) when rN == rP == r (a single spin temperature). It is
    the exact physical polarization. (The RAW-DATA area carries no Xi non-linearity: for
    spin-1 the two transitions have equal matrix elements, so the integrated data area is
    proportional to P exactly at zeta->0 for ANY populations. What diverges at strong hopping
    is the Xi=1 non-Boltzmann MODEL-component integral used by the cross-check, which omits
    the 1-Q population factor -- see the main technote, 'Frequency hopping'.)"""
    return (rN * rP - 1.0) / (rN * rP + rN + 1.0)


def ratio_Q_nonbolt(rN, rP):
    """Tensor polarization Q = 1 - 3 p0 of a frequency-hopped line (see ratio_P_nonbolt):
        Q = (rN*rP - 2*rN + 1) / (rN*rP + rN + 1),
    reducing exactly to ratio_Q(r) when rN == rP == r."""
    return (rN * rP - 2.0 * rN + 1.0) / (rN * rP + rN + 1.0)


def Q_equilibrium(P):
    """Equilibrium tensor polarization for a given vector polarization,
    Q_eq = 2 - sqrt(4 - 3 P^2).

    This is the unique Q a spin-1 system in equilibrium (a single spin
    temperature) has at vector polarization P; equivalently ratio_Q(r) evaluated
    at P = ratio_P(r). Measured Q above this curve indicates tensor enhancement,
    below it indicates suppression (e.g. by hole burning or ssRF). P, Q are
    fractions (not percent).
    """
    P = np.asarray(P, dtype=float)
    return 2.0 - np.sqrt(np.maximum(4.0 - 3.0 * P ** 2, 0.0))


def ratioPderiv(r):
    """dP/dr (deuts.ratioPderiv) -- for propagating sigma_r into sigma_P."""
    return (r ** 2 + 4.0 * r + 1.0) / ((r ** 2 + r + 1.0) ** 2)


def ratioQderiv(r):
    """dQ/dr (deuts.ratioQderiv)."""
    return (3.0 * (r ** 2 - 1.0)) / ((r ** 2 + r + 1.0) ** 2)


def ratioP_nonbolt_grad(rN, rP):
    """(dP/drN, dP/drP) of the frequency-hopped vector polarization
    P = (rN*rP - 1) / D, D = rN*rP + rN + 1. Needed to propagate the JOINT (rN, rP)
    fit covariance into sigma_P, including their correlation (the area/ratio freq-hop fit)."""
    D = rN * rP + rN + 1.0
    num = rN * rP - 1.0
    dP_drN = (rP * D - num * (rP + 1.0)) / (D * D)
    dP_drP = (rN * D - num * rN) / (D * D)
    return dP_drN, dP_drP


def ratioQ_nonbolt_grad(rN, rP):
    """(dQ/drN, dQ/drP) of the frequency-hopped tensor polarization
    Q = (rN*rP - 2*rN + 1) / D, D = rN*rP + rN + 1."""
    D = rN * rP + rN + 1.0
    num = rN * rP - 2.0 * rN + 1.0
    dQ_drN = ((rP - 2.0) * D - num * (rP + 1.0)) / (D * D)
    dQ_drP = (rN * D - num * rN) / (D * D)
    return dQ_drN, dQ_drP


def propagate_2(grad, cov):
    """Linear error propagation for a scalar f(x,y): sigma_f = sqrt(J C J^T) with J=(df/dx, df/dy)
    and C the 2x2 covariance [[var_x, cov],[cov, var_y]]. Used for the two-parameter freq-hop P/Q."""
    gx, gy = grad
    cov = np.asarray(cov, float)
    var = gx * gx * cov[0, 0] + gy * gy * cov[1, 1] + 2.0 * gx * gy * cov[0, 1]
    return float(np.sqrt(max(var, 0.0)))


def riemann_sum(xdata, ydata):
    """deuts.riemann_sum -- rectangular integral with step = (xmax-xmin)/N."""
    xdata = np.asarray(xdata, dtype=float)
    ydata = np.asarray(ydata, dtype=float)
    n = len(xdata)
    step = (xdata[-1] - xdata[0]) / n
    return step * np.sum(ydata)


def chisq_red(data, fit, std):
    """deuts.chisq_red -- reduced chi-squared with a single std."""
    data = np.asarray(data, dtype=float)
    fit = np.asarray(fit, dtype=float)
    chisq = np.sum((data - fit) ** 2) / std ** 2
    return chisq / (len(data) - 1)


# ---------------------------------------------------------------------------
# Full lineshape models (the functions handed to curve_fit)
# ---------------------------------------------------------------------------
def chi_real_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase):
    """Real (measured) hydrocarbon lineshape WITHOUT the Xi(r) correction.

    deuts.deut_real_double: scale*((1-K)*carbon + K*oxygen), absorptive and
    dispersive mixed by ``phase``. A2 is pegged via A2 = A1*omegQ1/omegQ2.
    """
    A2 = A1 * omegQ1 / omegQ2
    absor = scale * ((1 - K) * _fit_absorptive(freq, omegD, omegQ1, A1, eta1, r)
                     + K * _fit_absorptive(freq, omegD, omegQ2, A2, eta2, r))
    disper = scale * ((1 - K) * _fit_dispersive(freq, omegD, omegQ1, A1, eta1, r)
                      + K * _fit_dispersive(freq, omegD, omegQ2, A2, eta2, r))
    return absor * np.cos(phase) + disper * np.sin(phase)


def model_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase):
    """deuts.deut_real_double_Xi -- hydrocarbon lineshape with Xi(r), no cubic."""
    return Xi(r) * chi_real_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)


def model_double_cube(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale,
                      phase, x3, x2, x1, x0):
    """deuts.deut_real_double_cube_Xi -- hydrocarbon lineshape + cubic wing."""
    return (model_double(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)
            + cubic(freq, x3, x2, x1, x0))


def chi_real_double_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase):
    """deuts.deut_real_double_rev -- hydrocarbon lineshape with branch-swapped (rev)
    spin-flip averages (the mirror projection used for the hole-burning bump)."""
    A2 = A1 * omegQ1 / omegQ2
    absor = scale * ((1 - K) * _fit_absorptive_rev(freq, omegD, omegQ1, A1, eta1, r)
                     + K * _fit_absorptive_rev(freq, omegD, omegQ2, A2, eta2, r))
    disper = scale * ((1 - K) * _fit_dispersive_rev(freq, omegD, omegQ1, A1, eta1, r)
                      + K * _fit_dispersive_rev(freq, omegD, omegQ2, A2, eta2, r))
    return absor * np.cos(phase) + disper * np.sin(phase)


def model_double_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase):
    """deuts.deut_real_double_Xi_rev -- rev hydrocarbon lineshape with Xi(r)."""
    return Xi(r) * chi_real_double_rev(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, r, K, scale, phase)


def model_single(freq, omegD, omegQ, A, eta, r, scale, phase):
    """deuts.deut_real_Xi -- single-bond (e.g. ammonia) lineshape with Xi(r)."""
    absor = _fit_absorptive(freq, omegD, omegQ, A, eta, r)
    disper = _fit_dispersive(freq, omegD, omegQ, A, eta, r)
    real = absor * np.cos(phase) + disper * np.sin(phase)
    return Xi(r) * scale * real


def model_single_cube(freq, omegD, omegQ, A, eta, r, scale, phase, x3, x2, x1, x0):
    """deuts.deut_real_cube_Xi -- single-bond lineshape + cubic wing."""
    return (model_single(freq, omegD, omegQ, A, eta, r, scale, phase)
            + cubic(freq, x3, x2, x1, x0))


def model_double_nonbolt(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP,
                         K, scale, phase):
    """deuts.deut_real_double_Xi_NonBolt -- frequency-hopping (independent rN, rP).

    Note: the original applies Xi = 1 here (NOT Xi(r)), because out of Boltzmann
    equilibrium a single r is undefined. Preserved.
    """
    A2 = A1 * omegQ1 / omegQ2
    absor = scale * ((1 - K) * _fit_absorptive_nonbolt(freq, omegD, omegQ1, A1, eta1, rN, rP)
                     + K * _fit_absorptive_nonbolt(freq, omegD, omegQ2, A2, eta2, rN, rP))
    disper = scale * ((1 - K) * _fit_dispersive_nonbolt(freq, omegD, omegQ1, A1, eta1, rN, rP)
                      + K * _fit_dispersive_nonbolt(freq, omegD, omegQ2, A2, eta2, rN, rP))
    return absor * np.cos(phase) + disper * np.sin(phase)


def model_double_nonbolt_cube(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP,
                              K, scale, phase, x3, x2, x1, x0):
    """deuts.deut_real_double_cube_Xi_NonBolt -- frequency-hopping + cubic wing."""
    return (model_double_nonbolt(freq, omegD, omegQ1, omegQ2, A1, eta1, eta2, rN, rP,
                                 K, scale, phase)
            + cubic(freq, x3, x2, x1, x0))


# ---------------------------------------------------------------------------
# Component spin-flip curves (for the area method and hole-burning)
# ---------------------------------------------------------------------------
def component_signed(freq, epsi, omegD, omegQ, A, eta, r, k, scale, phase):
    """Single bond + single spin-flip-sign component (deuts NormComp).

    out = Xi(r)*scale*k*(absorptive*cos(phase) + dispersive*sin(phase))
    """
    return (Xi(r) * scale * k
            * (phi_avg_absorptive(freq, epsi, omegD, omegQ, A, eta, r) * np.cos(phase)
               + phi_avg_dispersive(freq, epsi, omegD, omegQ, A, eta, r) * np.sin(phase)))


def component_signed_rev(freq, epsi, omegD, omegQ, A, eta, r, k, scale, phase):
    """Branch-swapped (rev) component, used to build the hole-burning bump.

    Same shape as component_signed but with the reversed phi-averages.
    """
    return (Xi(r) * scale * k
            * (phi_avg_absorptive_rev(freq, epsi, omegD, omegQ, A, eta, r) * np.cos(phase)
               + phi_avg_dispersive_rev(freq, epsi, omegD, omegQ, A, eta, r) * np.sin(phase)))


def bump_component(freq, epsi, omegD, omegQ, A, eta, r_eq, r_stc, k, scale, phase):
    """deuts/SimpleXi_HB BumpComp: equilibrium component + carved-out bump.

    bump = component(r_eq) + 0.5*rev(r_eq) - 0.5*rev(r_stc)   (Eq. 4.13 form).
    """
    return (component_signed(freq, epsi, omegD, omegQ, A, eta, r_eq, k, scale, phase)
            + 0.5 * component_signed_rev(freq, epsi, omegD, omegQ, A, eta, r_eq, k, scale, phase)
            - 0.5 * component_signed_rev(freq, epsi, omegD, omegQ, A, eta, r_stc, k, scale, phase))


def bonds(model, omegQ1, omegQ2, A1, eta1, eta2, K):
    """List of (omegQ, A, eta, k, label) per bond for the chosen model.

    k is the bond weight: single -> 1.0; double -> (1-K) carbon, K oxygen.
    A2 is pegged via A2 = A1*omegQ1/omegQ2.
    """
    if model == "single":
        return [(omegQ1, A1, eta1, 1.0, "X")]
    A2 = A1 * omegQ1 / omegQ2
    return [(omegQ1, A1, eta1, 1.0 - K, "C"), (omegQ2, A2, eta2, K, "O")]


def area_polarizations(freq, params, model="double"):
    """Area-method P and Q from the fitted component spin-flip curves.

    Reproduces the area method in DeuteronAnalysisComponentsAllXi.py:
      CC = ratio_P(r) / riemann_sum(full real fit)
      AreaP = CC * (sum of all component integrals)
      AreaQ = CC * (sum of positive-sign integrals - negative-sign integrals)

    ``params`` is the parameter dict (keys: omegD, omegQ1, omegQ2, A1, eta1,
    eta2, r, K, scale, phase) -- omegQ2/eta2/K are ignored for the single model.
    Returns (AreaP, AreaQ, CC).
    """
    freq = np.asarray(freq, dtype=float)
    p = params
    if model == "single":
        full = model_single(freq, p["omegD"], p["omegQ1"], p["A1"], p["eta1"],
                             p["r"], p["scale"], p["phase"])
    else:
        full = model_double(freq, p["omegD"], p["omegQ1"], p["omegQ2"], p["A1"],
                            p["eta1"], p["eta2"], p["r"], p["K"], p["scale"], p["phase"])
    cc = ratio_P(p["r"]) / riemann_sum(freq, full)

    pos_area = 0.0
    neg_area = 0.0
    for omegQ, A, eta, k, _label in bonds(model, p["omegQ1"], p.get("omegQ2", 0.0),
                                          p["A1"], p["eta1"], p.get("eta2", 0.0),
                                          p.get("K", 0.0)):
        neg = component_signed(freq, -1, p["omegD"], omegQ, A, eta, p["r"], k,
                               p["scale"], p["phase"])
        pos = component_signed(freq, 1, p["omegD"], omegQ, A, eta, p["r"], k,
                               p["scale"], p["phase"])
        neg_area += riemann_sum(freq, neg)
        pos_area += riemann_sum(freq, pos)
    area_p = cc * (pos_area + neg_area)
    area_q = cc * (pos_area - neg_area)
    return area_p, area_q, cc


# ---------------------------------------------------------------------------
# Fast model: precomputed phi-averages for fitting only r, scale, phase (+cubic)
# ---------------------------------------------------------------------------
# The phi-averaged spin-flip curves depend ONLY on the lineshape constants
# (omegD, omegQ, A, eta) -- not on r, scale or phase. Over a spin-up the
# Dissertation shows these constants are effectively fixed and only r (and Xi(r))
# change. So precompute the four phi-averaged curves per bond ONCE; then each fit
# evaluation is cheap elementwise algebra. This is ~100-1000x faster per fit and
# better-conditioned at low P (where r and scale are otherwise degenerate).
#
# Use after determining the constants from one full free-parameter reference fit
# (typically the highest-P signal in a segment) or a material preset.
class FastModel:
    """Precomputed lineshape with free (r, scale, phase[, cubic]).

    Build with constants fixed; call ``curve_func`` (signature matching
    curve_fit: ``f(freq, r, scale, phase[, x3,x2,x1,x0])``) or ``area(r, phase)``.
    """

    def __init__(self, freqs, const, model="double", with_cube=False):
        self.freqs = np.asarray(freqs, dtype=float)
        self.const = dict(const)
        self.model = model
        self.with_cube = with_cube
        self.omegD = const["omegD"]
        self._bonds = bonds(model, const["omegQ1"], const.get("omegQ2", 0.0),
                            const["A1"], const["eta1"], const.get("eta2", 0.0),
                            const.get("K", 0.0))
        # Per bond: unweighted phi-averages (/omegQ) for both signs, plus R/theta.
        # ``base`` uses the 0..pi/2 grid (normal curves); ``base_rev`` uses the
        # 0..2pi grid (the branch-swapped curves used by the hole-burning bump).
        self._pre = []
        grid = _phi_grid()[:, None]
        grid_rev = (2.0 * np.pi * np.arange(_J) / _J)[:, None]
        for omegQ, A, eta, k, label in self._bonds:
            R = (self.freqs - self.omegD) / (3.0 * omegQ)
            theta = omegQ / self.omegD
            base, base_rev = {}, {}
            for epsi in (-1, 1):
                base[("abs", epsi)] = _phi_average(
                    self.freqs, epsi, self.omegD, omegQ, A, eta, _half_absorptive, grid) / omegQ
                base[("disp", epsi)] = _phi_average(
                    self.freqs, epsi, self.omegD, omegQ, A, eta, _half_dispersive, grid) / omegQ
                base_rev[("abs", epsi)] = _phi_average(
                    self.freqs, epsi, self.omegD, omegQ, A, eta, _half_absorptive, grid_rev) / omegQ
                base_rev[("disp", epsi)] = _phi_average(
                    self.freqs, epsi, self.omegD, omegQ, A, eta, _half_dispersive, grid_rev) / omegQ
            self._pre.append(dict(R=R, theta=theta, k=k, label=label,
                                  base=base, base_rev=base_rev))

    def _branches(self, r, R, theta):
        pos = (r ** 2 - r ** (1 - 3 * theta * R)) / (r ** (1 - theta * R))
        neg = (r ** (1 + 3 * theta * R) - 1) / (r ** (1 + theta * R))
        return neg, pos

    def _components(self, r, scale, phase):
        """Return dict (label, epsi) -> component curve (with Xi, scale, k)."""
        xi = Xi(r)
        cphase, sphase = np.cos(phase), np.sin(phase)
        comps = {}
        for pre in self._pre:
            neg, pos = self._branches(r, pre["R"], pre["theta"])
            b = pre["base"]
            for epsi, branch, dsign in ((-1, neg, 1.0), (1, pos, -1.0)):
                absorptive = b[("abs", epsi)] * branch
                dispersive = dsign * b[("disp", epsi)] * branch
                comps[(pre["label"], epsi)] = (
                    xi * scale * pre["k"] * (absorptive * cphase + dispersive * sphase))
        return comps

    def _components_rev(self, r, scale, phase):
        """Branch-swapped (rev) components -- the mirror projection for the bump.

        Reverse weighting (deut_*_rev): epsi=-1 uses the positive branch, epsi=+1
        uses the negative branch (with the dispersive -1 on epsi=+1).
        """
        xi = Xi(r)
        cphase, sphase = np.cos(phase), np.sin(phase)
        comps = {}
        for pre in self._pre:
            neg, pos = self._branches(r, pre["R"], pre["theta"])
            b = pre["base_rev"]
            for epsi, branch, dsign in ((-1, pos, 1.0), (1, neg, -1.0)):
                absorptive = b[("abs", epsi)] * branch
                dispersive = dsign * b[("disp", epsi)] * branch
                comps[(pre["label"], epsi)] = (
                    xi * scale * pre["k"] * (absorptive * cphase + dispersive * sphase))
        return comps

    def real(self, r, scale, phase):
        """Real lineshape (with Xi, no cubic)."""
        return sum(self._components(r, scale, phase).values())

    def real_rev(self, r, scale, phase):
        """Branch-swapped real lineshape (== deuts.deut_real_double_Xi_rev)."""
        return sum(self._components_rev(r, scale, phase).values())

    def bump(self, r_eq, r_stc, scale, phase):
        """Hole-burning bump signal: real(r_eq) + 0.5*rev(r_eq) - 0.5*rev(r_stc)."""
        return (self.real(r_eq, scale, phase)
                + 0.5 * self.real_rev(r_eq, scale, phase)
                - 0.5 * self.real_rev(r_stc, scale, phase))

    def _components_nonbolt(self, rN, rP, scale, phase):
        """Frequency-hopping components: neg curve at rN, pos curve at rP, NO Xi
        (a single r is undefined out of equilibrium -- matches model_double_nonbolt)."""
        cphase, sphase = np.cos(phase), np.sin(phase)
        comps = {}
        for pre in self._pre:
            negN, _ = self._branches(rN, pre["R"], pre["theta"])
            _, posP = self._branches(rP, pre["R"], pre["theta"])
            b = pre["base"]
            for epsi, branch, dsign in ((-1, negN, 1.0), (1, posP, -1.0)):
                absorptive = b[("abs", epsi)] * branch
                dispersive = dsign * b[("disp", epsi)] * branch
                comps[(pre["label"], epsi)] = (
                    scale * pre["k"] * (absorptive * cphase + dispersive * sphase))
        return comps

    def real_nonbolt(self, rN, rP, scale, phase):
        """Frequency-hopping lineshape (== model_double_nonbolt)."""
        return sum(self._components_nonbolt(rN, rP, scale, phase).values())

    def components(self, r, scale, phase):
        """Public access to the per-(label, epsi) component curves (r may be array)."""
        return self._components(r, scale, phase)

    def bump_components(self, r_eq, r_stc, scale, phase):
        """Per-component bump curves: comp(r_eq) + 0.5*rev(r_eq) - 0.5*rev(r_stc)."""
        normal = self._components(r_eq, scale, phase)
        rev_eq = self._components_rev(r_eq, scale, phase)
        rev_stc = self._components_rev(r_stc, scale, phase)
        return {key: normal[key] + 0.5 * rev_eq[key] - 0.5 * rev_stc[key] for key in normal}

    def curve_func(self, freq, r, scale, phase, *cube):
        out = self.real(r, scale, phase)
        if self.with_cube:
            out = out + cubic(self.freqs, *cube)
        return out

    def area(self, r, scale, phase):
        """Area-method (AreaP, AreaQ) using the precomputed components."""
        comps = self._components(r, scale, phase)
        full = sum(comps.values())
        cc = ratio_P(r) / riemann_sum(self.freqs, full)
        pos_area = sum(riemann_sum(self.freqs, comps[(lbl, 1)]) for _, _, _, _, lbl in self._bonds)
        neg_area = sum(riemann_sum(self.freqs, comps[(lbl, -1)]) for _, _, _, _, lbl in self._bonds)
        return cc * (pos_area + neg_area), cc * (pos_area - neg_area)


# ---------------------------------------------------------------------------
# Material presets (Dissertation Tables 5.2-5.11) -- initial guesses / HB refs.
# phase stored in degrees. omegQ ~ 0.0217 (carbon) is stable across materials.
# ---------------------------------------------------------------------------
MATERIALS = {
    "irradiated d-butanol (Dec 2025, 12th)": {
        "model": "double", "omegD": 33.181728, "omegQ1": 0.021701,
        "omegQ2": 0.026312, "A1": 0.025862, "eta1": 0.036178, "eta2": 0.168391,
        "K": 0.054466, "scale": 0.004037, "phase_deg": 6.41,
    },
    "irradiated d-butanol (Dec 2025, 13th/15th)": {
        "model": "double", "omegD": 33.181266, "omegQ1": 0.021701,
        "omegQ2": 0.026227, "A1": 0.025472, "eta1": 0.036356, "eta2": 0.170048,
        "K": 0.058355, "scale": 0.004423, "phase_deg": 5.78,
    },
    "trityl-doped d-propanediol (Oct 2021)": {
        "model": "double", "omegD": 33.051328, "omegQ1": 0.021720,
        "omegQ2": 0.025991, "A1": 0.028222, "eta1": 0.062230, "eta2": 0.196132,
        "K": 0.218579, "scale": 0.000296, "phase_deg": 2.16,
    },
    "deuterated ammonia (Dec 2024)": {
        "model": "single", "omegD": 32.450724, "omegQ1": 0.027335,
        "A1": 0.038029, "eta1": 0.116228, "scale": 0.001189, "phase_deg": 0.72,
    },
}


D_ALCOHOL_K_SPLIT = 0.13   # d-butanol K ~ 0.05-0.08 (one O-D bond) vs d-propanediol K ~ 0.22 (two);
                           # the midpoint cleanly separates the pair, which omegQ1 alone cannot.


def material_from_omega_q(omegQ1, model=None, K=None):
    """Identify the target material from the fitted line-shape constants, matching against the preset
    table (Dissertation Tables 5.2-5.11). The carbon quadrupole width omegQ1 ~ 0.0217 is shared across
    the d-alcohols and separates single-bond ammonia (~0.0273); the Larmor frequency omegD is
    field-dependent and deliberately NOT used. \
    \
    omegQ1 ALONE cannot tell d-butanol from d-propanediol (both ~0.0217); the real discriminant is the
    bond fraction K (d-but ~0.05-0.08 vs d-prop ~0.22, a ~4x gap, because propanediol has two O-D bonds
    vs butanol's one). So pass the fitted ``K`` (e.g. from main_fit's golden full fit, which re-derives
    it per epoch) to disambiguate that pair; without it the function falls back to the omegQ1-nearest
    preset, which may pick the wrong one of the two. Pass the bond ``model`` ('single'/'double') to
    restrict to that family. Returns a clean display name. \
    \
    NOTE: for the main pipeline label this is SUPERSEDED -- main_fit reports the explicitly-set PRESET
    (= COOLDOWN_MATERIAL, the material actually fit), which is authoritative and needs no K. This
    utility is for the case where only the fitted constants are known."""
    best, bd = None, float("inf")
    for name, m in MATERIALS.items():
        if model and m.get("model") != model:
            continue
        d = abs(m["omegQ1"] - omegQ1)
        if d < bd:
            bd, best = d, name
    low = (best or "").lower()
    # The omegQ1-nearest preset lands in the d-alcohol cluster -> let the fitted K pick d-but vs d-prop
    # (they share omegQ1, so the nearest-by-omegQ1 winner is a coin flip between them without K).
    if K is not None and ("butanol" in low or "propanediol" in low):
        return "d-butanol" if K < D_ALCOHOL_K_SPLIT else "d-propanediol"
    if "butanol" in low:
        return "d-butanol"
    if "propanediol" in low:
        return "d-propanediol"
    if "ammonia" in low:
        return "deuterated ammonia"
    return best or "d-butanol"
