"""Software-only tests for the Genesis Agilent 33220A controller."""

from __future__ import annotations

import threading
import unittest

from ssrf_ioc.ag33220a import DeviceConnection
from tools.sim_ag33220a import State, serve


class TestAg33220AController(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = serve(
            "127.0.0.1",
            0,
        )

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
            "33220A",
            self.connection.idn(),
        )

        state = self.connection.read_state()

        self.assertEqual(
            state["function"],
            "PULS",
        )

        self.assertAlmostEqual(
            state["freq_hz"],
            10.0,
            places=6,
        )

        self.assertAlmostEqual(
            state["ampl_vpp"],
            1.05,
            places=4,
        )

        self.assertAlmostEqual(
            state["offset_v"],
            -0.05,
            places=4,
        )

        self.assertAlmostEqual(
            state["width_s"],
            1e-3,
            places=9,
        )

        self.assertEqual(
            state["ncyc"],
            1,
        )

        self.assertFalse(
            state["burst_on"]
        )

        self.assertFalse(
            state["output_on"]
        )

    def test_pulse_parameter_round_trip(self):
        self.assertAlmostEqual(
            self.connection.set_pulse_freq(
                20.0
            ),
            20.0,
            places=6,
        )

        self.assertAlmostEqual(
            self.connection.set_amplitude(
                2.0
            ),
            2.0,
            places=4,
        )

        self.assertAlmostEqual(
            self.connection.set_offset(
                0.1
            ),
            0.1,
            places=4,
        )

        self.assertAlmostEqual(
            self.connection.set_width(
                0.002
            ),
            0.002,
            places=9,
        )

        self.assertEqual(
            self.connection.set_ncycles(
                3
            ),
            3,
        )

        state = self.connection.read_state()

        self.assertAlmostEqual(
            state["freq_hz"],
            20.0,
            places=6,
        )

        self.assertAlmostEqual(
            state["ampl_vpp"],
            2.0,
            places=4,
        )

        self.assertAlmostEqual(
            state["offset_v"],
            0.1,
            places=4,
        )

        self.assertAlmostEqual(
            state["width_s"],
            0.002,
            places=9,
        )

        self.assertEqual(
            state["ncyc"],
            3,
        )

    def test_burst_round_trip(self):
        self.assertTrue(
            self.connection.set_burst(
                True
            )
        )

        state = self.connection.read_state()

        self.assertTrue(
            state["burst_on"]
        )

        self.assertEqual(
            self.server.state.burst_mode,
            "TRIG",
        )

        self.assertEqual(
            self.server.state.trig_source,
            "BUS",
        )

        self.assertFalse(
            self.connection.set_burst(
                False
            )
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

    def test_fire_does_not_enable_output(self):
        self.connection.set_burst(
            True
        )

        self.assertFalse(
            self.connection.read_state()[
                "output_on"
            ]
        )

        before = self.server.state.triggers

        self.assertTrue(
            self.connection.fire()
        )

        # Synchronization barrier: TCP preserves command order, so receiving
        # the *OPC? reply guarantees the simulator has processed *TRG.
        self.assertEqual(
            self.connection.query("*OPC?"),
            "1",
        )

        self.assertEqual(
            self.server.state.triggers,
            before + 1,
        )

        # Genesis deliberately separates FIRE from OUTPUT.
        self.assertFalse(
            self.connection.read_state()[
                "output_on"
            ]
        )

    def test_fire_with_output_enabled(self):
        self.connection.set_burst(
            True
        )

        self.connection.set_output(
            True
        )

        before = self.server.state.triggers

        self.connection.fire()

        self.assertEqual(
            self.connection.query("*OPC?"),
            "1",
        )

        self.assertEqual(
            self.server.state.triggers,
            before + 1,
        )

        self.assertTrue(
            self.connection.read_state()[
                "output_on"
            ]
        )

    def test_error_queue(self):
        self.connection.write(
            "VOLT 50"
        )

        errors = self.connection.errors()

        self.assertTrue(
            errors
        )

        self.assertIn(
            "out of range",
            errors[0].lower(),
        )

        self.assertEqual(
            self.connection.errors(),
            [],
        )


if __name__ == "__main__":
    unittest.main()