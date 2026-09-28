from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "scripts" / "genesisctl.sh"


class GenesisCtlSsrfOperator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CTL.read_text()

    def test_help_exposes_single_final_physical_workflow(self):
        self.assertIn("ssrf-physical-status", self.text)
        self.assertIn("ssrf-deploy-keysight", self.text)
        self.assertIn("ssrf-start", self.text)
        self.assertIn("ssrf-stop", self.text)
        self.assertNotIn("ssrf-demo-start", self.text)
        self.assertNotIn("ssrf-demo-stop", self.text)

    def test_operator_commands_map_to_final_scripts(self):
        self.assertIn("check_ssrf_physical_readonly.sh", self.text)
        self.assertIn("deploy_ssrf_keysight_pi.sh", self.text)
        self.assertIn("start_ssrf_physical.sh", self.text)
        self.assertIn("stop_ssrf_physical.sh", self.text)

    def test_dispatch_is_explicit(self):
        self.assertIn("    ssrf-physical-status)", self.text)
        self.assertIn("    ssrf-deploy-keysight)", self.text)
        self.assertIn("    ssrf-start)", self.text)
        self.assertIn("    ssrf-stop)", self.text)

    def test_normal_start_and_stop_are_not_redefined(self):
        self.assertIn("    start)\n        normal_start", self.text)
        self.assertIn("    stop)\n        normal_stop", self.text)


if __name__ == "__main__":
    unittest.main()
