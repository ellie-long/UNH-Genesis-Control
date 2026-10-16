"""Static operator checks for the Genesis Cryo flowmeters."""

from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "gui/genesis_cryo_flow.bob"
DATA = ROOT / "gui/genesis_cryo_data.bob"

DEVICES = (
    ("flowmeter_pi", "RPump"),
    ("flowmeter_left", "LPump"),
    ("flowmeter_sep", "SepFlow"),
)


def test_flow_gui_exact_readbacks():
    root = ET.parse(FLOW).getroot()

    assert int(root.findtext("width")) == 1280
    assert int(root.findtext("height")) == 720

    manager = yaml.safe_load((
        ROOT / "configs/target_physical/ioc_manager/settings.yaml"
    ).read_text())

    expected = set()

    for name, channel in DEVICES:
        profile = yaml.safe_load((
            ROOT / f"configs/target_physical/{name}/settings.yaml"
        ).read_text())
        prefix = profile["general"]["prefix"]
        device = profile[name]

        assert device["autostart"] is False
        assert device["write_enabled"] is False

        expected.update({
            f"ca://{prefix}:{channel}_SLM",
            f"ca://{prefix}:{channel}_Status",
            f"ca://{manager[name]['heartbeat_pv']}",
        })

    actual = {
        w.findtext("pv_name")
        for w in root.findall("widget")
        if w.findtext("pv_name")
    }

    assert len(expected) == len(actual) == 9
    assert actual == expected

    assert not root.findall(".//action[@type='write_pv']")

    for w in root.findall(".//widget[@type='textupdate']"):
        assert w.findtext("format") != "6"


def test_flow_gui_geometry_and_navigation():
    root = ET.parse(FLOW).getroot()

    names = [
        w.findtext("name") for w in root.findall("widget")
    ]
    assert len(names) == len(set(names))

    for w in root.findall("widget"):
        x = int(w.findtext("x"))
        y = int(w.findtext("y"))
        width = int(w.findtext("width"))
        height = int(w.findtext("height"))

        assert x >= 0 and y >= 0
        assert x + width <= 1280
        assert y + height <= 720

    destinations = sorted(
        action.findtext("file")
        for action in root.findall(
            ".//action[@type='open_display']"
        )
    )

    assert destinations == [
        "genesis_cryo.bob",
        "genesis_cryo_data.bob",
    ]


def test_data_page_has_one_flow_route():
    data = ET.parse(DATA).getroot()
    flow_links = [
        w for w in data.findall("widget")
        if w.findtext("name") == "FlowTrendLink"
    ]

    assert len(flow_links) == 1

    actions = flow_links[0].findall(
        ".//action[@type='open_display']"
    )

    assert len(actions) == 1
    assert actions[0].findtext("file") == "genesis_cryo_flow.bob"

    overview = ET.parse(
        ROOT / "gui/genesis_cryo.bob"
    ).getroot()

    # All eight original routes and four added routes must be present.
    routes = [
        a.findtext("file")
        for a in overview.findall(
            ".//action[@type='open_display']"
        )
    ]

    expected_routes = {
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
    }

    assert len(routes) == len(set(routes)) == 12
    assert set(routes) == expected_routes
    assert "genesis_cryo_flow.bob" in routes
