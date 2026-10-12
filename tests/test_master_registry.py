"""Tests for physical Genesis master PV registry."""

import csv
import json
from collections import Counter
from datetime import datetime, timezone

from archive.master_registry import build_registry
from archive.master_recorder import MasterRecorder


def test_complete_configured_registry():
    registry, report = build_registry()

    assert report["counts"] == {
        "Cryo": 136,
        "ssRF": 97,
        "Microwave": 104,
    }

    assert len(registry) == 337
    assert report["total"] == 337

    assert all(
        data["transport"] == "unverified"
        for data in registry.values()
    )


def test_important_physical_channels():
    registry, _ = build_registry()

    assert "UNH:GEN:CRYO:FRIDGE:F1_TI" in registry
    assert "UNH:GEN:SSRF:FIT:P_FIT" in registry
    assert "UNH:GEN:MW:SRC:MW_FI" in registry
    assert "UNH:GEN:MW:PS:VMEAS" in registry


def test_invalid_archive_references_reported():
    _, report = build_registry()

    missing = report["invalid_archive_references"]

    assert len(missing) == 9

    counts = Counter(x["ioc"] for x in missing)

    assert counts["mw_supervisor"] == 4
    assert counts["mw_pcl602"] == 5


def test_registry_master_recorder_integration(tmp_path):
    registry, report = build_registry()

    recorder = MasterRecorder(
        tmp_path, registry, interval_s=1.0
    )

    stamp = datetime(
        2026, 10, 12, 14, 0, 0,
        tzinfo=timezone.utc,
    )

    recorder.record(
        "UNH:GEN:CRYO:FRIDGE:F1_TI",
        1.23456789,
        at=stamp,
    )
    recorder.record(
        "UNH:GEN:MW:SRC:MW_FI",
        140.125,
        at=stamp,
    )

    recorder.snapshot(stamp)
    recorder.close()

    directory = tmp_path / "2026-10-12"

    with (
        directory / "genesis_master.csv"
    ).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = reader.fieldnames

    assert len(rows) == 1
    assert len(fields) == 2 + 5 * report["total"]

    row = rows[0]
    assert row[
        "UNH:GEN:CRYO:FRIDGE:F1_TI|value"
    ] == "1.23456789"

    assert row[
        "UNH:GEN:MW:SRC:MW_FI|value"
    ] == "140.125"

    assert row[
        "UNH:GEN:SSRF:FIT:P_FIT|quality"
    ] == "missing"

    exported = json.loads(
        recorder.export_json("2026-10-12").read_text()
    )

    assert len(exported["events"]) == 2
    assert len(exported["snapshots"]) == 1
