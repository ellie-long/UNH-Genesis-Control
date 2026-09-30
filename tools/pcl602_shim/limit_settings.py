"""Validated, atomic persistence for GUI-adjustable machine-coordinate limits."""
import json
import math
import os
from pathlib import Path
import tempfile
from motion import AxisConfig

COORDINATE_LIMIT = 2_147_483_647


def bounds(config, low, high, enabled):
    if type(enabled) is not bool:
        raise ValueError('limits enabled must be boolean')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (low, high)):
        raise ValueError('limit bounds must be finite centimeters')
    if low >= high:
        raise ValueError('lower limit must be less than upper')
    scale = config['steps_per_cm_256']
    if any(not math.isfinite(v * scale) or abs(v * scale) > COORDINATE_LIMIT for v in (low, high)):
        raise ValueError('limits exceed numerical coordinate range')
    # Round inward: an accepted endpoint cannot exceed the entered boundary.
    lo, hi = math.ceil(low * scale), math.floor(high * scale)
    if config['physical_sign'] == -1:
        lo, hi = -hi, -lo
    return AxisConfig(lo, hi, coordinate_limit=COORDINATE_LIMIT, limits_enabled=enabled)


def load(path, axes):
    if path is None or not Path(path).exists():
        return
    data = json.loads(Path(path).read_text())
    if data.get('version') != 1 or set(data.get('axes', {})) != {'X', 'Y'}:
        raise ValueError('invalid saved limit settings')
    for name, config in axes.items():
        saved = data['axes'][name]
        if saved['steps_per_cm_256'] != config['steps_per_cm_256'] or saved['physical_sign'] != config['physical_sign']:
            raise ValueError('saved limits calibration differs from config.json')
        setting = saved['soft_limits']
        bounds(config, setting['low_cm'], setting['high_cm'], setting['enabled'])
        config['soft_limits'] = setting


def save(path, axes):
    if path is None:
        return  # In-process tests/embedded use; CLI always supplies a path.
    path = Path(path)
    data = dict(version=1, axes={name: {k: config[k] for k in
        ('steps_per_cm_256', 'physical_sign', 'soft_limits')} for name, config in axes.items()})
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name+'.', delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
