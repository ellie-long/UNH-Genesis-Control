import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui/genesis_ssrf.bob"


class TestSsrfMainWorkflowGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = GUI.read_text()
        cls.root = ET.parse(GUI).getroot()

    def test_xml(self):
        ET.parse(GUI)

    def test_integrated_panel(self):
        self.assertIn("<name>Workflow_BurnPanel</name>", self.gui)
        self.assertIn("BURN SETUP", self.gui)

    def test_final_operator_sequence_controls(self):
        for pv in (
            "UNH:GEN:SSRF:RECIPE:GENERATE_LOAD_CMD",
            "UNH:GEN:SSRF:SEQ:ARM_CMD",
            "UNH:GEN:SSRF:SEQ:START_CMD",
            "UNH:GEN:SSRF:SEQ:ABORT_CMD",
        ):
            self.assertIn(pv, self.gui)

        for label in ("GENERATE / LOAD", "ARM", "BURN ONCE", "ABORT"):
            self.assertIn(f"<off_label>{label}</off_label>", self.gui)
            self.assertIn(f"<on_label>{label}</on_label>", self.gui)

    def test_recipe_fields_are_visible(self):
        required = (
            "RECIPE:CURRENT_POLARIZATION",
            "RECIPE:REFERENCE_POLARIZATION",
            "RECIPE:CARRIER_MHZ",
            "RECIPE:START_LEVEL_DBM",
            "RECIPE:TARGET_LEVEL_DBM",
            "RECIPE:FM_REPEATS",
            "RECIPE:FM_PERIOD_S",
            "RECIPE:SWEEP_DWELL_MS",
            "RECIPE:WAVEFORM_POINTS",
            "RECIPE:VLOW_V",
            "RECIPE:VHIGH_V",
        )
        for value in required:
            self.assertIn(value, self.gui)

    def test_no_legacy_demo_contract_in_main_operator_panel(self):
        for forbidden in (
            "DEMO_STATE",
            "DEMO_REASON",
            "NO_RF_LOCK",
            "PHYSICAL_NO_RF",
            "START_FITTER_CMD",
            "REPLAY_EQ_CMD",
            "SET_SSRF_MODE_CMD",
        ):
            self.assertNotIn(forbidden, self.gui)

    def test_all_main_gui_leds_are_circular(self):
        for widget in self.root.iter("widget"):
            if widget.attrib.get("type") == "led":
                self.assertEqual(widget.findtext("width"), widget.findtext("height"))


if __name__ == "__main__":
    unittest.main()
