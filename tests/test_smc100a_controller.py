"""Software-only tests for the Genesis SMC100A controller."""

from __future__ import annotations

import threading
import unittest

from ssrf_ioc.smc100a import DeviceConnection
from tools.sim_smc100a import State, serve


class TestSMC100AController(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Port 0 asks macOS to assign an unused local TCP port.
        cls.server = serve("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]

        cls.thread = threading.Thread(
            target=cls.server.serve_forever,
            daemon=True,
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        # Fresh simulated instrument state for every test.
        self.server.state = State()

        self.connection = DeviceConnection(
            "127.0.0.1",
            port=self.port,
            timeout=2.0,
            settle=0.0,
        )

    def tearDown(self):
        self.connection.close()

    def test_identity_and_default_state(self):
        self.assertIn(
            "SMC100A",
            self.connection.idn(),
        )

        state = self.connection.read_state()

        self.assertAlmostEqual(
            state["freq_mhz"],
            33.0,
            places=6,
        )
        self.assertAlmostEqual(
            state["level_dbm"],
            -22.0,
            places=2,
        )
        self.assertFalse(state["rf_on"])
        self.assertFalse(state["fm_on"])
        self.assertEqual(
            state["pow_mode"],
            "CW",
        )
        self.assertEqual(
            state["fm_source"],
            "EXT",
        )
        self.assertAlmostEqual(
            state["fm_dev_khz"],
            10.0,
            places=3,
        )

    def test_default_sweep_state(self):
        sweep = self.connection.read_sweep()

        self.assertAlmostEqual(
            sweep["start_dbm"],
            -120.0,
            places=2,
        )
        self.assertAlmostEqual(
            sweep["stop_dbm"],
            -22.0,
            places=2,
        )
        self.assertAlmostEqual(
            sweep["step_db"],
            1.0,
            places=2,
        )
        self.assertAlmostEqual(
            sweep["dwell_ms"],
            10.0,
            places=1,
        )

    def test_carrier_and_level_round_trip(self):
        self.assertAlmostEqual(
            self.connection.set_frequency_mhz(32.8),
            32.8,
            places=6,
        )

        self.assertAlmostEqual(
            self.connection.set_level_dbm(-30.0),
            -30.0,
            places=2,
        )

        state = self.connection.read_state()

        self.assertAlmostEqual(
            state["freq_mhz"],
            32.8,
            places=6,
        )
        self.assertAlmostEqual(
            state["level_dbm"],
            -30.0,
            places=2,
        )

    def test_rf_and_fm_round_trip(self):
        self.assertTrue(
            self.connection.set_rf(True)
        )
        self.assertFalse(
            self.connection.set_rf(False)
        )

        self.assertEqual(
            self.connection.set_fm_source("INT"),
            "INT",
        )

        self.assertAlmostEqual(
            self.connection.set_fm_deviation_khz(25.0),
            25.0,
            places=3,
        )

        self.assertTrue(
            self.connection.set_fm(True)
        )

        state = self.connection.read_state()

        self.assertFalse(state["rf_on"])
        self.assertTrue(state["fm_on"])
        self.assertEqual(
            state["fm_source"],
            "INT",
        )
        self.assertAlmostEqual(
            state["fm_dev_khz"],
            25.0,
            places=3,
        )

    def test_sweep_round_trip(self):
        start_dbm = -120.0
        stop_dbm = -40.0
        step_db = stop_dbm - start_dbm
        dwell_ms = 90.0

        sweep = self.connection.set_sweep(
            start_dbm=start_dbm,
            stop_dbm=stop_dbm,
            step_db=step_db,
            dwell_ms=dwell_ms,
            shape="TRI",
        )

        self.assertAlmostEqual(
            sweep["start_dbm"],
            start_dbm,
            places=2,
        )

        self.assertAlmostEqual(
            sweep["stop_dbm"],
            stop_dbm,
            places=2,
        )

        self.assertAlmostEqual(
            sweep["step_db"],
            step_db,
            places=2,
        )

        self.assertAlmostEqual(
            sweep["dwell_ms"],
            dwell_ms,
            places=1,
        )

        self.assertTrue(
            sweep["shape"].startswith("TRI")
        )

    def test_arm_trigger_and_disarm(self):
        self.assertTrue(
            self.connection.arm_sweep(True)
        )

        self.connection.trigger_sweep()

        self.assertFalse(
            self.connection.arm_sweep(False)
        )

        self.assertEqual(
            self.connection.read_state()["pow_mode"],
            "CW",
        )

    def test_error_queue_is_empty(self):
        self.assertEqual(
            self.connection.errors(),
            [],
        )


if __name__ == "__main__":
    unittest.main()