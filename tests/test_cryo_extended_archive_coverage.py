from pathlib import Path

import yaml

from archive.master_registry import build_registry


ROOT = Path(__file__).resolve().parents[1]

ADDITIONAL_PVS = {
    "UNH:GEN:CRYO:COLDBOARD:COMM",
    "UNH:GEN:CRYO:FLOW:RIGHT:COMM",
    "UNH:GEN:CRYO:FLOW:LEFT:COMM",
    "UNH:GEN:CRYO:FLOW:SEP:COMM",
    "UNH:GEN:CRYO:FRIDGE:LHE:COMM",
    "UNH:GEN:CRYO:MAG:LHE:COMM",
    "UNH:GEN:CRYO:VAC:LOW:COMM",
    "UNH:GEN:CRYO:VAC:Pump_IDN",
    "UNH:GEN:CRYO:VAC:Pump_Unit",
}


def test_extended_cryo_pvs_in_both_archivers():
    path = ROOT / "configs/target_physical/archiver/settings.yaml"
    config = yaml.safe_load(path.read_text())

    pvs = config["archiver"]["explicit_pvs"]

    assert len(pvs) == len(set(pvs)) == 168
    assert ADDITIONAL_PVS.issubset(set(pvs))

    registry, report = build_registry(root=ROOT)

    assert report["counts"]["Cryo"] == 168
    assert report["counts"]["ssRF"] == 97
    assert report["counts"]["Microwave"] == 104

    for pv in ADDITIONAL_PVS:
        assert pv in registry
        assert registry[pv]["subsystem"] == "Cryo"
