"""
Permanent regression tests for Genesis Phase-I ssRF physical connectivity.

No test in this file contacts physical hardware.
"""

from pathlib import Path
import unittest

import yaml

from ssrf_ioc.minicircuits_switch import MiniCircuitsSwitchConnection


class TestMiniCircuitsPhysicalIdentity(unittest.TestCase):
    def test_prefixed_model_is_normalized(self):
        self.assertEqual(
            MiniCircuitsSwitchConnection._strip_identity_prefix(
                "MN=RC-2SPDT-A18",
                "MN",
            ),
            "RC-2SPDT-A18",
        )

    def test_prefixed_serial_is_normalized(self):
        self.assertEqual(
            MiniCircuitsSwitchConnection._strip_identity_prefix(
                "SN=02206270097",
                "SN",
            ),
            "02206270097",
        )

    def test_bare_identity_is_accepted(self):
        self.assertEqual(
            MiniCircuitsSwitchConnection._strip_identity_prefix(
                "RC-2SPDT-A18",
                "MN",
            ),
            "RC-2SPDT-A18",
        )

    def test_prefix_matching_is_case_insensitive(self):
        self.assertEqual(
            MiniCircuitsSwitchConnection._strip_identity_prefix(
                "mn=RC-2SPDT-A18",
                "MN",
            ),
            "RC-2SPDT-A18",
        )

    def test_empty_prefixed_identity_is_rejected(self):
        with self.assertRaises(OSError):
            MiniCircuitsSwitchConnection._strip_identity_prefix(
                "MN=",
                "MN",
            )


class TestKeysightPhysicalIdentityRegression(unittest.TestCase):
    def setUp(self):
        self.path = Path(
            "ssrf_ioc/keysight_wavegen.py"
        )
        self.text = self.path.read_text()

    def test_idn_record_is_long_string(self):
        self.assertIn(
            'self.pvs["IDN"] = builder.longStringIn(',
            self.text,
        )
        self.assertIn(
            "length=128",
            self.text,
        )

    def test_physical_idn_is_not_truncated_to_39_chars(self):
        self.assertNotIn(
            "identity[:39]",
            self.text,
        )

    def test_full_identity_is_written_to_idn_pv(self):
        expected = """self.pvs["IDN"].set(
            identity
        )"""
        self.assertIn(
            expected,
            self.text,
        )


class TestPhysicalReadOnlyProfile(unittest.TestCase):
    def setUp(self):
        path = Path(
            "configs/commissioning/"
            "ssrf_physical_readonly/"
            "keysight_wavegen/"
            "settings.yaml"
        )
        with path.open() as stream:
            self.cfg = yaml.safe_load(stream)

    def test_physical_resource_is_exact(self):
        device = self.cfg["keysight_wavegen"]
        self.assertEqual(
            device["resource"],
            (
                "USB0::10893::5990::"
                "MY57252012::0::INSTR"
            ),
        )
        self.assertEqual(
            device["visa_backend"],
            "@py",
        )

    def test_startup_remains_read_only(self):
        device = self.cfg["keysight_wavegen"]
        self.assertFalse(
            device["write_enabled"]
        )
        self.assertFalse(
            device["initialize_on_connect"]
        )

    def test_command_pvs_retain_access_security(self):
        device = self.cfg["keysight_wavegen"]

        for name in (
            "OUTPUT_CMD",
            "PERIOD_CMD",
            "VHIGH_CMD",
            "VLOW_CMD",
            "WAVEFORM_CMD",
            "LOAD_CMD",
        ):
            self.assertEqual(
                device["records"][name]["ASG"],
                "GENESIS_SSRF_CMD",
            )


if __name__ == "__main__":
    unittest.main()
