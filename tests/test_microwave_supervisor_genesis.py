from pathlib import Path
import unittest

import yaml

from logic_devices.microwave_supervisor.conversions import (
    FrequencyConversion,
)
from logic_devices.microwave_supervisor.interlocks import (
    rf_on_permissive,
)
from logic_devices.microwave_supervisor.magic import (
    configured_magic,
)
from logic_devices.microwave_supervisor.sweep import (
    sweep_points,
    validate_dwell,
)


ROOT = Path(__file__).resolve().parents[1]

CONFIG = (
    ROOT
    / "configs"
    / "microwave"
    / "supervisor"
    / "settings.yaml"
)


class GenesisSupervisorConfig(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with CONFIG.open() as stream:
            cls.cfg = yaml.safe_load(stream)

        cls.dev = cls.cfg["microwave_supervisor"]

    def test_namespace(self):
        self.assertEqual(
            self.cfg["general"]["prefix"],
            "UNH:GEN:MW:SUP",
        )

        self.assertEqual(
            self.dev["record_prefix"],
            "",
        )

    def test_simulation_is_default(self):
        self.assertTrue(
            self.dev["simulation"]
        )

    def test_genesis_uca_mapping(self):
        src = self.dev["source_pvs"]

        self.assertEqual(
            src["uca_connected"],
            "UNH:GEN:MW:PS:COMM",
        )
        self.assertEqual(
            src["uca_voltage"],
            "UNH:GEN:MW:PS:VMEAS",
        )

    def test_genesis_vaunix_mapping(self):
        src = self.dev["source_pvs"]

        self.assertEqual(
            src["vaunix_connected"],
            "UNH:GEN:MW:SRC:MW_Connected",
        )
        self.assertEqual(
            src["vaunix_rf"],
            "UNH:GEN:MW:SRC:MW_RF_CMD",
        )
        self.assertEqual(
            src["vaunix_frequency_command"],
            "UNH:GEN:MW:SRC:MW_FC",
        )
        self.assertEqual(
            src["vaunix_frequency_readback"],
            "UNH:GEN:MW:SRC:MW_FI",
        )

    def test_phase1_frequency_range(self):
        freq = self.dev["frequency"]

        self.assertEqual(freq["multiplier"], 12.0)
        self.assertEqual(freq["output_min_ghz"], 138.0)
        self.assertEqual(freq["output_max_ghz"], 142.0)

    def test_magic_constants_are_not_invented(self):
        self.assertEqual(
            self.dev["dopants"],
            [],
        )

    def test_supervisor_device_initializes_frequency_command(self):
        source = (
            ROOT
            / "logic_devices"
            / "microwave_supervisor"
            / "device.py"
        ).read_text()

        self.assertIn(
            'p["OutputFrequency"].set(low, process=False)',
            source,
        )


class GenesisSupervisorLogic(unittest.TestCase):

    def setUp(self):
        self.conversion = FrequencyConversion(
            multiplier=12,
            output_min_ghz=138,
            output_max_ghz=142,
        )

    def test_frequency_conversion(self):
        self.assertAlmostEqual(
            self.conversion.to_vaunix(140),
            11.666666666666666,
        )

        self.assertAlmostEqual(
            self.conversion.from_vaunix(11.5),
            138.0,
        )

    def test_output_range_is_enforced(self):
        with self.assertRaises(ValueError):
            self.conversion.validate_output(137.99)

        with self.assertRaises(ValueError):
            self.conversion.validate_output(142.01)

    def test_rf_permissive_good_state(self):
        permitted, message = rf_on_permissive(
            True,
            True,
            5.0,
            5.0,
            0.10,
        )

        self.assertTrue(permitted)
        self.assertEqual(
            message,
            "RF ON permitted",
        )

    def test_rf_permissive_blocks_disconnected_vaunix(self):
        permitted, message = rf_on_permissive(
            True,
            False,
            5.0,
            5.0,
            0.10,
        )

        self.assertFalse(permitted)
        self.assertIn(
            "Vaunix disconnected",
            message,
        )

    def test_rf_permissive_blocks_disconnected_uca(self):
        permitted, message = rf_on_permissive(
            False,
            True,
            5.0,
            5.0,
            0.10,
        )

        self.assertFalse(permitted)
        self.assertIn(
            "UCA",
            message,
        )

    def test_rf_permissive_blocks_bad_voltage(self):
        permitted, _ = rf_on_permissive(
            True,
            True,
            4.8,
            5.0,
            0.10,
        )

        self.assertFalse(permitted)

    def test_sweep_includes_requested_stop(self):
        points = sweep_points(
            138.0,
            138.01,
            0.004,
        )

        self.assertEqual(points[0], 138.0)
        self.assertEqual(points[-1], 138.01)

    def test_descending_sweep(self):
        points = sweep_points(
            140.0,
            139.0,
            -0.5,
        )

        self.assertEqual(
            points,
            [140.0, 139.5, 139.0],
        )

    def test_bad_sweep_direction_rejected(self):
        with self.assertRaises(ValueError):
            sweep_points(
                138.0,
                139.0,
                -0.1,
            )

    def test_dwell_must_be_positive(self):
        self.assertEqual(
            validate_dwell(0.5),
            0.5,
        )

        with self.assertRaises(ValueError):
            validate_dwell(0)

    def test_magic_unconfigured_is_rejected(self):
        with self.assertRaises(ValueError):
            configured_magic(
                [],
                0,
                "+",
                140.0,
            )


if __name__ == "__main__":
    unittest.main()
