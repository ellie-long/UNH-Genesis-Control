import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

POLICY = (
    "configs/access_security/"
    "ssrf_commands.acf"
)

ASG = "GENESIS_SSRF_CMD"

EXPECTED = {
    "smc100a": {
        "FREQ_CMD",
        "LEVEL_CMD",
        "RF_CMD",
        "FM_CMD",
        "FM_SOURCE_CMD",
        "FM_DEV_CMD",
        "SWEEP_START_CMD",
        "SWEEP_STOP_CMD",
        "SWEEP_STEP_CMD",
        "SWEEP_DWELL_CMD",
        "SWEEP_SHAPE_CMD",
        "SWEEP_ARM_CMD",
        "SWEEP_TRIGGER_CMD",
    },

    "keysight_wavegen": {
        "OUTPUT_CMD",
        "PERIOD_CMD",
        "VHIGH_CMD",
        "VLOW_CMD",
        "WAVEFORM_CMD",
        "LOAD_CMD",
    },

    "nmr_switch": {
        "PORT_CMD",
    },

    "rf_switch": {
        "PORT_CMD",
    },
}


class TestPhysicalAuthorization(unittest.TestCase):

    def load(self, name):
        return yaml.safe_load(
            (
                ROOT
                / "configs"
                / "ssrf"
                / name
                / "settings.yaml"
            ).read_text()
        )

    def device_section(self, cfg):
        names = [
            key
            for key, value
            in cfg.items()
            if (
                key != "general"
                and isinstance(value, dict)
                and "records" in value
            )
        ]

        self.assertEqual(
            len(names),
            1,
        )

        return cfg[
            names[0]
        ]

    def test_policy_file_exists(self):
        self.assertTrue(
            (
                ROOT
                / POLICY
            ).is_file()
        )

    def test_all_device_profiles_reference_policy(self):
        for name in EXPECTED:

            cfg = self.load(name)

            self.assertEqual(
                cfg["general"][
                    "access_security_file"
                ],
                POLICY,
            )

    def test_all_normal_profiles_stay_read_only(self):
        for name in EXPECTED:

            device = self.device_section(
                self.load(name)
            )

            self.assertFalse(
                device[
                    "write_enabled"
                ]
            )

    def test_complete_command_inventory_is_protected(self):
        for name, expected in EXPECTED.items():

            device = self.device_section(
                self.load(name)
            )

            records = device[
                "records"
            ]

            for record in expected:

                self.assertIn(
                    record,
                    records,
                    msg=(
                        f"{name}: missing "
                        f"{record}"
                    ),
                )

                self.assertEqual(
                    records[
                        record
                    ].get("ASG"),
                    ASG,
                    msg=(
                        f"{name}: {record} "
                        "not protected"
                    ),
                )

    def test_waveform_staging_is_protected(self):
        device = self.device_section(
            self.load(
                "keysight_wavegen"
            )
        )

        self.assertEqual(
            device[
                "records"
            ][
                "WAVEFORM_CMD"
            ].get("ASG"),
            ASG,
        )


if __name__ == "__main__":
    unittest.main()
