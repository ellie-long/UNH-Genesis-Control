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

PHYSICAL_SUPERVISOR = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "supervisor"
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


class MicrowaveSupervisorManager(unittest.TestCase):

    def test_physical_manager_registration(self):
        cfg = load(PHYSICAL_MANAGER)
        dev = cfg["mw_supervisor"]

        self.assertFalse(dev["autostart"])

        self.assertEqual(
            dev["settings_folder"],
            "configs/commissioning/microwave_physical/supervisor",
        )

        self.assertEqual(
            dev["ioc"],
            "microwave_supervisor",
        )

        self.assertEqual(
            dev["heartbeat_pv"],
            "UNH:GEN:MW:SUP:MAN:microwave_supervisor_time",
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
            "configs/commissioning/microwave_sim/supervisor",
        )

        self.assertEqual(
            dev["ca_server_port"],
            5085,
        )

        self.assertEqual(
            dev["pva_server_port"],
            5086,
        )

    def test_physical_profile_is_not_simulation(self):
        cfg = load(PHYSICAL_SUPERVISOR)

        self.assertFalse(
            cfg["microwave_supervisor"]["simulation"]
        )

    def test_sim_profile_is_simulation(self):
        cfg = load(SIM_SUPERVISOR)

        self.assertTrue(
            cfg["microwave_supervisor"]["simulation"]
        )

    def test_ports_are_unique_in_physical_manager(self):
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

        self.assertEqual(
            len(ca),
            len(set(ca)),
        )

        self.assertEqual(
            len(pva),
            len(set(pva)),
        )

    def test_ca_discovery_contains_supervisor(self):
        manager = load(PHYSICAL_MANAGER)
        archive = load(PHYSICAL_ARCHIVER)

        self.assertIn(
            "127.0.0.1:5085",
            manager["general"]["epics_addr_list"],
        )

        self.assertIn(
            "127.0.0.1:5085",
            archive["general"]["epics_addr_list"],
        )

    def test_phoebus_contains_supervisor_pva(self):
        self.assertIn(
            "127.0.0.1:5086",
            PHOEBUS.read_text(),
        )


if __name__ == "__main__":
    unittest.main()
