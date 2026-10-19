"""Fail-closed multi-range Chebyshev Cernox resistance -> Kelvin converter.

Calibration curves are loaded from Thermo_Calibrations.ods, not instrument writes.
Only an explicit settings.yaml channel-to-source-row mapping activates a curve.
"""

import csv
import math
from pathlib import Path

COEFFICIENTS = tuple(f"c{i}" for i in range(10))


def load_ccx_bands(path):
    """Return {source_row: [(ZL, ZU, coefficients), ...]} with strict validation."""
    result = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sensor = (row.get("Source_Row") or "").strip()
            if not sensor:
                raise ValueError("CCX calibration without source identity")
            try:
                lo, hi = float(row["ZL_log10_ohm"]), float(row["ZU_log10_ohm"])
                coefs = tuple(float(row[name]) for name in COEFFICIENTS)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"Invalid CCX coefficients: {sensor}") from error
            if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi and
                    all(math.isfinite(c) for c in coefs)):
                raise ValueError(f"Invalid CCX band: {sensor}")
            result.setdefault(sensor, []).append((lo, hi, coefs))
    if not result:
        raise ValueError("Empty CCX calibration")
    for sensor, bands in result.items():
        if len(bands) != 4:
            raise ValueError(f"Expected four CCX bands for {sensor}")
        # Adjacent calibration bands must overlap (rather than silently leave gaps).
        ordered = sorted(bands, key=lambda b: b[0])
        if any(a[1] < b[0] for a, b in zip(ordered, ordered[1:])):
            raise ValueError(f"Gap between CCX bands: {sensor}")
    return result


def ccx_kelvin(bands, resistance, *, kelvin_bounds=None):
    """Evaluate only within curve range; return NaN if invalid/uncovered."""
    try:
        r = float(resistance)
    except (ValueError, TypeError, OverflowError):
        return float("nan")
    if not math.isfinite(r) or r <= 0 or not bands:
        return float("nan")
    z = math.log10(r)
    candidates = [band for band in bands if band[0] <= z <= band[1]]
    if not candidates:
        return float("nan")
    # Prefer the band that keeps the measured point furthest from an overlap boundary.
    lo, hi, coeffs = max(candidates, key=lambda b: min(z-b[0], b[1]-z))
    k = (2*z-lo-hi)/(hi-lo)
    k = max(-1.0, min(1.0, k))
    theta = math.acos(k)
    temp = sum(coef * math.cos(i*theta) for i, coef in enumerate(coeffs))
    if not math.isfinite(temp):
        return float("nan")
    if kelvin_bounds and not (kelvin_bounds[0] <= temp <= kelvin_bounds[1]):
        return float("nan")
    return temp
