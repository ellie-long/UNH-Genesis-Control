"""Pure offline tests for timestamped NMR/source RF readbacks."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import sys

import pytest

# Allow tests to run even where the Linux DAQ's aioca is not installed.
if 'aioca' not in sys.modules:
    try:
        import aioca  # noqa: F401
    except ImportError:
        sys.modules['aioca'] = SimpleNamespace(FORMAT_TIME=object())

from logic_devices import nmr_source_readbacks as n


def pv(value, *, ts=100., severity=0, status=0, ok=True):
    class Value:
        timestamp = ts
        def __str__(self):
            return str(value)
        def __float__(self):
            return float(value)
    v = Value()
    v.severity = severity
    v.status = status
    v.ok = ok
    return v


def test_valid_timestamp():
    assert n.validate_timed_readback(pv(0), pv='TEST', now=101) is not None


def test_missing_metadata_is_rejected():
    with pytest.raises(RuntimeError, match='metadata'):
        n.validate_timed_readback(SimpleNamespace(ok=True), pv='TEST', now=101)


def test_alarm_is_rejected():
    with pytest.raises(RuntimeError, match='alarmed'):
        n.validate_timed_readback(pv(0, severity=2), pv='TEST', now=101)


def test_stale_time_is_rejected():
    with pytest.raises(RuntimeError, match='stale'):
        n.validate_timed_readback(pv(0, ts=20), pv='TEST', now=101)


def test_future_time_is_rejected():
    with pytest.raises(RuntimeError, match='future'):
        n.validate_timed_readback(pv(0, ts=104), pv='TEST', now=101)


def test_timezone_aware_datetime_accepted():
    timed = pv(0, ts=datetime.fromtimestamp(100, timezone.utc))
    n.validate_timed_readback(timed, pv='TEST', now=100.1)


def test_naive_datetime_rejected():
    timed = pv(0, ts=datetime.fromtimestamp(100))
    with pytest.raises(RuntimeError, match='timezone'):
        n.validate_timed_readback(timed, pv='TEST', now=100.1)


def test_source_and_epoch_readback_is_consistent(monkeypatch):
    values = [pv(8), pv('labview'), pv(8)]
    async def read(pv_name, *, max_age_s):
        return values.pop(0)
    monkeypatch.setattr(n, 'read_fresh_scalar', read)
    assert asyncio.run(n.read_fitter_source_snapshot()) == {
        'selected_source': 'labview', 'route_epoch': 8
    }


def test_torn_epoch_rejected(monkeypatch):
    values = [pv(8), pv('pynmr'), pv(9)]
    async def read(pv_name, *, max_age_s):
        return values.pop(0)
    monkeypatch.setattr(n, 'read_fresh_scalar', read)
    with pytest.raises(RuntimeError, match='changed'):
        asyncio.run(n.read_fitter_source_snapshot())


def test_unknown_source_rejected(monkeypatch):
    values = [pv(8), pv('unknown'), pv(8)]
    async def read(pv_name, *, max_age_s):
        return values.pop(0)
    monkeypatch.setattr(n, 'read_fresh_scalar', read)
    with pytest.raises(RuntimeError, match='unrecognized'):
        asyncio.run(n.read_fitter_source_snapshot())


def test_rf_off_fresh_verified(monkeypatch):
    async def read(pv_name, *, max_age_s):
        assert pv_name == n.RF_PV
        return pv(0)
    monkeypatch.setattr(n, 'read_fresh_scalar', read)
    assert asyncio.run(n.verify_ssrf_rf_off_fresh()) is None


def test_rf_on_rejected(monkeypatch):
    async def read(pv_name, *, max_age_s):
        return pv(1)
    monkeypatch.setattr(n, 'read_fresh_scalar', read)
    with pytest.raises(RuntimeError, match='not verified'):
        asyncio.run(n.verify_ssrf_rf_off_fresh())
