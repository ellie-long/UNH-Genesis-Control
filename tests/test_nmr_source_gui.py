"""Validate read-only Genesis NMR source panel."""

from pathlib import Path
import xml.etree.ElementTree as ET


GUI = Path("gui/genesis_ssrf.bob")

EXPECTED = {
    "NMR_Source_Protected": "NMR_PROTECTED",
    "NMR_Source_Selected_V": "NMR_SOURCE_RBV",
    "NMR_Source_Physical_V": "NMR_PHYSICAL_RBV",
    "NMR_Source_Route_LED": "NMR_ROUTE_OK",
    "NMR_Source_Stream_LED": "NMR_STREAM_OK",
    "NMR_Source_Eligible_LED": "NMR_FIT_ELIGIBLE",
    "NMR_Source_Status_V": "NMR_STATUS",
}


def test_source_status_pvs_are_display_only():
    root = ET.parse(GUI).getroot()
    widgets = list(root.iter("widget"))

    by_name = {}

    for widget in widgets:
        name = widget.findtext("name")
        if name and name.startswith("NMR_Source_"):
            assert name not in by_name
            by_name[name] = widget

    assert len(by_name) == 14

    for name, pv in EXPECTED.items():
        widget = by_name[name]
        assert widget.get("type") in ("led", "textupdate")
        assert widget.findtext("pv_name") == (
            "ca://UNH:GEN:SSRF:FIT:" + pv
        )
        assert widget.find("actions") is None

    assert all(
        w.get("type") in ("label", "led", "textupdate")
        for w in by_name.values()
    )


def test_original_display_dimensions():
    root = ET.parse(GUI).getroot()
    assert root.findtext("width") == "1920"
    assert root.findtext("height") == "1390"


def test_nmr_panel_stays_inside_display():
    root = ET.parse(GUI).getroot()

    for widget in root.iter("widget"):
        name = widget.findtext("name") or ""
        if not name.startswith("NMR_Source_"):
            continue

        x, y, w, h = (
            int(widget.findtext(key))
            for key in ("x", "y", "width", "height")
        )

        assert x >= 0 and y >= 0
        assert x + w <= 1920
        assert y + h <= 1390
