import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

SAFE = ROOT / "configs" / "commissioning" / "ssrf_sim_readonly"

ACTIVE = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_active"
)


def load(path):
    with open(path) as f:
        return yaml.safe_load(f)


class TestSsrfSimulatorActiveProfile(
    unittest.TestCase
):
    def test_safe_device_profiles_remain_read_only(self):
        cases = {
            "smc100a": "smc100a",
            "keysight_wavegen":
                "keysight_wavegen",
            "nmr_switch":
                "minicircuits_switch",
            "rf_switch":
                "minicircuits_switch",
        }

        for folder, key in cases.items():
            data = load(
                SAFE
                / folder
                / "settings.yaml"
            )

            self.assertIs(
                data[key]["write_enabled"],
                False,
            )

    def test_active_device_profiles_are_writable(self):
        cases = {
            "smc100a": "smc100a",
            "keysight_wavegen":
                "keysight_wavegen",
            "nmr_switch":
                "minicircuits_switch",
            "rf_switch":
                "minicircuits_switch",
        }

        for folder, key in cases.items():
            data = load(
                ACTIVE
                / folder
                / "settings.yaml"
            )

            self.assertIs(
                data[key]["write_enabled"],
                True,
            )

    def test_initialize_on_connect_remains_false(self):
        cases = {
            "smc100a": "smc100a",
            "keysight_wavegen":
                "keysight_wavegen",
            "nmr_switch":
                "minicircuits_switch",
            "rf_switch":
                "minicircuits_switch",
        }

        for folder, key in cases.items():
            data = load(
                ACTIVE
                / folder
                / "settings.yaml"
            )

            self.assertIs(
                data[key][
                    "initialize_on_connect"
                ],
                False,
            )

    def test_smc_is_localhost_only(self):
        data = load(
            ACTIVE
            / "smc100a"
            / "settings.yaml"
        )

        self.assertEqual(
            data["smc100a"]["ip"],
            "127.0.0.1",
        )

        self.assertEqual(
            int(data["smc100a"]["port"]),
            5025,
        )

    def test_wavegen_is_localhost_only(self):
        data = load(
            ACTIVE
            / "keysight_wavegen"
            / "settings.yaml"
        )

        resource = str(
            data[
                "keysight_wavegen"
            ]["resource"]
        )

        self.assertIn(
            "127.0.0.1",
            resource,
        )

        self.assertIn(
            "5027",
            resource,
        )

    def test_switches_are_localhost_only(self):
        cases = {
            "nmr_switch": 8081,
            "rf_switch": 8082,
        }

        for folder, port in cases.items():
            data = load(
                ACTIVE
                / folder
                / "settings.yaml"
            )

            settings = data[
                "minicircuits_switch"
            ]

            self.assertEqual(
                settings["ip"],
                "127.0.0.1",
            )

            self.assertEqual(
                int(settings["port"]),
                port,
            )

    def test_execution_enabled_only_in_active_profile(self):
        active = load(
            ACTIVE
            / "sequencer"
            / "settings.yaml"
        )

        safe = load(
            SAFE
            / "sequencer"
            / "settings.yaml"
        )

        self.assertIs(
            active[
                "ssrf_sequencer"
            ]["execution_enabled"],
            True,
        )

        self.assertFalse(
            safe[
                "ssrf_sequencer"
            ].get(
                "execution_enabled",
                False,
            )
        )

    def test_logic_epics_addresses_are_localhost(self):
        for folder in (
            "interlock",
            "sequencer",
        ):
            data = load(
                ACTIVE
                / folder
                / "settings.yaml"
            )

            addresses = str(
                data["general"][
                    "epics_addr_list"
                ]
            ).split()

            self.assertTrue(addresses)

            for address in addresses:
                self.assertTrue(
                    address.startswith(
                        "127.0.0.1:"
                    ),
                    address,
                )

    def test_manager_contains_only_ssrf_sim_profile(self):
        data = load(
            ACTIVE
            / "ioc_manager"
            / "settings.yaml"
        )

        expected = {
            "ssrf_smc100a",
            "ssrf_keysight_wavegen",
            "ssrf_nmr_switch",
            "ssrf_rf_switch",
            "ssrf_interlock",
            "ssrf_sequencer",
        }

        actual = (
            set(data)
            - {"general"}
        )

        self.assertEqual(
            actual,
            expected,
        )

        for name in expected:
            folder = str(
                data[name][
                    "settings_folder"
                ]
            )

            self.assertTrue(
                folder.startswith(
                    "configs/commissioning/"
                    "ssrf_sim_active/"
                )
            )

    def test_manager_ca_addresses_are_localhost(self):
        data = load(
            ACTIVE
            / "ioc_manager"
            / "settings.yaml"
        )

        addresses = str(
            data["general"][
                "epics_addr_list"
            ]
        ).split()

        for address in addresses:
            self.assertTrue(
                address.startswith(
                    "127.0.0.1:"
                ),
                address,
            )


if __name__ == "__main__":
    unittest.main()
