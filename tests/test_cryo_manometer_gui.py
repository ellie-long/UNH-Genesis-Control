"""Static tests for the optional low-pressure manometer GUI."""

from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui/genesis_cryo_vacuum.bob"

PREFIX = "UNH:GEN:CRYO:VAC:LOW"

SUFFIXES = (
    "Mano_PI",
    "Mano_Status",
    "Mano_VapT",
    "Mano_VapPhase",
    "Mano_VapStatus",
    "MAN:manometer_low_time",
)

AREA = (895, 134, 250, 203)


def manometer_widgets():
    root = ET.parse(GUI).getroot()
    widgets = [
        w for w in root.findall("widget")
        if (w.findtext("name") or "").startswith("Genesis_Mano_")
    ]
    return root, widgets


def test_manometer_exact_read_only_pvs():
    _, widgets = manometer_widgets()

    assert len(widgets) == 13

    actual = {
        w.findtext("pv_name")
        for w in widgets
        if w.findtext("pv_name")
    }

    expected = {
        f"ca://{PREFIX}:{suffix}"
        for suffix in SUFFIXES
    }

    assert actual == expected

    assert not any(
        w.get("type") in {
            "action_button", "bool_button", "textentry"
        }
        for w in widgets
    )


def test_manometer_panel_geometry():
    root, widgets = manometer_widgets()
    x, y, width, height = AREA

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

    backgrounds = {
        "Page", "Header", "Pressure_Card", "Pressure_Header"
    }

    for w in root.findall("widget"):
        name = w.findtext("name") or ""

        if name.startswith("Genesis_Mano_") or name in backgrounds:
            continue

        wx = int(w.findtext("x", "0"))
        wy = int(w.findtext("y", "0"))
        ww = int(w.findtext("width", "0"))
        wh = int(w.findtext("height", "0"))

        overlap = (
            wx < x + width and wx + ww > x
            and wy < y + height and wy + wh > y
        )

        assert not overlap, name


def test_manometer_gui_units_and_conversion_status():
    _, widgets = manometer_widgets()
    labels = {
        w.findtext("text")
        for w in widgets
        if w.get("type") == "label"
    }

    assert "Pressure (Torr)" in labels
    assert "Vapor T (K)" in labels
    assert "Conv. status" in labels
    assert "IOC heartbeat" in labels

    profile = yaml.safe_load((
        ROOT / "configs/target_physical/manometer_low/settings.yaml"
    ).read_text())

    device = profile["manometer_low"]

    assert device["pressure_unit"] == "Torr"
    assert device["vapor_pressure"] is True
    assert device["write_enabled"] is False
    assert device["autostart"] is False
