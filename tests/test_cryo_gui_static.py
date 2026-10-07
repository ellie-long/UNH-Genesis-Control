from pathlib import Path
import xml.etree.ElementTree as ET
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"


EXPECTED = {
    "genesis_cryo.bob",
    "genesis_cryo_fridge.bob",
    "genesis_cryo_magnet.bob",
    "genesis_cryo_vacuum.bob",
    "genesis_cryo_cell_expert.bob",
    "genesis_cryo_vacuum_expert.bob",
    "genesis_cryo_ion_expert.bob",
    "genesis_cryo_field.bob",
    "genesis_cryo_coldboard.bob",
    "genesis_cryo_safety.bob",
    "genesis_cryo_data.bob",
    "genesis_cryo_diagnostics.bob",
}


class CryoGuiContract(unittest.TestCase):

    def test_expected_operator_hierarchy_exists(self):
        for name in EXPECTED:
            self.assertTrue(
                (GUI / name).is_file(),
                name,
            )

    def test_every_bob_parses(self):
        for path in GUI.glob(
            "genesis_cryo*.bob"
        ):
            ET.parse(path)

    def test_main_overview_is_read_only(self):
        root = ET.parse(
            GUI / "genesis_cryo.bob"
        ).getroot()

        self.assertFalse(
            root.findall(
                ".//action[@type='write_pv']"
            )
        )

    def test_cryo_pv_ownership(self):
        for path in GUI.glob(
            "genesis_cryo*.bob"
        ):
            root = ET.parse(path).getroot()

            for node in root.iter(
                "pv_name"
            ):
                pv = (
                    node.text or ""
                ).strip()

                if not pv:
                    continue

                if pv.startswith(
                    "ca://"
                ):
                    pv = pv[5:]

                self.assertTrue(
                    pv.startswith(
                        "UNH:GEN:CRYO:"
                    ),
                    f"{path.name}: {pv}",
                )

    def test_no_rf_nmr_microwave_leak(self):
        forbidden = (
            "UNH:GEN:SSRF",
            "UNH:GEN:MW",
            "Burn_",
            "rf_chain.bob",
            "burn.bob",
        )

        for path in GUI.glob(
            "genesis_cryo*.bob"
        ):
            text = path.read_text()

            for token in forbidden:
                self.assertNotIn(
                    token,
                    text,
                    path.name,
                )

    def test_navigation_targets_exist(self):
        for path in GUI.glob(
            "genesis_cryo*.bob"
        ):
            root = ET.parse(path).getroot()

            for action in root.findall(
                ".//action[@type='open_display']"
            ):
                target = (
                    action.findtext(
                        "file",
                        "",
                    )
                    .strip()
                )

                if target.startswith(
                    "genesis_cryo"
                ):
                    self.assertTrue(
                        (GUI / target).exists(),
                        f"{path.name} -> {target}",
                    )


if __name__ == "__main__":
    unittest.main()
