"""Software-only tests for the Genesis Mini-Circuits switch controller."""

from __future__ import annotations

import threading
import unittest

from ssrf_ioc.minicircuits_switch import (
    DeviceConnection,
)
from tools.sim_minicircuits_switch import (
    State,
    serve,
)


class TestMiniCircuitsSwitchController(
    unittest.TestCase
):

    @classmethod
    def setUpClass(cls):
        # Port 0 asks the OS for an unused local TCP port.
        cls.server = serve(
            "127.0.0.1",
            0,
        )

        cls.port = (
            cls.server
            .server_address[1]
        )

        cls.thread = (
            threading.Thread(
                target=(
                    cls.server
                    .serve_forever
                ),
                daemon=True,
            )
        )

        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

        cls.thread.join(
            timeout=2
        )

    def setUp(self):
        # Fresh simulated hardware state for every test.
        self.server.state = State()

        self.connection = (
            DeviceConnection(
                "127.0.0.1",
                port=self.port,
                timeout=1.0,
            )
        )

    def tearDown(self):
        self.connection.close()

    def test_identity_and_default_state(
        self
    ):
        self.assertEqual(
            self.connection.model(),
            "RC-2SPDT-A18",
        )

        self.assertEqual(
            self.connection.serial(),
            "SIM0001",
        )

        self.assertEqual(
            self.connection.read_port_mask(),
            3,
        )

        self.assertEqual(
            self.connection.state_name(3),
            "PORT2",
        )

    def test_port1_round_trip(
        self
    ):
        result = (
            self.connection
            .set_port1()
        )

        self.assertEqual(
            result,
            0,
        )

        self.assertEqual(
            self.connection
            .read_port_mask(),
            0,
        )

        self.assertEqual(
            self.connection
            .state_name(0),
            "PORT1",
        )

    def test_port2_round_trip(
        self
    ):
        self.server.state.port_mask = (
            0
        )

        result = (
            self.connection
            .set_port2()
        )

        self.assertEqual(
            result,
            3,
        )

        self.assertEqual(
            self.connection
            .read_port_mask(),
            3,
        )

    def test_exact_protocol_paths(
        self
    ):
        self.connection.model()
        self.connection.serial()

        self.connection.set_port1()

        log = (
            self.server
            .state
            .request_log
        )

        # HTTP libraries may strip an empty trailing '?'.
        self.assertTrue(
            "/MN?" in log
            or "/MN" in log
        )

        self.assertTrue(
            "/SN?" in log
            or "/SN" in log
        )

        self.assertIn(
            "/SETP=0",
            log,
        )

        self.assertTrue(
            "/SWPORT?" in log
            or "/SWPORT" in log
        )

    def test_mixed_switch_state_is_not_hidden(
        self
    ):
        # 1 and 2 mean the two internal SPDTs disagree.
        # The controller must report this state exactly.
        self.server.state.port_mask = (
            1
        )

        self.assertEqual(
            self.connection
            .read_port_mask(),
            1,
        )

        self.assertEqual(
            self.connection
            .state_name(1),
            "MIXED",
        )

        self.server.state.port_mask = (
            2
        )

        self.assertEqual(
            self.connection
            .read_port_mask(),
            2,
        )

        self.assertEqual(
            self.connection
            .state_name(2),
            "MIXED",
        )

    def test_invalid_combined_command_rejected(
        self
    ):
        for value in (
            1,
            2,
            -1,
            4,
        ):
            with self.assertRaises(
                ValueError
            ):
                self.connection.set_port_mask(
                    value
                )

    def test_command_requires_verified_readback(
        self
    ):
        # Simulate the HTTP command being received while the physical
        # switch fails to move. The controller must not report success.
        self.server.state.port_mask = (
            3
        )

        self.server.state.ignore_set = (
            True
        )

        with self.assertRaises(
            OSError
        ):
            self.connection.set_port_mask(
                0,
                verify_timeout_s=0.05,
                verify_interval_s=0.005,
            )

        self.assertEqual(
            self.connection
            .read_port_mask(),
            3,
        )


if __name__ == "__main__":
    unittest.main()
