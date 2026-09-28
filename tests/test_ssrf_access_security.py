import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

POLICY = (
    ROOT
    / "configs"
    / "access_security"
    / "ssrf_commands.acf"
)

SIM = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_active"
)


class TestSsrfAccessSecurityFoundation(
    unittest.TestCase
):

    def test_policy_exists(self):
        self.assertTrue(
            POLICY.is_file()
        )

    def test_policy_has_dedicated_service_identity(self):
        text = POLICY.read_text()

        self.assertIn(
            "UAG(GENESIS_SSRF_SEQUENCER)",
            text,
        )

        self.assertIn(
            "genesis_ssrf_sequencer",
            text,
        )

        self.assertIn(
            "shrestha",
            text,
        )

    def test_policy_has_protected_command_group(self):
        text = POLICY.read_text()

        self.assertIn(
            "ASG(GENESIS_SSRF_CMD)",
            text,
        )

        self.assertIn(
            "RULE(1, READ)",
            text,
        )

        self.assertIn(
            "RULE(1, WRITE",
            text,
        )

    def test_simulator_profile_does_not_claim_os_identity(self):
        settings = yaml.safe_load(
            (
                SIM
                / "sequencer"
                / "settings.yaml"
            ).read_text()
        )

        seq = settings[
            "ssrf_sequencer"
        ]

        self.assertTrue(
            seq["execution_enabled"]
        )

        self.assertNotIn(
            "ca_client_user",
            seq,
        )

    def test_simulator_devices_do_not_enable_acf(self):
        for name in (
            "smc100a",
            "keysight_wavegen",
            "nmr_switch",
            "rf_switch",
        ):
            settings = yaml.safe_load(
                (
                    SIM
                    / name
                    / "settings.yaml"
                ).read_text()
            )

            self.assertNotIn(
                "access_security_file",
                settings["general"],
            )

    def test_master_ioc_keeps_optional_security_loader(self):
        text = (
            ROOT
            / "master_ioc.py"
        ).read_text()

        self.assertIn(
            "configure_access_security(",
            text,
        )

        self.assertLess(
            text.index(
                "configure_access_security("
            ),
            text.index(
                "softioc.iocInit("
            ),
        )


if __name__ == "__main__":
    unittest.main()
