"""Fresh, alarm-validated EPICS observations for NMR source selection.

Read only. No device commands or source switching.
"""

from datetime import datetime, timezone
import math
import time

import aioca

RF_PV = "UNH:GEN:SSRF:GEN:RF_RBV"
FITTER_PREFIX = "UNH:GEN:SSRF:FIT"


def _unix_timestamp(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("EPICS timestamp timezone unspecified")
        return value.timestamp()
    if type(value) in (int, float):
        return float(value)
    raise RuntimeError("EPICS timestamp is missing or unsupported")


def validate_timed_readback(value, *, pv, now, max_age_s=3.0):
    """Reject unsupported/old/future timestamps and any EPICS alarm."""
    if type(max_age_s) not in (int, float) or not math.isfinite(max_age_s) or max_age_s <= 0:
        raise ValueError("Invalid NMR readback freshness limit")
    if not all(hasattr(value, x) for x in ("timestamp", "severity", "status", "ok")):
        raise RuntimeError(f"{pv}: EPICS time/alarm metadata unavailable")
    if value.ok is not True or value.severity != 0 or value.status != 0:
        raise RuntimeError(f"{pv}: EPICS readback alarmed or disconnected")
    ts = _unix_timestamp(value.timestamp)
    if not math.isfinite(ts) or not math.isfinite(now):
        raise RuntimeError(f"{pv}: invalid EPICS timestamp")
    age = now - ts
    if age > max_age_s or age < -1.0:
        raise RuntimeError(f"{pv}: stale or future EPICS timestamp")
    return value


async def read_fresh_scalar(pv, *, max_age_s=3.0):
    value = await aioca.caget(pv, format=aioca.FORMAT_TIME, timeout=2.0)
    return validate_timed_readback(value, pv=pv, now=time.time(), max_age_s=max_age_s)


def _epoch(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Fitter source epoch is invalid") from exc
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        raise RuntimeError("Fitter source epoch is invalid")
    return int(numeric)


async def read_fitter_source_snapshot(*, prefix=FITTER_PREFIX, max_age_s=3.0):
    """Double-read epoch around selected source to reject torn observations."""
    if type(prefix) is not str or not prefix:
        raise ValueError("Missing fitter PV prefix")
    epoch_pv = f"{prefix}:NMR_SOURCE_EPOCH_RBV"
    source_pv = f"{prefix}:NMR_SOURCE_RBV"
    first = _epoch(await read_fresh_scalar(epoch_pv, max_age_s=max_age_s))
    source = str(await read_fresh_scalar(source_pv, max_age_s=max_age_s)).strip().lower()
    last = _epoch(await read_fresh_scalar(epoch_pv, max_age_s=max_age_s))
    if first != last:
        raise RuntimeError("Fitter source and epoch changed during readback")
    if source not in ("labview", "pynmr"):
        raise RuntimeError("Fitter source is unrecognized")
    return {"selected_source": source, "route_epoch": last}


async def verify_ssrf_rf_off_fresh(*, max_age_s=3.0):
    value = await read_fresh_scalar(RF_PV, max_age_s=max_age_s)
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("SMC100A RF state is unverified") from exc
    if not math.isfinite(numeric) or numeric != 0:
        raise RuntimeError("SMC100A RF OFF is not verified")
    return None
