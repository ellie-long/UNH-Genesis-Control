import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
IOC = ROOT / "tools/ssrf_physical_no_rf_demo_ioc.py"
GUI = ROOT / "gui/genesis_ssrf.bob"
START = ROOT / "scripts/start_ssrf_physical_no_rf_demo.sh"
STOP = ROOT / "scripts/stop_ssrf_physical_no_rf_demo.sh"

class TestPhysicalNoRfDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ioc = IOC.read_text()
        cls.start = START.read_text()
        cls.stop = STOP.read_text()

    def test_gui_xml(self):
        ET.parse(GUI)

    def test_no_smc_write_authority(self):
        for forbidden in (".set_rf(", ".set_fm(", ".set_level_dbm(", ".set_frequency_mhz(", ".trigger_sweep(", ".arm_sweep("):
            self.assertNotIn(forbidden, self.ioc)

    def test_wavegen_output_never_commanded_on(self):
        self.assertNotIn('_ca_put(f"{WGEN}:OUTPUT_CMD"', self.ioc)
        self.assertIn("OUTPUT_RBV", self.ioc)

    def test_one_shot_arm_is_required(self):
        self.assertIn('p["ARM_CMD"] = builder.boolOut(', self.ioc)
        self.assertIn('p["EXECUTION_ARMED"] = builder.boolIn(', self.ioc)
        self.assertIn("RUN DEMO blocked: execution is not armed", self.ioc)

    def test_existing_fitter_stage_runtime_reused(self):
        self.assertIn("ssrf_analysis.fitter_stage_runtime", self.ioc)
        self.assertIn("WAVEFORM_STAGE_REQ", self.ioc)

    def test_physical_keysight_load_is_allowed_but_output_off_required(self):
        self.assertIn("LOAD_CMD", self.ioc)
        self.assertIn("physical WaveGen output is ON", self.ioc)

    def test_switch_ordering(self):
        self.assertLess(self.ioc.index("nmr.set_port_mask(3)"), self.ioc.index("ssrf.set_port_mask(0)"))
        self.assertLess(self.ioc.index("ssrf.set_port_mask(3)"), self.ioc.index("nmr.set_port_mask(0)"))

    def test_start_script_requires_canonical_physical_status(self):
        self.assertIn("./scripts/genesisctl.sh ssrf-physical-status", self.start)

    def test_start_removes_exact_stale_upload_ioc(self):
        self.assertIn("CLEAN STALE PHYSICAL KEYSIGHT IOC", self.start)
        self.assertIn('pkill -TERM -f "$PATTERN"', self.start)
        self.assertIn("expected exactly one physical Keysight IOC", self.start)

    def test_start_verifies_physical_ioc_comm_and_output_off(self):
        for token in ('"COMM"', '"WRITE_ENABLED"', '"OUTPUT_RBV"', "physical Keysight upload IOC ready"):
            self.assertIn(token, self.start)

    def test_stop_handles_both_known_screen_names_and_orphan_process(self):
        self.assertIn("genesis_ssrf_demo_wgen", self.stop)
        self.assertIn("genesis_keysight_upload", self.stop)
        self.assertIn('pkill -TERM -f "$PATTERN"', self.stop)
        self.assertIn('pkill -KILL -f "$PATTERN"', self.stop)

if __name__ == "__main__":
    unittest.main()
