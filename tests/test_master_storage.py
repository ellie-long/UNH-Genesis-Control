"""Offline master-archive writer ownership and schema tests."""

import csv
from datetime import datetime, timedelta, timezone

import pytest

from archive.master_recorder import MasterRecorder


T0 = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
PV = "UNH:GEN:TEST:VALUE"


def test_exclusive_writer_ownership(tmp_path):
    first = MasterRecorder(tmp_path, {PV: {}})

    try:
        with pytest.raises(RuntimeError, match="Another"):
            MasterRecorder(tmp_path, {PV: {}})
    finally:
        first.close()

    second = MasterRecorder(tmp_path, {PV: {}})
    second.snapshot(T0)
    second.close()


def test_registry_change_refused_on_restart(tmp_path):
    first = MasterRecorder(tmp_path, {PV: {}})
    first.snapshot(T0)
    first.close()

    metadata = (
        tmp_path / "2026-10-12" / "metadata.json"
    ).read_bytes()

    changed = MasterRecorder(
        tmp_path, {PV: {}, "UNH:GEN:NEW:PV": {}}
    )

    try:
        with pytest.raises(RuntimeError, match="schema/registry"):
            changed.snapshot(T0 + timedelta(seconds=1))
    finally:
        changed.close()

    assert (
        tmp_path / "2026-10-12" / "metadata.json"
    ).read_bytes() == metadata


def test_corrupted_csv_header_refused(tmp_path):
    first = MasterRecorder(tmp_path, {PV: {}})
    first.snapshot(T0)
    first.close()

    path = tmp_path / "2026-10-12" / "genesis_master.csv"
    content = path.read_text()
    original_header, rest = content.split("\n", 1)
    path.write_text("incorrect,header\n" + rest)
    before = path.read_bytes()

    restarted = MasterRecorder(tmp_path, {PV: {}})

    try:
        with pytest.raises(RuntimeError, match="CSV column schema"):
            restarted.snapshot(T0 + timedelta(seconds=1))
    finally:
        restarted.close()

    assert path.read_bytes() == before


def test_incomplete_jsonl_refused(tmp_path):
    first = MasterRecorder(tmp_path, {PV: {}})
    first.record(PV, 1, at=T0)
    first.close()

    path = tmp_path / "2026-10-12" / "genesis_master.jsonl"

    with path.open("ab") as handle:
        handle.write(b'{"partial":true')

    before = path.read_bytes()
    restarted = MasterRecorder(tmp_path, {PV: {}})

    try:
        with pytest.raises(RuntimeError, match="incomplete"):
            restarted.record(
                PV, 2, at=T0 + timedelta(seconds=1)
            )
    finally:
        restarted.close()

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "interval", [0.0, 0.2, 0.5, float("nan"), float("inf")]
)
def test_unsafe_snapshot_intervals_rejected(tmp_path, interval):
    with pytest.raises(ValueError, match="at least"):
        MasterRecorder(
            tmp_path, {PV: {}}, interval_s=interval
        )


def test_valid_restart_with_identical_schema(tmp_path):
    first = MasterRecorder(tmp_path, {PV: {}})
    first.record(PV, 1.25, at=T0)
    first.snapshot(T0)
    first.close()

    second = MasterRecorder(tmp_path, {PV: {}})
    second.record(
        PV, 2.50, at=T0 + timedelta(seconds=2)
    )
    second.snapshot(T0 + timedelta(seconds=2))
    second.close()

    path = tmp_path / "2026-10-12" / "genesis_master.csv"

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert rows[0][PV + "|value"] == "1.25"
    assert rows[1][PV + "|value"] == "2.5"
