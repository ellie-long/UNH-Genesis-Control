from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from microwave_ioc.pcl602.toolkit_device import (
    Device,
    DeviceRefused,
    LocalSimulationConnection,
)


ROOT = Path(__file__).resolve().parents[1]

PHYSICAL = (
    ROOT
    / "configs"
    / "microwave"
    / "pcl602"
    / "settings.yaml"
)

SIM = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_sim"
    / "pcl602"
    / "settings.yaml"
)


def load(path):
    with path.open() as stream:
        return yaml.safe_load(stream)


class PCL602GenesisConfig(unittest.TestCase):

    def test_physical_config(self):
        cfg = load(PHYSICAL)
        dev = cfg["pcl602"]

        self.assertEqual(
            cfg["general"]["prefix"],
            "UNH:GEN:MW:ROBOT",
        )

        self.assertEqual(
            dev["module"],
            "microwave_ioc.pcl602.toolkit_device",
        )

        self.assertFalse(dev["simulation"])
        self.assertFalse(dev["autostart"])

        self.assertEqual(
            dev["ip"],
            "mmWaveRobot.local",
        )

        self.assertEqual(
            dev["port"],
            1702,
        )

        self.assertEqual(
            dev["channels"],
            ["X", "Y"],
        )

        self.assertTrue(
            dev["require_speed_profiles"]
        )

    def test_sim_config_is_explicit(self):
        cfg = load(SIM)
        dev = cfg["pcl602"]

        self.assertTrue(dev["simulation"])

        self.assertEqual(
            dev["ip"],
            "127.0.0.1",
        )


class PCL602LocalSimulation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.settings = load(SIM)["pcl602"]

    def new_sim(self):
        return LocalSimulationConnection(
            dict(self.settings)
        )

    def test_initial_state_is_safe(self):
        sim = self.new_sim()
        state = sim.request("READ")

        self.assertTrue(state["healthy"])
        self.assertEqual(state["protocol"], 2)
        self.assertEqual(state["mode"], 0)
        self.assertFalse(
            state["limits_configurable"]
        )

        for axis in ("X", "Y"):
            data = state["axes"][axis]

            self.assertEqual(
                data["position"],
                0,
            )

            self.assertFalse(
                data["busy"]
            )

            self.assertFalse(
                data["motor_enabled"]
            )

            self.assertTrue(
                data["position_fresh"]
            )

            self.assertFalse(
                data["limits_enabled"]
            )

    def test_connection_factory_never_uses_network(self):
        dummy = SimpleNamespace(
            simulation=True,
            settings=dict(self.settings),
        )

        with patch(
            "microwave_ioc.pcl602.toolkit_device."
            "socket.create_connection",
            side_effect=AssertionError(
                "network must not be used "
                "for local robot simulation"
            ),
        ):
            transport = Device._create_connection(
                dummy
            )

        self.assertIsInstance(
            transport,
            LocalSimulationConnection,
        )

    def test_motion_requires_epics_mode_and_motor(self):
        sim = self.new_sim()

        with self.assertRaises(DeviceRefused):
            sim.request(
                "MOVE_REL",
                axis="X",
                value=1000,
            )

        sim.request(
            "MOTOR_ENABLE",
            axis="X",
            value=1,
        )

        sim.request(
            "MODE",
            value=1,
        )

        result = sim.request(
            "MOVE_REL",
            axis="X",
            value=1000,
        )

        self.assertEqual(
            result,
            1000,
        )

        state = sim.request("READ")

        self.assertEqual(
            state["axes"]["X"]["position"],
            1000,
        )

        self.assertFalse(
            state["axes"]["X"]["busy"],
        )

    def test_motor_enable_changes_require_disabled_mode(self):
        sim = self.new_sim()

        sim.request(
            "MODE",
            value=1,
        )

        with self.assertRaises(DeviceRefused):
            sim.request(
                "MOTOR_ENABLE",
                axis="X",
                value=1,
            )

    def test_stop_all_returns_safe_state(self):
        sim = self.new_sim()

        sim.request(
            "MOTOR_ENABLE",
            axis="X",
            value=1,
        )

        sim.request(
            "MOTOR_ENABLE",
            axis="Y",
            value=1,
        )

        sim.request(
            "MODE",
            value=1,
        )

        sim.request(
            "STOP_ALL",
        )

        state = sim.request("READ")

        self.assertEqual(
            state["mode"],
            0,
        )

        for axis in ("X", "Y"):
            self.assertTrue(
                state["axes"][axis]["motor_enabled"]
            )

            self.assertFalse(
                state["axes"][axis]["busy"]
            )

    def test_joystick_is_not_available_in_local_sim(self):
        sim = self.new_sim()

        with self.assertRaises(DeviceRefused):
            sim.request(
                "MODE",
                value=2,
            )

    def test_calibration_writes_disabled_in_local_sim(self):
        sim = self.new_sim()

        with self.assertRaises(DeviceRefused):
            sim.request(
                "SET_LIMITS",
                axis="X",
                low_cm=0,
                high_cm=10,
                enabled=True,
            )

    def test_driver_keeps_software_home_disabled(self):
        source = (
            ROOT
            / "microwave_ioc"
            / "pcl602"
            / "toolkit_device.py"
        ).read_text()

        self.assertIn(
            "Switch-based homing is disabled",
            source,
        )


if __name__ == "__main__":
    unittest.main()
