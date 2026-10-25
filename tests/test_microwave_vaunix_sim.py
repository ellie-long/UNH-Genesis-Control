from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from microwave_ioc.vaunix_lms.toolkit_device import (
    Device,
    LocalSimulationConnection,
    ShimRejected,
)


ROOT = Path(__file__).resolve().parents[1]
SIM_CONFIG = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_sim"
    / "vaunix_lms"
    / "settings.yaml"
)


class VaunixLocalSimulation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with SIM_CONFIG.open() as stream:
            cls.full_config = yaml.safe_load(stream)

        cls.settings = cls.full_config["vaunix_lms"]

    def new_sim(self):
        return LocalSimulationConnection(dict(self.settings))

    def test_sim_profile_is_explicit(self):
        self.assertTrue(self.settings["simulation"])
        self.assertFalse(
            self.settings["shim"]["start_with_ioc"]
        )

    def test_simulator_identity_is_never_hardware(self):
        sim = self.new_sim()

        identity, test_mode = sim.identity()

        self.assertIn("LOCAL", identity)
        self.assertEqual(test_mode, 1)

        with self.assertRaises(ShimRejected):
            sim.set_test_mode(False)

    def test_simulator_initial_state_is_safe(self):
        sim = self.new_sim()

        frequency, power, rf, reference, lock = sim.read()

        self.assertEqual(frequency, 11.5)
        self.assertEqual(power, -40.0)
        self.assertEqual(rf, 0)
        self.assertEqual(reference, 1)
        self.assertEqual(lock, 1)

    def test_frequency_and_power_commands_round_trip(self):
        sim = self.new_sim()

        self.assertEqual(
            sim.set_frequency(11.6),
            11.6,
        )
        self.assertEqual(
            sim.set_power(-20.0),
            -20.0,
        )

        frequency, power, *_ = sim.read()

        self.assertEqual(frequency, 11.6)
        self.assertEqual(power, -20.0)

    def test_limits_reject_out_of_range_commands(self):
        sim = self.new_sim()

        # the simulated shim models the Pi shim's Expert envelope,
        # 134-144 GHz / 12 (Elena Long, 2026-10-24)
        with self.assertRaises(ShimRejected):
            sim.set_frequency(11.1)

        with self.assertRaises(ShimRejected):
            sim.set_frequency(12.1)

        with self.assertRaises(ShimRejected):
            sim.set_power(-41.0)

        with self.assertRaises(ShimRejected):
            sim.set_power(11.0)

    def test_rf_and_reference_are_simulated_only(self):
        sim = self.new_sim()

        self.assertEqual(sim.set_rf(True), 1)
        self.assertEqual(sim.set_reference(False), 0)

        _, _, rf, reference, _ = sim.read()

        self.assertEqual(rf, 1)
        self.assertEqual(reference, 0)

    def test_sweep_state_round_trip(self):
        sim = self.new_sim()

        sim.set_sweep_start(11.55)
        sim.set_sweep_end(11.70)
        sim.set_sweep_step(0.002)
        sim.set_sweep_time(500)
        sim.set_sweep_flag("REPEAT", True)
        sim.set_sweep_flag("RUN", True)

        values = sim.sweep()

        self.assertEqual(values[0], 11.55)
        self.assertEqual(values[1], 11.70)
        self.assertEqual(values[2], 0.002)
        self.assertEqual(values[3], 500)
        self.assertEqual(values[5], 1)
        self.assertEqual(values[8], 1)

    def test_device_connection_factory_uses_no_network_in_simulation(self):
        dummy = SimpleNamespace(
            simulation=True,
            settings=dict(self.settings),
        )

        with patch(
            "microwave_ioc.vaunix_lms.toolkit_device."
            "socket.create_connection",
            side_effect=AssertionError(
                "network must not be used in local simulation"
            ),
        ):
            transport = Device._create_connection(dummy)

        self.assertIsInstance(
            transport,
            LocalSimulationConnection,
        )

    def test_remote_service_is_never_started_in_simulation(self):
        dummy = SimpleNamespace(
            simulation=True,
            settings=dict(self.settings),
        )

        with patch(
            "microwave_ioc.vaunix_lms.toolkit_device."
            "subprocess.run"
        ) as run:
            Device._start_remote_service(dummy)

        run.assert_not_called()


    def test_device_connect_updates_simulation_input_without_process_kwarg(self):
        class InputPV:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        class OutputPV:
            def __init__(self):
                self.value = None

            def set(self, value, process=True):
                self.value = value

        settings = dict(self.settings)

        dummy = SimpleNamespace(
            simulation=True,
            settings=settings,
            channel="MW",
            pvs={
                "MW_Connected": InputPV(),
                "MW_Simulation": InputPV(),
                "MW_TestMode": OutputPV(),
            },
            t=None,
            _session=0,
        )

        dummy.pvs.update({name: InputPV() for name in ("MW_RF_VALID", "MW_RF_READY", "MW_RF_RBV", "MW_RF_STATE", "MW_RF_TIME", "MW_RF_ERROR", "MW_CONTROL_OWNED", "MW_CONTROL_SESSION")})
        dummy._clear_commands = lambda: None
        dummy._unknown = lambda message: None
        dummy._off = lambda: None
        dummy._start_remote_service = lambda: None
        dummy._create_connection = lambda: LocalSimulationConnection(settings)
        dummy.read_outs = lambda: None

        Device.connect(dummy)

        self.assertEqual(
            dummy.pvs["MW_Connected"].value,
            True,
        )
        self.assertEqual(
            dummy.pvs["MW_Simulation"].value,
            True,
        )
        self.assertEqual(
            dummy.pvs["MW_TestMode"].value,
            1,
        )


    def test_sweep_run_immediately_updates_active_readback(self):
        class InputPV:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        class OutputPV:
            def __init__(self):
                self.value = None

            def set(self, value, process=True):
                self.value = value

        sim = self.new_sim()

        dummy = SimpleNamespace(
            t=sim,
            simulation=True,
            channel="MW",
            pvs={
                "MW_SweepRun": OutputPV(),
                "MW_SweepActive": InputPV(),
            },
        )

        dummy.read_outs = lambda: None
        dummy.reconnect = lambda: None

        Device.do_sets(
            dummy,
            1,
            "UNH:GEN:MW:SRC:MW_SweepRun",
        )

        self.assertEqual(
            dummy.pvs["MW_SweepRun"].value,
            1,
        )
        self.assertEqual(
            dummy.pvs["MW_SweepActive"].value,
            1,
        )

        Device.do_sets(
            dummy,
            0,
            "UNH:GEN:MW:SRC:MW_SweepRun",
        )

        self.assertEqual(
            dummy.pvs["MW_SweepActive"].value,
            0,
        )


if __name__ == "__main__":
    unittest.main()
