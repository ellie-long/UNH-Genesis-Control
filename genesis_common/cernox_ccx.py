"""Fail-closed multi-range Chebyshev Cernox resistance -> Kelvin converter.

Calibration curves are loaded directly from Thermo_Calibrations.ods, not instrument writes.
Only an explicit settings.yaml channel-to-source-row mapping activates a curve.
"""

import csv
import math
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET

COEFFICIENTS = tuple(f"c{i}" for i in range(10))



# The target stick source is the actual `.ods` spreadsheet, not a curve from
# a sensor PDF. The rows / Lake Shore slots are explicit, to fail closed if
# someone replaces this workbook with an incompatible calibration table.
_ODS_ROWS = {21: ('2-T1', 'C4'), 22: ('2-T3', 'C2')}
_T = 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'
_O = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
_P = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'


def _ods_cells(row, columns=51):
    """Return 0-based cells A..AY; support ODF compressed column repeats."""
    cells = []
    for cell in row:
        if cell.tag not in (f'{{{_T}}}table-cell', f'{{{_T}}}covered-table-cell'):
            continue
        repeat = int(cell.get(f'{{{_T}}}number-columns-repeated', '1'))
        if repeat < 1:
            raise ValueError('Invalid ODS column repetition')
        value = cell.get(f'{{{_O}}}value')
        if value is None:
            value = ''.join(cell.itertext()).strip()
        cells.extend([str(value or '')] * min(repeat, columns-len(cells)))
        if len(cells) >= columns:
            break
    return cells


def _load_ccx_ods(path):
    """Read spreadsheet rows 21/22, four (ZU,ZL,A0..A9) blocks each."""
    try:
        with ZipFile(path) as zf:
            root = ET.fromstring(zf.read('content.xml'))
    except (OSError, BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError('Cannot read ODS calibration') from error
    tables = root.findall(f'.//{{{_T}}}table')
    sheet = next((t for t in tables if t.get(f'{{{_T}}}name') == 'Thermo_Calibrations'), None)
    if sheet is None:
        raise ValueError('Missing Thermo_Calibrations sheet')
    rows = sheet.findall(f'{{{_T}}}table-row')
    if len(rows) < max(_ODS_ROWS):
        raise ValueError('Missing target-stick calibration rows 21 and 22')
    result = {}
    for row_number, (source, slot) in _ODS_ROWS.items():
        values = _ods_cells(rows[row_number-1])
        if len(values) < 51 or tuple(values[:3]) != (source, slot, 'CCX'):
            raise ValueError(f'Unexpected target-stick identity in spreadsheet row {row_number}')
        bands = []
        for band in range(4):
            offset = 3 + 12*band  # D/E/F..O, P/Q/R..AA, ...
            try:
                zu, zl = float(values[offset]), float(values[offset+1])
                coeffs = tuple(float(v) for v in values[offset+2:offset+12])
            except (ValueError, TypeError) as error:
                raise ValueError(f'Invalid CCX block {source} band {band+1}') from error
            if not (math.isfinite(zl) and math.isfinite(zu) and zl < zu and
                    len(coeffs)==10 and all(math.isfinite(c) for c in coeffs)):
                raise ValueError(f'Invalid CCX range/coefficient: {source}, band {band+1}')
            bands.append((zl, zu, coeffs))
        ordered = sorted(bands, key=lambda b: b[0])
        if any(a[1] < b[0] for a,b in zip(ordered, ordered[1:])):
            raise ValueError(f'Calibration gap for {source}')
        result[source] = bands
    return result


def load_ccx_bands(path):
    """Return {source_row: [(ZL, ZU, coefficients), ...]} with strict validation."""
    if Path(path).suffix.lower() == ".ods":
        return _load_ccx_ods(path)
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
