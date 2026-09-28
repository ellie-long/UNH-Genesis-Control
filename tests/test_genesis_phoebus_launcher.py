from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GenesisPhoebusLauncher(unittest.TestCase):
    def test_shared_launcher_is_two_stage(self):
        text = (ROOT / "scripts/start_genesis_phoebus.sh").read_text()
        self.assertIn('-server "${PHOEBUS_SERVER_PORT}"', text)
        self.assertIn('-settings "${SETTINGS_FILE}"', text)
        self.assertIn("-clean", text)
        self.assertIn('-resource "${RESOURCE_FILE}"', text)

        clean_index = text.index("-clean")
        resource_index = text.rindex('-resource "${RESOURCE_FILE}"')
        self.assertLess(clean_index, resource_index)

        forward = text[clean_index:resource_index + 100]
        self.assertNotIn('-settings "${SETTINGS_FILE}"', forward)

    def test_normal_startup_uses_shared_launcher(self):
        text = (ROOT / "scripts/start_genesis_mac.sh").read_text()
        self.assertIn("scripts/start_genesis_phoebus.sh", text)

    def test_physical_demo_uses_shared_launcher(self):
        text = (
            ROOT / "scripts/start_ssrf_physical_no_rf_demo.sh"
        ).read_text()
        self.assertIn("scripts/start_genesis_phoebus.sh", text)

    def test_ssrf_detail_navigation_uses_tabs(self):
        text = (ROOT / "gui/genesis_ssrf.bob").read_text()
        self.assertNotIn("<target>window</target>", text)
        self.assertIn("<target>tab</target>", text)


if __name__ == "__main__":
    unittest.main()
