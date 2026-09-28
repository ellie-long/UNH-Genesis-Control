from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI_DIR = ROOT / "gui"


class GenesisGuiNavigation(unittest.TestCase):
    def test_all_genesis_open_display_actions_use_tabs(self):
        files = sorted(GUI_DIR.glob("genesis_*.bob"))
        self.assertTrue(files)

        count = 0

        for path in files:
            root = ET.parse(path).getroot()

            for action in root.iter("action"):
                if action.attrib.get("type") != "open_display":
                    continue

                count += 1
                target = (action.findtext("target") or "").strip()
                destination = (action.findtext("file") or "").strip()

                self.assertEqual(
                    target,
                    "tab",
                    f"{path.name}: {destination} must open in a tab",
                )

        self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()
