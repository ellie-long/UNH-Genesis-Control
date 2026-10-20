"""Offline viewer tests for the new Cryo instruments."""

from pathlib import Path
import yaml

from archive.viewer_filters import (
    CRYO_GROUP_OPTIONS,
    CRYO_PRESETS,
    cryo_group_for_pv,
    filter_pvs,
    normalized_archive_key,
    resolve_cryo_preset,
    subsystem_for_pv,
)


ROOT = Path(__file__).resolve().parents[1]

NEW_PRESETS = (
    "Cryo - Pump / separator flow",
    "Cryo - Liquid helium levels",
    "Cryo - Low manometer pressure",
    "Cryo - Helium vapor-pressure temperature",
)


def test_new_cryo_group_classification():
    expected = {
        "UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM": "Flow",
        "UNH:GEN:CRYO:FLOW:LEFT:LPump_SLM": "Flow",
        "UNH:GEN:CRYO:FLOW:SEP:SepFlow_SLM": "Flow",
        "UNH:GEN:CRYO:FRIDGE:LHE:Fridge_LI": "Fridge",
        "UNH:GEN:CRYO:MAG:LHE:Magnet_LI": "Magnet",
        "UNH:GEN:CRYO:VAC:LOW:Mano_PI": "Vacuum",
        "UNH:GEN:CRYO:VAC:LOW:Mano_VapT": "Vacuum",
    }

    assert "Flow" in CRYO_GROUP_OPTIONS

    for pv, group in expected.items():
        assert subsystem_for_pv(pv) == "Cryo"
        assert cryo_group_for_pv(pv) == group


def test_new_cryo_presets_are_archived():
    config = yaml.safe_load((
        ROOT / "configs/target_physical/archiver/settings.yaml"
    ).read_text())

    available = [
        normalized_archive_key(pv)
        for pv in config["archiver"]["explicit_pvs"]
    ]

    available_set = set(available)

    for name in NEW_PRESETS:
        assert name in CRYO_PRESETS
        values = CRYO_PRESETS[name]

        assert 1 <= len(values) <= 4
        assert set(values) <= available_set

        resolved = resolve_cryo_preset(available, name)
        assert len(resolved) == len(values)


def test_flow_filter_preserves_other_cryo_groups():
    flow = "UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM"
    fridge = "UNH:GEN:CRYO:FRIDGE:F1_TI"
    magnet = "UNH:GEN:CRYO:MAG:M1_TI"

    available = [flow, fridge, magnet]

    assert filter_pvs(
        available,
        subsystem="Cryo",
        cryo_group="Flow",
    ) == [flow]

    assert filter_pvs(
        available,
        subsystem="Cryo",
        cryo_group="Fridge",
    ) == [fridge]

    assert filter_pvs(
        available,
        subsystem="Cryo",
        cryo_group="Magnet",
    ) == [magnet]
