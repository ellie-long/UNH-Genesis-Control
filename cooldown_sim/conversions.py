"""Unit conversions between recorded quantities and what instruments put on the wire.

The emulators must answer in each instrument's own terms so the *unchanged*
Genesis drivers do their own conversions:

* LS224 with a ``calibration:`` file reads ``SRDG?`` (ohms) and applies the
  lab's CCS/CCS2/CCCS curves itself, so the emulator must send the resistance
  that the driver will convert back to the recorded kelvin --
  :class:`InverseCalibration`.
* TPG262 reports in its display unit: Torr (``UNI`` = 1), as the live
  controller is set; the record is Torr too.
* IONIVAC streams a 16-bit count: ``P(Torr) = 10**(count/4000 - 12.625)``.
* LS455 reports field in its display unit (gauss, ``UNIT?`` = 1).

All functions are pure; SI is not used on the wire because the instruments
do not use it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

TORR_TO_MBAR = 1.33322368        # 1 Torr = 133.322368 Pa = 1.33322368 mbar
MBAR_TO_TORR = 1.0 / TORR_TO_MBAR
GAUSS_TO_TESLA = 1.0e-4

IONIVAC_OFFSET = 12.625          # from target_ioc/instruments/ionivac.py
IONIVAC_SCALE = 4000.0
IONIVAC_MAX_COUNT = 0xFFFF


def torr_to_mbar(p_torr: float) -> float:
    return p_torr * TORR_TO_MBAR


def mbar_to_torr(p_mbar: float) -> float:
    return p_mbar * MBAR_TO_TORR


def torr_to_ionivac_count(p_torr: float) -> int | None:
    """Nearest 16-bit telegram count for ``p_torr`` (None if not representable).

    Quantisation: one count is a factor 10**(1/4000) ~ 0.058 % in pressure.
    """
    if not (math.isfinite(p_torr) and p_torr > 0):
        return None
    count = round((math.log10(p_torr) + IONIVAC_OFFSET) * IONIVAC_SCALE)
    if count < 1 or count > IONIVAC_MAX_COUNT:
        return None
    return int(count)


def ionivac_count_to_torr(count: int) -> float:
    return 10.0 ** (count / IONIVAC_SCALE - IONIVAC_OFFSET)


IONIVAC_REL_QUANTUM = 10.0 ** (0.5 / IONIVAC_SCALE) - 1.0   # half a count, relative


# ------------------------------------------------------------- LS224 inverse
@dataclass
class _Branch:
    ln_r: np.ndarray        # ascending
    t: np.ndarray           # strictly decreasing along ln_r
    t_min: float
    t_max: float


class InverseCalibration:
    """kelvin -> ohms for one LS224 slot, on the physical (NTC) branch.

    The lab's curve forms are not monotonic over all R: e.g. CCS
    ``T = 1/(a + b lnR + c lnR^3)`` peaks near room temperature and turns
    over below it.  The physical branch is the one where resistance rises as
    temperature falls, running from the curve's maximum (~294-319 K for the
    fridge/magnet sensors) down to the cold end.  Temperatures outside the
    branch are clamped to its ends and reported via ``clamped``.

    ``forward`` must be ``ls224.resistance_to_kelvin`` bound to the slot's
    calibration entry, so the inverse is exactly consistent with what the
    unchanged driver computes.
    """

    #: hottest temperature considered physical (K) when a curve has no maximum
    T_CAP = 400.0
    T_FLOOR = 0.01

    def __init__(self, forward, r_min: float = 1.0, r_max: float = 1.0e8, n: int = 40001):
        self.forward = forward
        ln_r = np.linspace(math.log(r_min), math.log(r_max), n)
        t = np.array([forward(math.exp(x)) for x in ln_r])
        self._branch = self._physical_branch(ln_r, t)

    def _physical_branch(self, ln_r: np.ndarray, t: np.ndarray) -> _Branch:
        good = np.isfinite(t) & (t > self.T_FLOOR) & (t < self.T_CAP)
        dec = np.zeros_like(good)
        dec[:-1] = (t[1:] < t[:-1]) & good[:-1] & good[1:]
        # anchor on the 4 K region: the branch an LHe-cooled sensor lives on
        candidates = np.nonzero(dec & (np.abs(np.log(np.where(good, t, 1.0) / 4.0)) < 3.0))[0]
        if not len(candidates):
            raise ValueError("calibration has no decreasing branch through the cryogenic range")
        i = int(candidates[np.argmin(np.abs(t[candidates] - 4.0))])
        lo = i
        while lo > 0 and dec[lo - 1]:
            lo -= 1
        hi = i
        while hi < len(dec) - 1 and dec[hi]:
            hi += 1
        seg_ln, seg_t = ln_r[lo:hi + 1], t[lo:hi + 1]
        return _Branch(seg_ln, seg_t, float(seg_t.min()), float(seg_t.max()))

    @property
    def t_range(self) -> tuple[float, float]:
        return self._branch.t_min, self._branch.t_max

    def ohms(self, kelvin: float) -> tuple[float, bool]:
        """(resistance, clamped).  NaN in -> (NaN, False)."""
        if not math.isfinite(kelvin):
            return float("nan"), False
        b = self._branch
        clamped = not (b.t_min <= kelvin <= b.t_max)
        target = min(max(kelvin, b.t_min), b.t_max)
        # t decreases along ln_r: interpolate on the reversed arrays, then refine
        guess = float(np.interp(target, b.t[::-1], b.ln_r[::-1]))
        j = int(np.clip(np.searchsorted(-b.t, -target), 1, len(b.t) - 1))
        lo, hi = b.ln_r[j - 1], b.ln_r[j]
        x = _bisect(lambda x: self.forward(math.exp(x)) - target, lo, hi, guess)
        return math.exp(x), clamped


def _bisect(f, lo: float, hi: float, guess: float, iters: int = 80) -> float:
    flo, fhi = f(lo), f(hi)
    if not (math.isfinite(flo) and math.isfinite(fhi)) or flo * fhi > 0:
        return guess
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if fm == 0 or hi - lo < 1e-14:
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------- magnet LHe volume
#: Magnet-space LHe: height on the AMI level sensor (inches) -> litres, Ells's
#: piecewise conversion (2026-10-22).  Each segment is the liquid's cross
#: section at that height: the Ø12" bore (1.853 L/in) up to 0.75"; the bore
#: around the Ø7" solenoid (1.223 L/in) to the 16.5" magnet-support baffle;
#: the Ø16" belly (3.137 L/in) to 20.25"; the belly around the insert's Ø8"
#: tube (2.471 L/in) above.  (start inch, litres at start, litres per inch)
MAGNET_LHE_SEGMENTS = (
    (0.0, 0.0, 1.85334),
    (0.75, 1.39, 1.22269),
    (16.5, 1.39 + 19.2573, 3.13716),
    (20.25, 1.39 + 19.2573 + 11.7643, 2.47112),
)


def magnet_lhe_liters(inches: float) -> float:
    """Magnet-space LHe volume (L) at a level-sensor height (in).

    Exactly the lab's conversion, including its linear extension below 0 in
    (a negative reading gives a negative volume, as it does there)."""
    h = float(inches)
    if not math.isfinite(h):
        return math.nan
    start, base, per_inch = MAGNET_LHE_SEGMENTS[0]
    for seg in MAGNET_LHE_SEGMENTS[1:]:
        if h > seg[0]:
            start, base, per_inch = seg
    return base + (h - start) * per_inch


def magnet_lhe_inches(liters: float) -> float:
    """Inverse of :func:`magnet_lhe_liters`: the height (in) holding ``liters``."""
    v = float(liters)
    if not math.isfinite(v):
        return math.nan
    start, base, per_inch = MAGNET_LHE_SEGMENTS[0]
    for seg in MAGNET_LHE_SEGMENTS[1:]:
        if v > seg[1]:
            start, base, per_inch = seg
    return start + (v - base) / per_inch


# --------------------------------------------------------- fridge LHe volume
IN_TO_CM = 2.54


def _cyl_liters(diameter_in: float, length_in: float) -> float:
    """Volume (L) of a cylinder given in inches."""
    return 0.001 * math.pi * (diameter_in * IN_TO_CM / 2.0) ** 2 * length_in * IN_TO_CM


def fridge_lhe_liters(inches: float) -> float:
    """Fridge LHe volume (L) at a fridge level-probe height (in): Ells's
    piecewise conversion (2026-10-22), branch for branch.

    The fridge is a Ø2.25" tube (8.8" of it below the probe's zero), then a
    Ø5.78" section from 10.71" for 5", then Ø7.26" above 15.71".  Kept as the
    lab wrote it, including two quirks (open question 89): below 10.71" the
    tube length is 8.8 + h, from 10.71" a fixed 19.1" (8.8 + 10.71 = 19.51), so
    the volume steps DOWN by ~0.027 L there; and exactly 15.71", which the
    original matches to no branch, uses the (equal) value of both neighbours."""
    h = float(inches)
    if not math.isfinite(h):
        return math.nan
    if h < 0.1:
        return _cyl_liters(2.25, 8.8)
    if h < 10.71:
        return _cyl_liters(2.25, 8.8 + h)
    if h < 15.71:
        return _cyl_liters(2.25, 19.1) + _cyl_liters(5.78, h - 10.71)
    return _cyl_liters(2.25, 19.1) + _cyl_liters(5.78, 5.0) + _cyl_liters(7.26, h - 15.71)
