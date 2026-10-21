"""Static 16:9 Data/Trends Phoebus layout regression."""

from pathlib import Path
import xml.etree.ElementTree as ET

GUI = (
    Path(__file__).resolve().parents[1]
    / "gui/genesis_cryo_data.bob"
)


def load():
    root = ET.parse(GUI).getroot()
    widgets = {
        w.findtext("name"): w
        for w in root.findall("widget")
    }
    return root, widgets


def geometry(widget):
    return tuple(
        int(widget.findtext(key))
        for key in ("x", "y", "width", "height")
    )


def test_data_screen_is_16_by_9_and_in_bounds():
    root, widgets = load()

    assert (
        int(root.findtext("width")),
        int(root.findtext("height"))
    ) == (1280, 720)

    assert len(root.findall("widget")) == 25
    assert len(widgets) == 25

    for name, w in widgets.items():
        x, y, width, height = geometry(w)

        assert x >= 0 and y >= 0, name
        assert x + width <= 1280, name
        assert y + height <= 720, name


def test_data_screen_retains_full_plots_and_archive():
    _, widgets = load()

    assert geometry(widgets["Archive_Card"]) == (
        20, 86, 1240, 140
    )
    assert geometry(widgets["Trend_Card"]) == (
        20, 242, 1240, 393
    )
    assert geometry(widgets["FridgeTrend"]) == (
        40, 292, 585, 318
    )
    assert geometry(widgets["VacuumTrend"]) == (
        650, 292, 585, 318
    )

    assert widgets["FridgeTrend"].findtext("file") == (
        "genesis_cryo_fridge.plt"
    )
    assert widgets["VacuumTrend"].findtext("file") == (
        "genesis_cryo_vacuum.plt"
    )

    assert widgets["ArchiveEnable"].get("type") == (
        "bool_button"
    )


def test_data_navigation_remains_accessible():
    _, widgets = load()

    expected = {
        "MagTrend": "genesis_cryo_magnet.bob",
        "FieldTrend": "genesis_cryo_field.bob",
        "FlowTrendLink": "genesis_cryo_flow.bob",
        "Back": "genesis_cryo.bob",
    }

    for name, destination in expected.items():
        w = widgets[name]
        assert geometry(w)[1] == 655

        actions = w.findall(
            ".//action[@type='open_display']"
        )
        assert len(actions) == 1
        assert actions[0].findtext("file") == destination

    buttons = sorted(
        (geometry(w)[0], geometry(w)[2])
        for name, w in widgets.items()
        if name in expected
    )

    for (x, width), (next_x, _) in zip(
        buttons, buttons[1:]
    ):
        assert x + width <= next_x
