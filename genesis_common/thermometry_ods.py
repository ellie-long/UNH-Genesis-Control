"""Read CCS, CCS2 and CCCS Lake Shore thermometry directly from the lab ODS.

The ODS workbook is the sole source of coefficient values.  Sensor names and
physical Lake Shore slots are validated against the IOC's explicit wiring map.
No instrument writes, no cached derivative CSV, no silent KRDG fallback.
"""

import math
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

_TABLE = 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'
_OFFICE = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
_LENGTH = {'CCS': 3, 'CCS2': 3, 'CCCS': 7}


def _cells(row, limit=10):
    values = []
    for cell in row:
        if cell.tag not in (f'{{{_TABLE}}}table-cell', f'{{{_TABLE}}}covered-table-cell'):
            continue
        repeated = int(cell.get(f'{{{_TABLE}}}number-columns-repeated', '1'))
        if repeated < 1:
            raise ValueError('Invalid ODS repeated-column count')
        raw = cell.get(f'{{{_OFFICE}}}value')
        if raw is None:
            raw = ''.join(cell.itertext()).strip()
        values.extend([str(raw or '').strip()] * min(repeated, limit - len(values)))
        if len(values) >= limit:
            break
    return values + [''] * (limit - len(values))


def load_classic_ods(path, expected):
    """Return {slot: (type, coeffs, name)} for {name: slot} from ODS.

    Requires every configured classic thermometer, detects name/slot mismatch,
    and rejects malformed or nonfinite coefficients.  CCX rows are intentionally
    excluded; they are loaded by the dedicated multi-band CCX converter.
    """
    path = Path(path)
    if not expected:
        return {}
    if len(expected) != len(set(expected.values())):
        raise ValueError('Duplicate Lake Shore slot in expected thermometer mapping')
    try:
        with ZipFile(path) as archive:
            root = ET.fromstring(archive.read('content.xml'))
    except (OSError, BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f'Cannot read thermometry ODS {path}') from exc

    table = next((node for node in root.findall(f'.//{{{_TABLE}}}table')
                  if node.get(f'{{{_TABLE}}}name') == 'Thermo_Calibrations'), None)
    if table is None:
        raise ValueError('Missing Thermo_Calibrations worksheet')
    rows = table.findall(f'{{{_TABLE}}}table-row')
    if not rows or _cells(rows[0], 3) != ['Name', 'Lakeshore Slot', 'Thermistor Type']:
        raise ValueError('Unexpected ODS thermometry header')

    found = {}
    for row in rows[1:]:
        vals = _cells(row)
        name = vals[0]
        if name not in expected:
            continue
        if name in found:
            raise ValueError(f'Duplicate thermometer {name} in ODS')
        slot = vals[1]
        if slot != expected[name]:
            raise ValueError(f'ODS slot mismatch for {name}: {slot} != {expected[name]}')
        sensor_type = vals[2].upper()
        size = _LENGTH.get(sensor_type)
        if size is None:
            raise ValueError(f'Unexpected ODS curve type {sensor_type} for {name}')
        try:
            coeffs = [float(v) for v in vals[3:3 + size]]
        except (ValueError, TypeError) as exc:
            raise ValueError(f'Invalid ODS coefficients for {name}') from exc
        if len(coeffs) != size or not all(math.isfinite(v) for v in coeffs):
            raise ValueError(f'Nonfinite/missing ODS coefficients for {name}')
        found[name] = (sensor_type, coeffs, name)

    missing = sorted(set(expected) - set(found))
    if missing:
        raise ValueError(f'Missing ODS thermometer calibration(s): {missing}')
    return {expected[name]: entry for name, entry in found.items()}
