from pathlib import Path
import importlib
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "commissioning" / "microwave_physical" / "vaunix_lms" / "settings.yaml"


class VaunixGenesisConfiguration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with CONFIG.open() as stream:
            cls.settings = yaml.safe_load(stream)

    def test_genesis_namespace(self):
        self.assertEqual(
            self.settings["general"]["prefix"],
            "UNH:GEN:MW:SRC",
        )

    def test_canonical_module(self):
        device = self.settings["vaunix_lms"]
        self.assertEqual(
            device["module"],
            "microwave_ioc.vaunix_lms.toolkit_device",
        )

        module = importlib.import_module(device["module"])
        self.assertTrue(hasattr(module, "Device"))

    def test_not_autostarted(self):
        self.assertFalse(
            self.settings["vaunix_lms"]["autostart"]
        )

    def test_physical_config_is_not_local_simulation(self):
        self.assertFalse(
            self.settings["vaunix_lms"]["simulation"]
        )

    def test_remote_shim_not_started_implicitly(self):
        self.assertFalse(
            self.settings["vaunix_lms"]["shim"]["start_with_ioc"]
        )

    def test_single_vaunix_channel(self):
        self.assertEqual(
            self.settings["vaunix_lms"]["channels"],
            ["MW"],
        )

    def test_bridge12_multiplier(self):
        self.assertEqual(
            self.settings["vaunix_lms"]["multiplier"],
            12.0,
        )

    def test_frequency_window_is_consistent(self):
        records = self.settings["vaunix_lms"]["records"]

        synth_min = records["MW_FC"]["DRVL"]
        synth_max = records["MW_FC"]["DRVH"]

        output_min = records["MW_OutFI"]["LOPR"]
        output_max = records["MW_OutFI"]["HOPR"]

        multiplier = self.settings["vaunix_lms"]["multiplier"]

        self.assertAlmostEqual(
            synth_min * multiplier,
            output_min,
            places=6,
        )
        self.assertAlmostEqual(
            synth_max * multiplier,
            output_max,
            places=6,
        )

    def test_current_phase1_frequency_window(self):
        records = self.settings["vaunix_lms"]["records"]

        self.assertEqual(
            records["MW_OutFI"]["LOPR"],
            138.0,
        )
        self.assertEqual(
            records["MW_OutFI"]["HOPR"],
            142.0,
        )

    def test_power_command_window(self):
        record = self.settings["vaunix_lms"]["records"]["MW_PWRC"]

        self.assertEqual(record["DRVL"], -40.0)
        self.assertEqual(record["DRVH"], 10.0)


if __name__ == "__main__":
    unittest.main()
