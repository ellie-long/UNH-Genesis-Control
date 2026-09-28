from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / 'scripts' / 'genesisctl.sh'


class GenesisCtlSsrfOperator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CTL.read_text()

    def test_help_exposes_explicit_no_rf_commands(self):
        self.assertIn('ssrf-physical-status', self.text)
        self.assertIn('ssrf-demo-start', self.text)
        self.assertIn('ssrf-demo-stop', self.text)
        self.assertIn('PHYSICAL_NO_RF', self.text)

    def test_operator_commands_map_to_validated_scripts(self):
        self.assertIn('check_ssrf_physical_readonly.sh', self.text)
        self.assertIn('start_ssrf_physical_no_rf_demo.sh', self.text)
        self.assertIn('stop_ssrf_physical_no_rf_demo.sh', self.text)

    def test_dispatch_is_explicit(self):
        self.assertIn('    ssrf-physical-status)', self.text)
        self.assertIn('    ssrf-demo-start)', self.text)
        self.assertIn('    ssrf-demo-stop)', self.text)

    def test_normal_start_and_stop_are_not_redefined(self):
        self.assertIn('    start)\n        normal_start', self.text)
        self.assertIn('    stop)\n        normal_stop', self.text)


if __name__ == '__main__':
    unittest.main()
