"""Tests for physical Genesis master PV registry."""

import csv
import json
from datetime import datetime, timezone

from archive.master_registry import build_registry
from archive.master_recorder import MasterRecorder


def test_complete_configured_registry():
    registry, report = build_registry()

    assert report["counts"] == {
        "Cryo": 168,
        "ssRF": 97,
        "Microwave": 120,
    }

    assert len(registry) == 385
    assert report["total"] == 385

    assert all(
        data["transport"] == "unverified"
        for data in registry.values()
    )

    scope_pvs = {
        name for name in registry
        if name.startswith("UNH:GEN:MW:SCOPE:")
    }
    assert scope_pvs == {
        "UNH:GEN:MW:SCOPE:SIG_VI",
        "UNH:GEN:MW:SCOPE:SIG_Comm_Status",
        "UNH:GEN:MW:SCOPE:SIG_Data_Valid",
        "UNH:GEN:MW:SCOPE:SIG_Acq_Status",
        "UNH:GEN:MW:SCOPE:SIG_Frame_Epoch",
        "UNH:GEN:MW:SCOPE:SIG_Data_Age",
        "UNH:GEN:MW:SCOPE:MAN:siglent_sds814x_time",
    }



def test_important_physical_channels():
    registry, _ = build_registry()

    assert "UNH:GEN:CRYO:FRIDGE:F1_TI" in registry
    assert "UNH:GEN:SSRF:FIT:P_FIT" in registry
    assert "UNH:GEN:MW:SRC:MW_FI" in registry
    assert "UNH:GEN:MW:PS:VMEAS" in registry


def test_verified_runtime_archive_references_resolved():
    registry, report = build_registry()

    assert report["invalid_archive_references"] == []

    expected = {
        "UNH:GEN:MW:SUP:RFActual",
        "UNH:GEN:MW:SUP:RFValid",
        "UNH:GEN:MW:SUP:RFState",
        "UNH:GEN:MW:SUP:ControlOwned",
        "UNH:GEN:MW:ROBOT:MOTION_INHIBITED",
        "UNH:GEN:MW:ROBOT:JOYSTICK_CONNECTED",
        "UNH:GEN:MW:ROBOT:JOYSTICK_ACTIVE",
        "UNH:GEN:MW:ROBOT:X_LIMITS",
        "UNH:GEN:MW:ROBOT:Y_LIMITS",
    }

    assert expected.issubset(registry)


def test_genuinely_undefined_archive_reference_reported(monkeypatch):
    import archive.master_registry as module

    original_load = module._load
    config_path = (
        "configs/commissioning/microwave_physical/"
        "supervisor/settings.yaml"
    )

    def load_with_invalid_reference(root, path):
        data = original_load(root, path)
        if str(path) == config_path:
            data["microwave_supervisor"]["archive_records"].append(
                "UNVERIFIED_ARCHIVE_RECORD"
            )
        return data

    monkeypatch.setattr(module, "_load", load_with_invalid_reference)

    registry, report = module.build_registry()

    assert len(registry) == 385
    assert report["invalid_archive_references"] == [{
        "ioc": "mw_supervisor",
        "record": "UNVERIFIED_ARCHIVE_RECORD",
        "config": config_path,
    }]


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
