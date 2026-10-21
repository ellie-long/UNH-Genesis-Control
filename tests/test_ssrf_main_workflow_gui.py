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
        expected = (
            ("WF_GenerateLoad", "GENERATE / LOAD",
             "UNH:GEN:SSRF:RECIPE:GENERATE_LOAD_CMD"),
            ("WF_ARM", "ARM",
             "UNH:GEN:SSRF:SEQ:ARM_CMD"),
            ("WF_BURN", "BURN ONCE",
             "UNH:GEN:SSRF:SEQ:START_CMD"),
            ("WF_ABORT", "ABORT",
             "UNH:GEN:SSRF:SEQ:ABORT_CMD"),
        )

        widgets = {
            widget.findtext("name"): widget
            for widget in self.root.iter("widget")
        }

        for name, label, pv in expected:
            with self.subTest(control=name):
                self.assertIn(name, widgets)
                widget = widgets[name]
                self.assertEqual(widget.get("type"), "action_button")
                self.assertEqual(widget.findtext("text"), label)

                actions = widget.findall("./actions/action")
                self.assertEqual(len(actions), 1)
                action = actions[0]

                self.assertEqual(action.get("type"), "write_pv")
                self.assertEqual(
                    action.findtext("pv_name"), "ca://" + pv
                )
                self.assertEqual(action.findtext("value"), "1")

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
