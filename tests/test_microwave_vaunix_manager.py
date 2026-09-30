from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]

NORMAL_MANAGER = (
    ROOT / "configs" / "ioc_manager" / "settings.yaml"
)

SIM_MANAGER = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_sim"
    / "ioc_manager"
    / "settings.yaml"
)

ARCHIVER = (
    ROOT / "configs" / "archiver" / "settings.yaml"
)

PHOEBUS = (
    ROOT
    / "configs"
    / "phoebus"
    / "genesis_mac_settings.ini"
)

START_SCRIPT = ROOT / "scripts" / "start_genesis_mac.sh"
STOP_SCRIPT = ROOT / "scripts" / "stop_genesis_mac.sh"


def load_yaml(path):
    with path.open() as stream:
        return yaml.safe_load(stream)


class VaunixManagerIntegration(unittest.TestCase):

    def test_normal_manager_registration(self):
        cfg = load_yaml(NORMAL_MANAGER)
        dev = cfg["mw_vaunix_lms"]

        self.assertFalse(dev["autostart"])
        self.assertEqual(
            dev["settings_folder"],
            "configs/microwave/vaunix_lms",
        )
        self.assertEqual(dev["ioc"], "vaunix_lms")
        self.assertEqual(
            dev["heartbeat_pv"],
            "UNH:GEN:MW:SRC:MAN:vaunix_lms_time",
        )
        self.assertEqual(dev["ca_server_port"], 5069)
        self.assertEqual(dev["pva_server_port"], 5079)

    def test_sim_manager_uses_simulation_profile(self):
        cfg = load_yaml(SIM_MANAGER)
        dev = cfg["mw_vaunix_lms"]

        self.assertEqual(
            dev["settings_folder"],
            "configs/commissioning/"
            "microwave_sim/vaunix_lms",
        )
        self.assertEqual(dev["ca_server_port"], 5069)
        self.assertEqual(dev["pva_server_port"], 5079)

    def test_ca_discovery_contains_vaunix(self):
        normal = load_yaml(NORMAL_MANAGER)
        archive = load_yaml(ARCHIVER)

        self.assertIn(
            "127.0.0.1:5069",
            normal["general"]["epics_addr_list"],
        )
        self.assertIn(
            "127.0.0.1:5069",
            archive["general"]["epics_addr_list"],
        )

    def test_phoebus_discovery_contains_vaunix(self):
        text = PHOEBUS.read_text()

        self.assertIn(
            "127.0.0.1:5079",
            text,
        )

    def test_normal_startup_does_not_start_physical_vaunix_yet(self):
        text = START_SCRIPT.read_text()

        self.assertNotIn(
            'start_managed_ioc "mw_vaunix_lms"',
            text,
        )

    def test_mac_ca_lists_contain_vaunix(self):
        for path in (START_SCRIPT, STOP_SCRIPT):
            text = path.read_text()

            self.assertIn(
                "127.0.0.1:5069",
                text,
            )

    def test_shutdown_ca_list_is_not_malformed(self):
        text = STOP_SCRIPT.read_text()

        self.assertNotIn(
            "5070127.0.0.1",
            text,
        )


if __name__ == "__main__":
    unittest.main()
