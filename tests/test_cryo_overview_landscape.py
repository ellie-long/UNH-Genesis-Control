"""Permanent Genesis Cryogenics 16:9 overview contract."""

from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

GUI = (
    Path(__file__).resolve().parents[1]
    / "gui/genesis_cryo.bob"
)


def load():
    root = ET.parse(GUI).getroot()
    widgets = root.findall("widget")
    by_name = {
        w.findtext("name"): w
        for w in widgets
    }
    return root, widgets, by_name


def rect(widget):
    return tuple(
        int(widget.findtext(field))
        for field in ("x", "y", "width", "height")
    )


def test_overview_landscape_canvas_and_widget_inventory():
    root, widgets, by_name = load()

    assert (
        int(root.findtext("width")),
        int(root.findtext("height")),
    ) == (1600, 900)

    assert len(widgets) == len(by_name) == 160

    assert Counter(w.get("type") for w in widgets) == {
        "rectangle": 18,
        "label": 81,
        "textupdate": 43,
        "led": 6,
        "action_button": 12,
    }

    for name, widget in by_name.items():
        x, y, width, height = rect(widget)

        assert x >= 0 and y >= 0, name
        assert width > 0 and height > 0, name
        assert x + width <= 1600, name
        assert y + height <= 900, name

    assert not root.findall(
        ".//action[@type='write_pv']"
    )


def test_overview_cards_and_schematic_geometry():
    _, _, widgets = load()

    expected_cards = {
        "Status_Card": (20, 85, 1560, 95),
        "Magnet_Card": (20, 185, 355, 600),
        "Cryostat_Card": (395, 185, 690, 600),
        "Operations_Card": (1105, 185, 475, 600),
        "Navigation_Card": (20, 790, 1560, 110),
    }

    for name, expected in expected_cards.items():
        assert rect(widgets[name]) == expected, name

    # Schematic components retain their original dimensions.
    # Only their vertical positions changed by 15 pixels.
    schematic = {
        "MagnetCryostat": (650, 265, 185, 410),
        "TargetStick": (737, 285, 12, 325),
        "FridgeInsert": (704, 310, 77, 278),
        "MagnetCoilLeft": (662, 390, 24, 165),
        "MagnetCoilRight": (799, 390, 24, 165),
        "TargetCell": (701, 580, 84, 62),
        "PressureUnits": (425, 761, 450, 20),
    }

    for name, expected in schematic.items():
        assert rect(widgets[name]) == expected, name

    # Physical schematic stays inside the central card.
    cx, cy, cw, ch = rect(widgets["Cryostat_Card"])

    for name in schematic:
        x, y, width, height = rect(widgets[name])

        assert x >= cx and y >= cy, name
        assert x + width <= cx + cw, name
        assert y + height <= cy + ch, name


def test_overview_navigation_and_operator_note():
    root, _, widgets = load()

    expected_destinations = (
        "genesis_cryo_fridge.bob",
        "genesis_cryo_magnet.bob",
        "genesis_cryo_field.bob",
        "genesis_cryo_vacuum.bob",
        "genesis_cryo_coldboard.bob",
        "genesis_cryo_safety.bob",
        "genesis_cryo_data.bob",
        "genesis_cryo_diagnostics.bob",
        "genesis_cryo_flow.bob",
        "genesis_cryo_cell_expert.bob",
        "genesis_cryo_ion_expert.bob",
        "genesis_cryo_vacuum_expert.bob",
    )

    assert rect(widgets["Navigation_Card"]) == (
        20, 790, 1560, 110
    )

    for i, destination in enumerate(expected_destinations):
        widget = widgets[f"Nav{i}"]

        assert rect(widget) == (
            42 + 126 * i,
            820,
            120,
            34,
        )

        actions = widget.findall(
            ".//action[@type='open_display']"
        )

        assert len(actions) == 1
        assert actions[0].findtext("file") == destination

    all_routes = [
        action.findtext("file")
        for action in root.findall(
            ".//action[@type='open_display']"
        )
    ]

    assert len(all_routes) == len(set(all_routes)) == 12

    note = widgets["OperatorNote"]

    assert rect(note) == (42, 856, 1510, 42)
    assert note.get("type") == "label"

    text = note.findtext("text") or ""

    assert text.strip()
    assert "read-only" in text.lower()

    # Buttons and note must not overlap.
    for i in range(8):
        x, y, width, height = rect(widgets[f"Nav{i}"])
        assert y + height < rect(note)[1]
