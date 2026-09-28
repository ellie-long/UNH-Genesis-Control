import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui/genesis_ssrf.bob"
BACKEND = ROOT / "tools/ssrf_physical_no_rf_demo_ioc.py"

class TestSsrfMainWorkflowGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = GUI.read_text()
        cls.backend = BACKEND.read_text()
        cls.root = ET.parse(GUI).getroot()

    def test_xml(self):
        ET.parse(GUI)

    def test_integrated_panel(self):
        self.assertIn("<name>Workflow_BurnPanel</name>", self.gui)

    def test_operator_commands_use_sequencer_boundary(self):
        for pv in ("UNH:GEN:SSRF:SEQ:ARM_CMD", "UNH:GEN:SSRF:SEQ:DISARM_CMD", "UNH:GEN:SSRF:SEQ:START_CMD", "UNH:GEN:SSRF:SEQ:ABORT_CMD"):
            self.assertIn(pv, self.gui)

    def test_command_buttons_are_explicitly_named(self):
        for label in ("START FITTER", "REPLAY EQ", "CALIBRATE", "ssRF MODE", "ARM", "DISARM", "BLAST / BURN", "ABORT"):
            self.assertIn(f"<off_label>{label}</off_label>", self.gui)
            self.assertIn(f"<on_label>{label}</on_label>", self.gui)

    def test_all_main_gui_leds_are_circular(self):
        for widget in self.root.iter("widget"):
            if widget.attrib.get("type") == "led":
                self.assertEqual(widget.findtext("width"), widget.findtext("height"))

    def test_indicator_semantics_and_profile_note_are_documented(self):
        self.assertIn("<name>WF_ColorLegend</name>", self.gui)
        self.assertIn("GREEN = ready/safe", self.gui)
        self.assertIn("<name>WF_ProfileNote</name>", self.gui)
        self.assertIn("normal-production device/detail PVs may be magenta", self.gui)

    def test_commissioning_backend_exposes_start(self):
        self.assertIn('p["START_CMD"] = builder.boolOut(', self.backend)
        self.assertIn("def blast_workflow(self):", self.backend)

    def test_no_rf_authority_added(self):
        for forbidden in (".set_rf(", ".set_fm(", ".set_level_dbm("):
            self.assertNotIn(forbidden, self.backend)

if __name__ == "__main__":
    unittest.main()
