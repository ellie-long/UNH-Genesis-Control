"""Static acceptance of actual Cryo IOC connectivity and new magnet temperature history."""
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"


def test_cryo_navigation_complete():
    screens = list(GUI.glob("genesis_cryo*.bob"))
    assert len(screens) == 13
    for page in screens:
        root = ET.parse(page).getroot()
        for action in root.findall(".//action[@type='open_display']"):
            target = action.findtext("file")
            assert target and (GUI / target).is_file(), (page, target)


def test_cryo_connection_indicators_bind_to_produced_pvs():
    areas = {"CELL", "FIELD", "FRIDGE", "ION", "MAG", "VAC"}
    used = set()
    for page in GUI.glob("genesis_cryo*.bob"):
        root = ET.parse(page).getroot()
        for widget in root.iter("widget"):
            pv = widget.findtext("pv_name", "").replace("ca://", "")
            if not pv.startswith("UNH:GEN:CRYO:"):
                continue
            parts = pv.split(":")
            if len(parts) >= 5 and parts[3] in areas and parts[4].endswith("_Conn"):
                raise AssertionError((page.name, pv, "nonexistent _Conn PV"))
            if len(parts) >= 5 and parts[3] in areas and parts[4] == "COMM":
                used.add(parts[3])
    assert used == areas
    # Coldboard_Conn is intentionally retained: the SPD driver creates it.
    cold = ET.parse(GUI / "genesis_cryo_coldboard.bob").getroot()
    assert any((w.findtext("pv_name") or "").endswith("Coldboard_Conn")
               for w in cold.iter("widget"))


def test_magnet_ccx_channels_in_gui_plot_and_archive():
    archive = yaml.safe_load((ROOT / "configs/target_physical/archiver/settings.yaml").read_text())
    pvs = archive["archiver"]["explicit_pvs"]
    assert len(pvs) == len(set(pvs)) == 168
    magnet = ET.parse(GUI / "genesis_cryo_magnet.bob").getroot()
    visible = {w.findtext("pv_name") for w in magnet.iter("widget")}
    plot = ET.parse(GUI / "genesis_cryo_magnet.plt").getroot()
    traces = {x.text for x in plot.findall(".//pv/name")}
    for ch in ("T1", "T3"):
        prefix = f"UNH:GEN:CRYO:MAG:{ch}_"
        for suffix in ("TI", "RI", "Status"):
            assert "ca://" + prefix + suffix in visible
            assert prefix + suffix in pvs
        assert prefix + "TI" in traces
    for area in ("CELL", "FIELD", "FRIDGE", "ION", "MAG", "VAC"):
        assert f"UNH:GEN:CRYO:{area}:COMM" in pvs
    assert "UNH:GEN:CRYO:COLDBOARD:Coldboard_Conn" in pvs
    assert not any(p.endswith("_Conn") and "COLDBOARD" not in p for p in pvs)
