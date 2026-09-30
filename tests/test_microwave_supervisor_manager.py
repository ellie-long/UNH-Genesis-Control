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

SIM_SUPERVISOR = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_sim"
    / "supervisor"
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

START = ROOT / "scripts" / "start_genesis_mac.sh"
STOP = ROOT / "scripts" / "stop_genesis_mac.sh"
CTL = ROOT / "scripts" / "genesisctl.sh"


def load(path):
    with path.open() as stream:
        return yaml.safe_load(stream)


class MicrowaveSupervisorManager(unittest.TestCase):

    def test_normal_registration(self):
        cfg = load(NORMAL_MANAGER)
        dev = cfg["mw_supervisor"]

        self.assertFalse(dev["autostart"])

        self.assertEqual(
            dev["settings_folder"],
            "configs/microwave/supervisor",
        )

        self.assertEqual(
            dev["ioc"],
            "microwave_supervisor",
        )

        self.assertEqual(
            dev["heartbeat_pv"],
            "UNH:GEN:MW:SUP:"
            "MAN:microwave_supervisor_time",
        )

        self.assertEqual(
            dev["ca_server_port"],
            5085,
        )

        self.assertEqual(
            dev["pva_server_port"],
            5086,
        )

    def test_sim_manager_uses_dedicated_sim_profile(self):
        cfg = load(SIM_MANAGER)
        dev = cfg["mw_supervisor"]

        self.assertEqual(
            dev["settings_folder"],
            "configs/commissioning/"
            "microwave_sim/supervisor",
        )

        self.assertEqual(
            dev["ca_server_port"],
            5085,
        )

        self.assertEqual(
            dev["pva_server_port"],
            5086,
        )

    def test_sim_profile_is_simulation(self):
        cfg = load(SIM_SUPERVISOR)

        self.assertTrue(
            cfg["microwave_supervisor"]["simulation"]
        )

    def test_ports_are_unique_in_normal_manager(self):
        cfg = load(NORMAL_MANAGER)

        ca = []
        pva = []

        for name, dev in cfg.items():
            if name == "general":
                continue

            if dev.get("ca_server_port") is not None:
                ca.append(dev["ca_server_port"])

            if dev.get("pva_server_port") is not None:
                pva.append(dev["pva_server_port"])

        self.assertEqual(
            len(ca),
            len(set(ca)),
        )

        self.assertEqual(
            len(pva),
            len(set(pva)),
        )

    def test_ca_discovery_contains_supervisor(self):
        normal = load(NORMAL_MANAGER)
        archive = load(ARCHIVER)

        self.assertIn(
            "127.0.0.1:5085",
            normal["general"]["epics_addr_list"],
        )

        self.assertIn(
            "127.0.0.1:5085",
            archive["general"]["epics_addr_list"],
        )

        self.assertIn(
            "127.0.0.1:5085",
            START.read_text(),
        )

        self.assertIn(
            "127.0.0.1:5085",
            STOP.read_text(),
        )

        self.assertIn(
            "127.0.0.1:5085",
            CTL.read_text(),
        )

    def test_phoebus_contains_supervisor_pva(self):
        self.assertIn(
            "127.0.0.1:5086",
            PHOEBUS.read_text(),
        )

    def test_normal_startup_does_not_launch_supervisor(self):
        text = START.read_text()

        self.assertNotIn(
            'start_managed_ioc "mw_supervisor"',
            text,
        )

        self.assertNotIn(
            'start_managed_ioc "mw_vaunix_lms"',
            text,
        )

    def test_shutdown_knows_microwave_stack(self):
        text = STOP.read_text()

        self.assertIn(
            'stop_managed_ioc "mw_supervisor"',
            text,
        )

        self.assertIn(
            'stop_managed_ioc "mw_vaunix_lms"',
            text,
        )


if __name__ == "__main__":
    unittest.main()
