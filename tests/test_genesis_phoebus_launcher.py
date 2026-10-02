from __future__ import annotations
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class GenesisPhoebusLauncher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = (ROOT / "scripts/start_genesis_phoebus.sh").read_text()

    def test_shared_launcher_is_two_stage(self):
        text = self.launcher
        self.assertIn('-server "${PHOEBUS_SERVER_PORT}"', text)
        self.assertIn('-settings "${SETTINGS_FILE}"', text)
        self.assertIn("-clean", text)
        self.assertIn('-resource "${RESOURCE_FILE}"', text)
        self.assertLess(text.index("-clean"), text.rindex('-resource "${RESOURCE_FILE}"'))

    def test_resource_forward_does_not_repeat_settings(self):
        forward = self.launcher[self.launcher.index("forward_resource_once()"):]
        self.assertNotIn('-settings "${SETTINGS_FILE}"', forward)

    def test_fresh_server_has_ui_settle_delay(self):
        self.assertIn("PHOEBUS_UI_SETTLE_SECONDS", self.launcher)
        self.assertIn('sleep "${PHOEBUS_UI_SETTLE_SECONDS}"', self.launcher)

    def test_resource_handler_race_is_detected_and_retried(self):
        self.assertIn("No argument handler installed to handle [-resource", self.launcher)
        self.assertIn("PHOEBUS_RESOURCE_RETRIES", self.launcher)
        self.assertIn("Phoebus resource handler not ready", self.launcher)

    def test_normal_startup_uses_shared_launcher(self):
        self.assertIn("scripts/start_genesis_phoebus.sh", (ROOT / "scripts/start_genesis_mac.sh").read_text())

    def test_ssrf_detail_navigation_uses_tabs(self):
        text = (ROOT / "gui/genesis_ssrf.bob").read_text()
        self.assertNotIn("<target>window</target>", text)
        self.assertIn("<target>tab</target>", text)

if __name__ == "__main__":
    unittest.main()
