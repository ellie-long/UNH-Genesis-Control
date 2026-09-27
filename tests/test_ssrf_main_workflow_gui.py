import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui" / "genesis_ssrf.bob"
BACKEND = ROOT / "tools" / "ssrf_physical_no_rf_demo_ioc.py"

class TestSsrfMainWorkflowGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = GUI.read_text()
        cls.backend = BACKEND.read_text()

    def test_xml(self):
        ET.parse(GUI)

    def test_integrated_panel(self):
        self.assertIn("<name>Workflow_BurnPanel</name>", self.gui)

    def test_operator_commands_use_sequencer_boundary(self):
        for pv in (
            "UNH:GEN:SSRF:SEQ:ARM_CMD",
            "UNH:GEN:SSRF:SEQ:DISARM_CMD",
            "UNH:GEN:SSRF:SEQ:START_CMD",
            "UNH:GEN:SSRF:SEQ:ABORT_CMD",
        ):
            self.assertIn(pv, self.gui)

    def test_commissioning_backend_exposes_start(self):
        self.assertIn('p["START_CMD"] = builder.boolOut(', self.backend)
        self.assertIn("def blast_workflow(self):", self.backend)

    def test_no_rf_authority_added(self):
        for forbidden in (".set_rf(", ".set_fm(", ".set_level_dbm("):
            self.assertNotIn(forbidden, self.backend)

if __name__ == "__main__":
    unittest.main()
