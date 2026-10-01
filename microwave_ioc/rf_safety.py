"""Shared transition checks. No transports are opened at import time."""
import asyncio
import math
import time

import aioca


def finite(value, label):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} unavailable/nonfinite")
    return result


def flag(value, label):
    result = finite(value, label)
    if result not in (0, 1):
        raise ValueError(f"invalid {label}")
    return bool(result)


def fresh(value, maximum_age, label, now=None):
    age = (time.time() if now is None else now) - finite(value, label)
    if not 0 <= age <= maximum_age:
        raise ValueError(f"{label} stale")


async def read_snapshot(mapping, timeout):
    values = await asyncio.gather(*(aioca.caget(pv, timeout=timeout) for pv in mapping.values()))
    for value in values:
        if getattr(value, "severity", 0):
            raise ValueError("required readback alarmed")
    return dict(zip(mapping, values))


def validate_on(values, settings, simulation=False):
    """5 V is an ON-edge condition, never a continuous attenuation trip."""
    for key in ("uca_connected", "vaunix_connected", "uca_output", "rf_valid", "rf_ready", "control_owned"):
        if not flag(values[key], key):
            raise ValueError(f"{key} not ready")
    for key in ("uca_time", "vaunix_time", "rf_time"):
        fresh(values[key], float(settings.get("maximum_readback_age", 4.0)), key)
    if not simulation and (flag(values["simulation"], "simulation") or flag(values["test_mode"], "test mode")):
        raise ValueError("physical source required")
    voltage = finite(values["uca_voltage"], "UCA voltage")
    target = finite(settings.get("required_voltage", 5.0), "target voltage")
    tolerance = finite(settings.get("tolerance", 0.1), "voltage tolerance")
    if tolerance < 0 or abs(voltage - target) > tolerance + 1e-12:
        raise ValueError(f"RF blocked: need {target:g} +/- {tolerance:g} V")
    carrier = finite(values["frequency"], "carrier")
    power = finite(values["power"], "power")
    if not settings["frequency_min_ghz"] <= carrier <= settings["frequency_max_ghz"]:
        raise ValueError("carrier outside configured limits")
    if not settings["power_min_dbm"] <= power <= settings["power_max_dbm"]:
        raise ValueError("power outside configured limits")
    if not flag(values["pll"], "PLL"):
        raise ValueError("PLL unlocked")
