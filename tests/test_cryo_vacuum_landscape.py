"""Genesis Cryogenics Vacuum 16:9 GUI regression."""

from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

GUI = (
    Path(__file__).resolve().parents[1]
    / "gui/genesis_cryo_vacuum.bob"
)


def load():
    root = ET.parse(GUI).getroot()
    widgets = root.findall("widget")
    by_name = {
        w.findtext("name"): w
        for w in widgets
    }
    return root, widgets, by_name


def rect(w):
    return tuple(
        int(w.findtext(k))
        for k in ("x", "y", "width", "height")
    )


def test_vacuum_16_by_9_inventory_and_bounds():
    root, widgets, by_name = load()

    assert (
        int(root.findtext("width")),
        int(root.findtext("height"))
    ) == (1280, 720)

    assert len(widgets) == len(by_name) == 47

    assert Counter(w.get("type") for w in widgets) == {
        "rectangle": 6,
        "label": 18,
        "textupdate": 14,
        "led": 4,
        "action_button": 4,
        "databrowser": 1,
    }

    for name, w in by_name.items():
        x, y, width, height = rect(w)

        assert x >= 0 and y >= 0, name
        assert width > 0 and height > 0, name
        assert x + width <= 1280, name
        assert y + height <= 720, name

    assert not root.findall(
        ".//action[@type='write_pv']"
    )


def test_vacuum_preserves_manometer_and_trend():
    _, _, widgets = load()

    assert rect(widgets["Pressure_Card"]) == (
        20, 95, 1240, 260
    )
    assert rect(widgets["VacTrend_Card"]) == (
        20, 367, 1240, 275
    )

    trend = widgets["VacuumTrend"]

    assert trend.get("type") == "databrowser"
    assert rect(trend) == (40, 417, 1200, 210)
    assert trend.findtext("file") == (
        "genesis_cryo_vacuum.plt"
    )

    manometer = [
        w for name, w in widgets.items()
        if name.startswith("Genesis_Mano_")
    ]

    assert len(manometer) == 13

    expected = {
        "Genesis_Mano_0_V":
            "UNH:GEN:CRYO:VAC:LOW:Mano_PI",
        "Genesis_Mano_1_V":
            "UNH:GEN:CRYO:VAC:LOW:Mano_Status",
        "Genesis_Mano_2_V":
            "UNH:GEN:CRYO:VAC:LOW:Mano_VapT",
        "Genesis_Mano_3_V":
            "UNH:GEN:CRYO:VAC:LOW:Mano_VapPhase",
        "Genesis_Mano_4_V":
            "UNH:GEN:CRYO:VAC:LOW:Mano_VapStatus",
        "Genesis_Mano_5_V":
            "UNH:GEN:CRYO:VAC:LOW:MAN:manometer_low_time",
    }

    for name, pv in expected.items():
        assert widgets[name].findtext("pv_name") == (
            "ca://" + pv
        )


def test_vacuum_expert_and_overview_navigation():
    root, _, widgets = load()

    expected = {
        "CellExpert": "genesis_cryo_cell_expert.bob",
        "VacExpert": "genesis_cryo_vacuum_expert.bob",
        "IonExpert": "genesis_cryo_ion_expert.bob",
        "Back": "genesis_cryo.bob",
    }

    for name, destination in expected.items():
        actions = widgets[name].findall(
            ".//action[@type='open_display']"
        )

        assert len(actions) == 1
        assert actions[0].findtext("file") == destination

    assert rect(widgets["Back"]) == (
        1030, 663, 210, 34
    )

    routes = root.findall(
        ".//action[@type='open_display']"
    )

    assert len(routes) == 4
