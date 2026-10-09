"""Ubuntu static regression checks for Genesis landscape visualization."""
from pathlib import Path
from xml.etree import ElementTree as ET

from archive.viewer_filters import SSRF_PRESETS, resolve_ssrf_preset

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"
SCREENS = ("genesis_ssrf_workflow_view.bob", "genesis_mw_visualization.bob")
TRENDS = (
    "genesis_ssrf_smc_frequency.plt", "genesis_ssrf_wavegen_levels.plt",
    "genesis_ssrf_rf_level.plt", "genesis_ssrf_fitter_pq.plt",
    "genesis_mw_frequency.plt", "genesis_mw_source_power.plt",
    "genesis_mw_robot_position.plt", "genesis_mw_supply_vi.plt",
)


def test_new_screens_are_read_only_16x9_and_in_bounds():
    for name in SCREENS:
        root = ET.parse(GUI / name).getroot()
        w, h = int(root.findtext("width")), int(root.findtext("height"))
        assert (w, h) == (1600, 880)
        assert not root.findall('.//action[@type="write_pv"]')
        assert not root.findall('.//widget[@type="bool_button"]')
        plots = root.findall('.//widget[@type="databrowser"]')
        assert len(plots) == 4
        for node in root.iter("widget"):
            x, y, ww, hh = (int(node.findtext(t)) for t in
                            ("x", "y", "width", "height"))
            assert x >= 0 and y >= 0
            assert x + ww <= w and y + hh <= h
        for plot in plots:
            target = GUI / plot.findtext("file")
            assert target.exists(), target
            assert ET.parse(target).getroot().tag == "databrowser"


def test_trends_have_valid_existing_trace_definitions():
    for name in TRENDS:
        root = ET.parse(GUI / name).getroot()
        assert root.tag == "databrowser"
        traces = root.findall(".//pvlist/pv")
        assert traces
        for trace in traces:
            assert trace.findtext("name")
            assert trace.findtext("axis") is not None


def test_new_navigation_links_from_main_screens():
    cases = (
        ("genesis_ssrf.bob", "Open_ssRF_Visualization",
         "genesis_ssrf_workflow_view.bob"),
        ("genesis_microwave.bob", "Open_MW_Visualization",
         "genesis_mw_visualization.bob"),
    )
    for filename, widget_name, dest in cases:
        root = ET.parse(GUI / filename).getroot()
        matches = [n for n in root.iter("widget")
                   if n.findtext("name") == widget_name]
        assert len(matches) == 1
        action = matches[0].find("./actions/action")
        assert action is not None and action.get("type") == "open_display"
        assert action.findtext("file") == dest
        assert (GUI / dest).is_file()


def test_ssrf_archive_preset_resolves_existing_pvs_only():
    assert len(SSRF_PRESETS) >= 6
    available = ["UNH:GEN:SSRF:FIT:P_FIT", "UNH:GEN:SSRF:FIT:Q_FIT"]
    resolved = resolve_ssrf_preset(available, "ssRF - Fitted P / Q")
    assert resolved == available
    assert resolve_ssrf_preset([], "ssRF - Fitted P / Q") == []
    assert resolve_ssrf_preset(available, "not a preset") == []
