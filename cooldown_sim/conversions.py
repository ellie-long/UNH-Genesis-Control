"""Unit conversions between recorded quantities and what instruments put on the wire.

The emulators must answer in each instrument's own terms so the *unchanged*
Genesis drivers do their own conversions:

* LS224 with a ``calibration:`` file reads ``SRDG?`` (ohms) and applies the
  lab's CCS/CCS2/CCCS curves itself, so the emulator must send the resistance
  that the driver will convert back to the recorded kelvin --
  :class:`InverseCalibration`.
* TPG262 reports in its display unit (mbar, ``UNI`` = 0); the record is Torr.
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
