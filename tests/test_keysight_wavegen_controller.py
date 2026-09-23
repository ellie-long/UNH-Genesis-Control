import unittest

from ssrf_ioc.keysight_wavegen import (
    DeviceConnection,
)

from tools.sim_keysight_wavegen import (
    start_server,
)


class TestKeysightWaveGenController(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        (
            cls.server,
            cls.thread,
            cls.simulator,
        ) = start_server(
            host="127.0.0.1",
            port=0,
            verbose=False,
        )

        host, port = (
            cls.server.server_address
        )

        cls.resource = (
            f"TCPIP0::{host}::{port}::SOCKET"
        )

        cls.connection = DeviceConnection(
            resource=cls.resource,
            backend="@py",
            timeout=2.0,
            settle=0.0,
        )

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

        cls.server.shutdown()
        cls.server.server_close()

        cls.thread.join(
            timeout=2.0
        )

    def setUp(self):
        # Return to a deterministic safe state.
        self.connection.set_output(False)
        self.connection.set_function_arb()
        self.connection.set_period_s(
            0.010
        )
        self.connection.set_vhigh_v(
            0.700
        )
        self.connection.set_vlow_v(
            -0.700
        )

    def test_identity_and_default_state(self):
        state = (
            self.connection.read_state()
        )

        self.assertIn(
            "KEYSIGHT",
            state["idn"].upper(),
        )

        self.assertEqual(
            state["function"],
            "ARB",
        )

        self.assertFalse(
            state["output_on"]
        )

        self.assertAlmostEqual(
            state["period_s"],
            0.010,
            places=6,
        )

        self.assertAlmostEqual(
            state["vhigh_v"],
            0.700,
            places=6,
        )

        self.assertAlmostEqual(
            state["vlow_v"],
            -0.700,
            places=6,
        )

    def test_period_round_trip(self):
        confirmed = (
            self.connection.set_period_s(
                0.009
            )
        )

        self.assertAlmostEqual(
            confirmed,
            0.009,
            places=9,
        )

        self.assertAlmostEqual(
            self.connection.read_state()[
                "period_s"
            ],
            0.009,
            places=9,
        )

    def test_voltage_round_trip(self):
        high = (
            self.connection.set_vhigh_v(
                0.500
            )
        )

        low = (
            self.connection.set_vlow_v(
                -0.500
            )
        )

        self.assertAlmostEqual(
            high,
            0.500,
            places=6,
        )

        self.assertAlmostEqual(
            low,
            -0.500,
            places=6,
        )

        state = (
            self.connection.read_state()
        )

        self.assertAlmostEqual(
            state["vhigh_v"],
            0.500,
            places=6,
        )

        self.assertAlmostEqual(
            state["vlow_v"],
            -0.500,
            places=6,
        )

    def test_output_round_trip(self):
        self.assertTrue(
            self.connection.set_output(
                True
            )
        )

        self.assertTrue(
            self.connection.read_state()[
                "output_on"
            ]
        )

        self.assertFalse(
            self.connection.set_output(
                False
            )
        )

        self.assertFalse(
            self.connection.read_state()[
                "output_on"
            ]
        )

    def test_invalid_period_rejected(self):
        with self.assertRaises(
            ValueError
        ):
            self.connection.set_period_s(
                0.0
            )

        with self.assertRaises(
            ValueError
        ):
            self.connection.set_period_s(
                -0.010
            )

    def test_prepare_arb_mode_is_safe(self):
        # Deliberately begin with physical
        # output enabled.
        self.assertTrue(
            self.connection.set_output(
                True
            )
        )

        state = (
            self.connection.prepare_arb_mode()
        )

        self.assertEqual(
            state["function"],
            "ARB",
        )

        self.assertFalse(
            state["output_on"]
        )

        sim = self.simulator.state

        self.assertEqual(
            sim.arb_filter,
            "NORM",
        )

        self.assertTrue(
            sim.interpolate
        )

        self.assertEqual(
            sim.load,
            "INF",
        )

        self.assertEqual(
            sim.output_mode,
            "NORM",
        )

        self.assertFalse(
            sim.invert
        )

    def test_safe_output_off(self):
        self.connection.set_output(
            True
        )

        confirmed = (
            self.connection.safe_output_off()
        )

        self.assertFalse(
            confirmed
        )

        self.assertFalse(
            self.connection.read_state()[
                "output_on"
            ]
        )

    def test_error_queue_is_empty(self):
        self.assertEqual(
            self.connection.errors(),
            [],
        )


if __name__ == "__main__":
    unittest.main()
