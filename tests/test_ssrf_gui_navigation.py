from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui" / "genesis_ssrf.bob"


class SsrfGuiNavigation(unittest.TestCase):

    def test_detail_displays_open_in_tabs(self):
        root = ET.parse(GUI).getroot()

        expected = {
            "genesis_ssrf_smc100a.bob",
            "genesis_ssrf_wavegen.bob",
            "genesis_ssrf_switches.bob",
            "genesis_ssrf_fitter.bob",
        }

        targets = {}

        for action in root.iter("action"):
            if action.attrib.get("type") != "open_display":
                continue

            file_node = action.find("file")
            target_node = action.find("target")

            if file_node is None:
                continue

            display = (file_node.text or "").strip()

            if display not in expected:
                continue

            target = (
                (target_node.text or "").strip()
                if target_node is not None
                else ""
            )

            targets.setdefault(display, []).append(target)

        self.assertEqual(
            set(targets),
            expected,
            msg=f"Missing expected ssRF detail navigation: {expected - set(targets)}",
        )

        for display, values in targets.items():
            with self.subTest(display=display):
                self.assertTrue(values)
                self.assertTrue(
                    all(value == "tab" for value in values),
                    msg=f"{display} has non-tab targets: {values}",
                )


if __name__ == "__main__":
    unittest.main()
