"""Genesis master registry extensibility tests."""

import pytest
import yaml

from archive.master_registry import build_registry


def make_manifest(tmp_path, sources):
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "sources": sources,
    }))
    return path


def test_new_subsystem_without_python_changes(tmp_path):
    make_manifest(tmp_path, [
        {
            "subsystem": "Magnet",
            "kind": "pv_list",
            "pvs": [
                "UNH:GEN:MAG:FIELD_RBV",
                "UNH:GEN:MAG:STATE_RBV",
            ],
        },
        {
            "subsystem": "External NMR",
            "kind": "pv_list",
            "pvs": ["UNH:GEN:NMR:POL_RBV"],
        },
    ])

    registry, report = build_registry(
        tmp_path, "sources.yaml"
    )

    assert report["counts"] == {
        "Magnet": 2,
        "External NMR": 1,
    }
    assert report["total"] == 3
    assert "UNH:GEN:MAG:FIELD_RBV" in registry
    assert "UNH:GEN:NMR:POL_RBV" in registry


def test_duplicate_pv_rejected(tmp_path):
    make_manifest(tmp_path, [
        {
            "subsystem": "Magnet",
            "kind": "pv_list",
            "pvs": ["UNH:GEN:TEST:VALUE"],
        },
        {
            "subsystem": "Cryo",
            "kind": "pv_list",
            "pvs": ["UNH:GEN:TEST:VALUE"],
        },
    ])

    with pytest.raises(ValueError, match="Duplicate"):
        build_registry(tmp_path, "sources.yaml")


def test_unknown_source_kind_rejected(tmp_path):
    make_manifest(tmp_path, [
        {"subsystem": "Test", "kind": "unknown"}
    ])

    with pytest.raises(ValueError, match="Unsupported"):
        build_registry(tmp_path, "sources.yaml")


def test_existing_physical_coverage_unchanged():
    registry, report = build_registry()

    assert report["counts"] == {
        "Cryo": 136,
        "ssRF": 97,
        "Microwave": 104,
    }

    assert report["total"] == 337
    assert len(report["invalid_archive_references"]) == 9
    assert len(registry) == 337
