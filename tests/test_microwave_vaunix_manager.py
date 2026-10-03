from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]

PHYSICAL_MANAGER = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "ioc_manager"
    / "settings.yaml"
)

SIM_MANAGER = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_sim"
    / "ioc_manager"
    / "settings.yaml"
)

PHYSICAL_ARCHIVER = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "archiver"
    / "settings.yaml"
)

PHYSICAL_VAUNIX = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "vaunix_lms"
    / "settings.yaml"
)

PHOEBUS = (
    ROOT
    / "configs"
    / "phoebus"
    / "genesis_mac_settings.ini"
)


def load_yaml(file_path):
    with file_path.open() as stream:
        return yaml.safe_load(stream)


class VaunixManagerIntegration(unittest.TestCase):

    def test_physical_manager_registration(self):
        cfg = load_yaml(PHYSICAL_MANAGER)
        dev = cfg["mw_vaunix_lms"]

        self.assertFalse(dev["autostart"])
        self.assertEqual(
            dev["settings_folder"],
            "configs/commissioning/microwave_physical/vaunix_lms",
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
            "configs/commissioning/microwave_sim/vaunix_lms",
        )
        self.assertEqual(dev["ca_server_port"], 5069)
        self.assertEqual(dev["pva_server_port"], 5079)

    def test_physical_profile_is_not_simulation(self):
        cfg = load_yaml(PHYSICAL_VAUNIX)
        dev = cfg["vaunix_lms"]

        self.assertFalse(dev["simulation"])
        self.assertEqual(dev["ip"], "mmWaveRobot.local")
        self.assertEqual(dev["port"], 1701)

    def test_ca_discovery_contains_vaunix(self):
        manager = load_yaml(PHYSICAL_MANAGER)
        archive = load_yaml(PHYSICAL_ARCHIVER)

        self.assertIn(
            "127.0.0.1:5069",
            manager["general"]["epics_addr_list"],
        )
        self.assertIn(
            "127.0.0.1:5069",
            archive["general"]["epics_addr_list"],
        )

    def test_phoebus_discovery_contains_vaunix(self):
        self.assertIn(
            "127.0.0.1:5079",
            PHOEBUS.read_text(),
        )


if __name__ == "__main__":
    unittest.main()
