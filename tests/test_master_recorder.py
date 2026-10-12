"""Offline tests for the Genesis unified master recorder."""

import csv
import json
from datetime import datetime, timedelta, timezone

import pytest

from archive.master_recorder import MasterRecorder


UTC = timezone.utc
T0 = datetime(2026, 10, 12, 14, 0, 0, tzinfo=UTC)


def test_snapshots_and_events(tmp_path):
    registry = {
        "UNH:GEN:CRYO:FRIDGE:F1_TI": {"units": "K"},
        "UNH:GEN:SSRF:FIT:P_FIT": {"units": ""},
    }
    recorder = MasterRecorder(tmp_path, registry)
    recorder.record(
        "UNH:GEN:CRYO:FRIDGE:F1_TI",
        1.23456789, at=T0,
        source_time=T0 - timedelta(milliseconds=25),
    )
    recorder.snapshot(T0)
    recorder.snapshot(T0 + timedelta(seconds=1))
    recorder.close()

    directory = tmp_path / "2026-10-12"
    with (directory / "genesis_master.csv").open() as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert rows[0]["timestamp_local"].endswith("-04:00")
    assert rows[0]["UNH:GEN:CRYO:FRIDGE:F1_TI|value"] == \
        "1.23456789"
    assert rows[0]["UNH:GEN:SSRF:FIT:P_FIT|quality"] == \
        "missing"
    assert rows[1]["UNH:GEN:CRYO:FRIDGE:F1_TI|age_s"] == \
        "1.0"

    events = [
        json.loads(s) for s in
        (directory / "genesis_master.jsonl").read_text().splitlines()
    ]
    assert len(events) == 1
    assert events[0]["source_utc"].endswith("59.975Z")

    exported = json.loads(
        recorder.export_json("2026-10-12").read_text()
    )
    assert len(exported["snapshots"]) == 2
    assert len(exported["events"]) == 1


def test_missing_stale_disconnected(tmp_path):
    pv = "UNH:GEN:MW:SRC:MW_FI"
    recorder = MasterRecorder(
        tmp_path, {pv: {}}, stale_after_s=2
    )
    recorder.snapshot(T0)
    recorder.record(pv, 123.45, at=T0 + timedelta(seconds=1))
    recorder.snapshot(T0 + timedelta(seconds=1))
    recorder.snapshot(T0 + timedelta(seconds=4))
    recorder.record(
        pv, None, at=T0 + timedelta(seconds=5), connected=False
    )
    recorder.snapshot(T0 + timedelta(seconds=5))
    recorder.close()

    with (
        tmp_path / "2026-10-12" / "genesis_master.csv"
    ).open() as f:
        rows = list(csv.DictReader(f))

    assert [r[pv + "|quality"] for r in rows] == [
        "missing", "valid", "stale", "disconnected"
    ]


@pytest.mark.parametrize("interval", [1.0, 0.5, 0.2])
def test_supported_intervals(tmp_path, interval):
    recorder = MasterRecorder(
        tmp_path, {"UNH:GEN:TEST:PV": {}}, interval_s=interval
    )
    recorder.snapshot(T0)
    recorder.snapshot(T0 + timedelta(seconds=interval))
    with pytest.raises(ValueError):
        recorder.snapshot(
            T0 + timedelta(seconds=interval + interval / 2)
        )
    recorder.close()


def test_reject_naive_timestamps_and_arrays(tmp_path):
    pv = "UNH:GEN:TEST:PV"
    recorder = MasterRecorder(tmp_path, {pv: {}})
    with pytest.raises(ValueError):
        recorder.record(pv, 1, at=datetime(2026, 10, 12))
    with pytest.raises(TypeError):
        recorder.record(pv, [1, 2, 3], at=T0)
    recorder.close()


def test_daily_rotation(tmp_path):
    pv = "UNH:GEN:TEST:PV"
    recorder = MasterRecorder(tmp_path, {pv: {}})
    recorder.record(pv, 1, at=T0)
    recorder.snapshot(T0)
    later = T0 + timedelta(days=1)
    recorder.record(pv, 2, at=later)
    recorder.snapshot(later)
    recorder.close()

    assert (
        tmp_path / "2026-10-12" / "genesis_master.csv"
    ).exists()
    assert (
        tmp_path / "2026-10-13" / "genesis_master.csv"
    ).exists()
