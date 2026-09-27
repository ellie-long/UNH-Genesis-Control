import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
IOC = ROOT / "tools/ssrf_physical_no_rf_demo_ioc.py"
GUI = ROOT / "gui/genesis_ssrf_demo.bob"
START = ROOT / "scripts/start_ssrf_physical_no_rf_demo.sh"


class TestPhysicalNoRfDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ioc = IOC.read_text()
        cls.start = START.read_text()

    def test_gui_xml(self):
        ET.parse(GUI)

    def test_no_smc_write_authority(self):
        for forbidden in (
            ".set_rf(",
            ".set_fm(",
            ".set_level_dbm(",
            ".set_frequency_mhz(",
            ".trigger_sweep(",
            ".arm_sweep(",
        ):
            self.assertNotIn(forbidden, self.ioc)

    def test_wavegen_output_never_commanded_on(self):
        # Mentions in comments/docstrings are harmless. An actual EPICS
        # write to the physical WaveGen OUTPUT_CMD is forbidden.
        self.assertNotIn(
            '_ca_put(f"{WGEN}:OUTPUT_CMD"',
            self.ioc,
        )
        self.assertIn("OUTPUT_RBV", self.ioc)

    def test_one_shot_arm_is_required(self):
        self.assertIn('p["ARM_CMD"] = builder.boolOut(', self.ioc)
        self.assertIn('p["EXECUTION_ARMED"] = builder.boolIn(', self.ioc)
        self.assertIn(
            "RUN DEMO blocked: execution is not armed",
            self.ioc,
        )

    def test_existing_fitter_stage_runtime_reused(self):
        self.assertIn("ssrf_analysis.fitter_stage_runtime", self.ioc)
        self.assertIn("WAVEFORM_STAGE_REQ", self.ioc)

    def test_physical_keysight_load_is_allowed_but_output_off_required(self):
        self.assertIn("LOAD_CMD", self.ioc)
        self.assertIn("physical WaveGen output is ON", self.ioc)

    def test_switch_ordering(self):
        self.assertLess(self.ioc.index("nmr.set_port_mask(3)"), self.ioc.index("ssrf.set_port_mask(0)"))
        self.assertLess(self.ioc.index("ssrf.set_port_mask(3)"), self.ioc.index("nmr.set_port_mask(0)"))

    def test_start_script_requires_physical_status(self):
        self.assertIn("./scripts/genesisctl.sh physical-status", self.start)


if __name__ == "__main__":
    unittest.main()
