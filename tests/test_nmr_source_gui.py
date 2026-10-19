"""Validate the single-source PyNMR ssRF operator display."""

from pathlib import Path
import xml.etree.ElementTree as ET


GUI = Path("gui/genesis_ssrf.bob")


def _widgets():
    root = ET.parse(GUI).getroot()
    return root, {
        widget.findtext("name"): widget
        for widget in root.iter("widget")
        if widget.findtext("name")
    }


def test_obsolete_dual_source_panel_absent():
    root, widgets = _widgets()

    assert not any(
        name.startswith("NMR_Source_")
        for name in widgets
    )

    assert "GENESIS_NMR_SOURCE_STATUS_V1" not in GUI.read_text()


def test_original_display_dimensions():
    root, _ = _widgets()
    assert root.findtext("width") == "1920"
    assert root.findtext("height") == "1390"


def test_original_ssrf_controls_preserved():
    _, widgets = _widgets()

    required = {
        "Switch_Card",
        "Safety_Interlock_Card",
        "Workflow_BurnPanel",
        "WF_MaterialND3",
        "WF_MaterialButanol",
        "WF_MaterialRBV",
        "WF_Calibrate",
        "WF_OpenFitter",
    }

    assert required <= widgets.keys()


def test_material_and_calibration_pvs_preserved():
    _, widgets = _widgets()

    expected = {
        "WF_MaterialRBV": "UNH:GEN:SSRF:FIT:MATERIAL_RBV",
        "WF_Calibrate": "UNH:GEN:SSRF:FIT:UPDATE_CONSTANTS_CMD",
    }

    for name, pv in expected.items():
        assert widgets[name].findtext("pv_name") == "ca://" + pv

    for name, material in (
        ("WF_MaterialND3", "ND3"),
        ("WF_MaterialButanol", "d-butanol"),
    ):
        actions = widgets[name].find("actions")
        assert actions is not None
        assert any(
            action.findtext("pv_name")
            == "ca://UNH:GEN:SSRF:FIT:MATERIAL_CMD"
            and action.findtext("value") == material
            for action in actions.findall("action")
        )
