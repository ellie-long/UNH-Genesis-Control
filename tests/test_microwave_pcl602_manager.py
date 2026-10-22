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

PHYSICAL_ROBOT = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "pcl602"
    / "settings.yaml"
)

SIM_ROBOT = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_sim"
    / "pcl602"
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

PHOEBUS = (
    ROOT
    / "configs"
    / "phoebus"
    / "microwave_physical.ini"
)


def load(file_path):
    with file_path.open() as stream:
        return yaml.safe_load(stream)


class PCL602ManagerIntegration(unittest.TestCase):

    def test_physical_manager_registration(self):
        cfg = load(PHYSICAL_MANAGER)
        dev = cfg["mw_pcl602"]

        self.assertFalse(dev["autostart"])

        self.assertEqual(
            dev["settings_folder"],
            "configs/commissioning/microwave_physical/pcl602",
        )

        self.assertEqual(
            dev["ioc"],
            "pcl602",
        )

        self.assertEqual(
            dev["heartbeat_pv"],
            "UNH:GEN:MW:ROBOT:MAN:pcl602_time",
        )

        self.assertEqual(
            dev["ca_server_port"],
            5087,
        )

        self.assertEqual(
            dev["pva_server_port"],
            5088,
        )

    def test_sim_manager_uses_local_robot_profile(self):
        cfg = load(SIM_MANAGER)
        dev = cfg["mw_pcl602"]

        self.assertEqual(
            dev["settings_folder"],
            "configs/commissioning/microwave_sim/pcl602",
        )

    def test_physical_profile_is_not_simulation(self):
        cfg = load(PHYSICAL_ROBOT)

        self.assertFalse(
            cfg["pcl602"]["simulation"]
        )

    def test_sim_profile_is_simulation(self):
        cfg = load(SIM_ROBOT)

        self.assertTrue(
            cfg["pcl602"]["simulation"]
        )

    def test_discovery_lists_contain_robot(self):
        manager = load(PHYSICAL_MANAGER)
        archive = load(PHYSICAL_ARCHIVER)

        self.assertIn(
            "127.0.0.1:5087",
            manager["general"]["epics_addr_list"],
        )

        self.assertIn(
            "127.0.0.1:5087",
            archive["general"]["epics_addr_list"],
        )

        self.assertIn(
            "127.0.0.1:5087",
            PHOEBUS.read_text(),
        )

    def test_all_manager_ports_unique(self):
        cfg = load(PHYSICAL_MANAGER)

        ca = []
        pva = []

        for name, dev in cfg.items():
            if name == "general":
                continue

            if dev.get("ca_server_port") is not None:
                ca.append(dev["ca_server_port"])

            if dev.get("pva_server_port") is not None:
                pva.append(dev["pva_server_port"])

        self.assertEqual(len(ca), len(set(ca)))
        self.assertEqual(len(pva), len(set(pva)))


if __name__ == "__main__":
    unittest.main()
