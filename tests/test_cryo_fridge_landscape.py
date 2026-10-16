"""Genesis Cryogenics Fridge landscape GUI contract."""

from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

GUI = (
    Path(__file__).resolve().parents[1]
    / "gui/genesis_cryo_fridge.bob"
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
        int(w.findtext(key))
        for key in ("x", "y", "width", "height")
    )


def test_fridge_landscape_canvas_and_inventory():
    root, widgets, by_name = load()

    assert (
        int(root.findtext("width")),
        int(root.findtext("height"))
    ) == (1600, 900)

    assert len(widgets) == len(by_name) == 114

    assert Counter(w.get("type") for w in widgets) == {
        "label": 57,
        "textupdate": 52,
        "databrowser": 1,
        "rectangle": 3,
        "action_button": 1,
    }

    for name, w in by_name.items():
        x, y, width, height = rect(w)

        assert x >= 0 and y >= 0, name
        assert width > 0 and height > 0, name
        assert x + width <= 1600, name
        assert y + height <= 900, name


def test_fridge_temperature_columns_and_history():
    _, _, widgets = load()

    for channel in range(1, 12):
        col = 0 if channel <= 6 else 1
        row = channel - 1 if channel <= 6 else channel - 7

        for offset, suffix in enumerate(
            ("TI", "RI", "Status", "Conn")
        ):
            y = 145 + 84 * row + 21 * offset

            for prefix, x in (
                ("L", 10 if col == 0 else 440),
                ("T", 160 if col == 0 else 590),
            ):
                name = (
                    f"{prefix}_UNH:GEN:CRYO:FRIDGE:"
                    f"F{channel}_{suffix}"
                )
                w = widgets[name]

                assert rect(w)[0] == x
                assert rect(w)[1] == y
                assert rect(w)[3] == 20

                if prefix == "T":
                    assert w.findtext("pv_name") == (
                        f"ca://UNH:GEN:CRYO:FRIDGE:"
                        f"F{channel}_{suffix}"
                    )

    plot = widgets["Trend"]

    assert plot.get("type") == "databrowser"
    assert rect(plot) == (880, 114, 710, 600)
    assert plot.findtext("file") == (
        "genesis_cryo_fridge.plt"
    )


def test_fridge_status_lhe_and_navigation():
    root, _, widgets = load()

    assert rect(widgets["Genesis_Status_Card"]) == (
        8, 760, 760, 115
    )
    assert rect(widgets["Genesis_LHe_Card"]) == (
        800, 760, 790, 115
    )

    lhe = widgets["Genesis_LHe_Title"]
    assert "inch" in (
        lhe.findtext("text") or ""
    )

    expected = {
        "Genesis_LHe_Level_V":
            "ca://UNH:GEN:CRYO:FRIDGE:LHE:Fridge_LI",
        "Genesis_LHe_Status_V":
            "ca://UNH:GEN:CRYO:FRIDGE:LHE:Fridge_Status",
        "Genesis_LHe_Heartbeat_V":
            "ca://UNH:GEN:CRYO:FRIDGE:LHE:MAN:lhe_fridge_time",
    }

    for name, pv in expected.items():
        assert widgets[name].findtext("pv_name") == pv

    back = widgets["Genesis_Back"]
    actions = back.findall(
        ".//action[@type='open_display']"
    )

    assert len(actions) == 1
    assert actions[0].findtext("file") == (
        "genesis_cryo.bob"
    )

    assert not root.findall(
        ".//action[@type='write_pv']"
    )
