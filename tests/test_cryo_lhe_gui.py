"""Static validation of the optional Cryo LHe panels."""

from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1] / "gui"

TARGETS = {
    "genesis_cryo_fridge.bob": {
        "area": (800, 760, 790, 115),
        "pvs": {
            "UNH:GEN:CRYO:FRIDGE:LHE:Fridge_LI",
            "UNH:GEN:CRYO:FRIDGE:LHE:Fridge_Status",
            "UNH:GEN:CRYO:FRIDGE:LHE:MAN:lhe_fridge_time",
        },
    },
    "genesis_cryo_magnet.bob": {
        "area": (800, 631, 590, 95),
        "pvs": {
            "UNH:GEN:CRYO:MAG:LHE:Magnet_LI",
            "UNH:GEN:CRYO:MAG:LHE:Magnet_Status",
            "UNH:GEN:CRYO:MAG:LHE:MAN:lhe_magnet_time",
        },
    },
}


def test_lhe_panel_pv_bindings():
    for filename, cfg in TARGETS.items():
        root = ET.parse(ROOT / filename).getroot()

        widgets = [
            w for w in root.findall("widget")
            if (w.findtext("name") or "").startswith("Genesis_LHe_")
        ]

        assert len(widgets) == 9

        pvs = {
            w.findtext("pv_name").removeprefix("ca://")
            for w in widgets
            if w.findtext("pv_name")
        }

        assert pvs == cfg["pvs"]

        assert not any(
            w.get("type") in (
                "action_button", "bool_button", "textentry"
            )
            for w in widgets
        )

        title = next(
            w for w in widgets
            if w.findtext("name") == "Genesis_LHe_Title"
        )

        assert "inch" in (
            title.findtext("text") or ""
        )


def test_lhe_panel_geometry():
    for filename, cfg in TARGETS.items():
        root = ET.parse(ROOT / filename).getroot()

        x, y, width, height = cfg["area"]

        widgets = [
            w for w in root.findall("widget")
            if (w.findtext("name") or "").startswith("Genesis_LHe_")
        ]

        assert x + width <= int(root.findtext("width"))
        assert y + height <= int(root.findtext("height"))

        for w in widgets:
            wx = int(w.findtext("x"))
            wy = int(w.findtext("y"))
            ww = int(w.findtext("width"))
            wh = int(w.findtext("height"))

            assert x <= wx
            assert y <= wy
            assert wx + ww <= x + width
            assert wy + wh <= y + height

        # The LHe background may contain its own readback widgets.
        # No unrelated pre-existing widget may overlap the panel.
        for w in root.findall("widget"):
            name = w.findtext("name") or ""

            if name.startswith("Genesis_LHe_"):
                continue

            wx = int(w.findtext("x", "0"))
            wy = int(w.findtext("y", "0"))
            ww = int(w.findtext("width", "0"))
            wh = int(w.findtext("height", "0"))

            overlap = (
                wx < x + width and wx + ww > x
                and wy < y + height and wy + wh > y
            )

            assert not overlap, (filename, name)
