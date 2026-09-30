from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]

NORMAL = (
    ROOT / "configs" / "ioc_manager" / "settings.yaml"
)

SIM = (
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
    ROOT / "configs" / "phoebus"
    / "genesis_mac_settings.ini"
)

START = ROOT / "scripts" / "start_genesis_mac.sh"
STOP = ROOT / "scripts" / "stop_genesis_mac.sh"


def load(path):
    with path.open() as stream:
        return yaml.safe_load(stream)


class PCL602ManagerIntegration(unittest.TestCase):

    def test_normal_registration(self):
        cfg = load(NORMAL)
        dev = cfg["mw_pcl602"]

        self.assertFalse(dev["autostart"])

        self.assertEqual(
            dev["settings_folder"],
            "configs/microwave/pcl602",
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
        cfg = load(SIM)
        dev = cfg["mw_pcl602"]

        self.assertEqual(
            dev["settings_folder"],
            "configs/commissioning/"
            "microwave_sim/pcl602",
        )

    def test_discovery_lists_contain_robot(self):
        normal = load(NORMAL)
        archive = load(ARCHIVER)

        self.assertIn(
            "127.0.0.1:5087",
            normal["general"]["epics_addr_list"],
        )

        self.assertIn(
            "127.0.0.1:5087",
            archive["general"]["epics_addr_list"],
        )

        self.assertIn(
            "127.0.0.1:5088",
            PHOEBUS.read_text(),
        )

    def test_normal_startup_does_not_launch_robot(self):
        self.assertNotIn(
            'start_managed_ioc "mw_pcl602"',
            START.read_text(),
        )

    def test_shutdown_knows_robot(self):
        self.assertIn(
            'stop_managed_ioc "mw_pcl602"',
            STOP.read_text(),
        )

    def test_all_manager_ports_unique(self):
        cfg = load(NORMAL)

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
