from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]

ARCHIVER = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_sim"
    / "archiver"
    / "settings.yaml"
)

MANAGER = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_sim"
    / "ioc_manager"
    / "settings.yaml"
)


def load(path):
    with path.open() as stream:
        return yaml.safe_load(stream)


class MicrowaveArchiverConfig(unittest.TestCase):

    def test_uses_microwave_sim_manager(self):
        cfg = load(ARCHIVER)

        self.assertEqual(
            cfg["archiver"]["settings_file"],
            "configs/commissioning/"
            "microwave_sim/ioc_manager/settings.yaml",
        )

    def test_same_genesis_archive_policy(self):
        cfg = load(ARCHIVER)

        self.assertEqual(
            cfg["general"]["prefix"],
            "UNH:GEN",
        )

        self.assertEqual(
            cfg["archiver"]["archive_path"],
            "archive/data",
        )

        self.assertEqual(
            cfg["archiver"]["deadband"],
            0.01,
        )

        self.assertEqual(
            cfg["archiver"]["time_increment"],
            300,
        )

    def test_all_microwave_sim_iocs_discoverable(self):
        cfg = load(MANAGER)

        for name in (
            "mw_vaunix_lms",
            "mw_supervisor",
            "mw_pcl602",
        ):
            self.assertIn(
                name,
                cfg,
            )

    def test_required_archive_records_present(self):
        manager = load(MANAGER)

        required = {
            "mw_vaunix_lms":
                "MW_FI",
            "mw_supervisor":
                "OutputFrequencyRBV",
            "mw_pcl602":
                "SPEED_MODE",
        }

        for name, record in required.items():
            settings_folder = (
                ROOT
                / manager[name]["settings_folder"]
            )

            device_settings = load(
                settings_folder / "settings.yaml"
            )

            device_name = next(
                key
                for key in device_settings
                if key != "general"
            )

            self.assertIn(
                record,
                device_settings[
                    device_name
                ]["archive_records"],
            )


if __name__ == "__main__":
    unittest.main()
