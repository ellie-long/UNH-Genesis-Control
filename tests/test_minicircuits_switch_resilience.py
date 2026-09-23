"""Automated resilience regression for Genesis Mini-Circuits switches."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest

from ssrf_ioc.minicircuits_switch import Device
from tools.sim_minicircuits_switch import serve


class FakePV:
    """Minimal EPICS-PV stand-in for device-health testing."""

    def __init__(self, value=None):
        self.value = value
        self.alarm_state = None

    def set(
        self,
        value,
        process=None,
        **kwargs,
    ):
        self.value = value

    def set_alarm(
        self,
        severity=None,
        alarm=None,
    ):
        self.alarm_state = (
            severity,
            alarm,
        )


class SwitchHealthHarness(Device):
    """Exercise the real switch reconnect path without starting SoftIOC."""

    def _create_pvs(self):
        pass

    def __init__(
        self,
        port,
    ):
        # Bypass the normal SoftIOC constructor while retaining the real
        # Genesis Device/HttpDevice connection and reconnect methods.
        self.device_name = (
            "TEST:SW"
        )

        self.settings = {
            "ip": "127.0.0.1",
            "port": int(port),
            "timeout": 0.25,
            "delay": 1.0,
            "verify_timeout_s": 0.2,
            "verify_interval_s": 0.01,
            "write_enabled": False,
            "initialize_on_connect": False,
            "channels": [
                "SWITCH"
            ],
        }

        self.channels = [
            "SWITCH"
        ]

        self.pvs = {
            "MODEL": FakePV(""),
            "SERIAL": FakePV(""),

            "PORT_CMD": FakePV(0),
            "MASK_RBV": FakePV(0),
            "STATE_RBV": FakePV(""),

            "COMM": FakePV(0),
            "ERROR": FakePV(""),
            "LAST_EVENT": FakePV(""),
            "LAST_OK": FakePV(0.0),
            "WRITE_ENABLED": FakePV(0),
        }

        self.t = None

        self._last_mask = None

        self._reconnect_fails = 0
        self._reconnect_due = 0.0

        self._set_comm(
            False
        )


class TestMiniCircuitsSwitchResilience(
    unittest.TestCase
):

    def setUp(self):
        self.server = serve(
            "127.0.0.1",
            0,
        )

        self.port = (
            self.server
            .server_address[1]
        )

        self.thread = (
            threading.Thread(
                target=(
                    self.server
                    .serve_forever
                ),
                daemon=True,
            )
        )

        self.thread.start()

        self.device = (
            SwitchHealthHarness(
                self.port
            )
        )

    def tearDown(self):
        if self.device.t is not None:
            self.device._close_connection()

        if self.server is not None:
            try:
                self.server.drop_clients()
            except Exception:
                pass

            try:
                self.server.shutdown()
            except Exception:
                pass

            try:
                self.server.server_close()
            except Exception:
                pass

        if self.thread is not None:
            self.thread.join(
                timeout=2
            )

    def stop_server(self):
        """Simulate complete switch/network disappearance."""

        server = self.server
        thread = self.thread

        server.drop_clients()
        server.shutdown()
        server.server_close()

        thread.join(
            timeout=2
        )

        self.server = None
        self.thread = None

    def restart_server(
        self,
        *,
        mask,
    ):
        """Bring a fresh simulated switch back on the same address."""

        self.server = serve(
            "127.0.0.1",
            self.port,
        )

        self.server.state.port_mask = (
            int(mask)
        )

        self.thread = (
            threading.Thread(
                target=(
                    self.server
                    .serve_forever
                ),
                daemon=True,
            )
        )

        self.thread.start()

    def test_disconnect_backoff_and_safe_recovery(
        self
    ):
        # ----------------------------------------------------------
        # 1. Initial healthy state
        # ----------------------------------------------------------

        self.assertTrue(
            self.device.connect()
        )

        self.assertTrue(
            asyncio.run(
                self.device.do_reads()
            )
        )

        self.assertEqual(
            self.device.pvs[
                "COMM"
            ].value,
            1,
        )

        self.assertEqual(
            self.device.pvs[
                "ERROR"
            ].value,
            "",
        )

        self.assertEqual(
            self.device.pvs[
                "MASK_RBV"
            ].value,
            3,
        )

        self.assertEqual(
            self.device.pvs[
                "STATE_RBV"
            ].value,
            "PORT2",
        )

        self.assertEqual(
            self.device.pvs[
                "PORT_CMD"
            ].value,
            1,
        )

        first_last_ok = (
            self.device.pvs[
                "LAST_OK"
            ].value
        )

        self.assertGreater(
            first_last_ok,
            0.0,
        )

        self.device.record_event(
            "Resilience marker"
        )

        remembered_event = (
            self.device.pvs[
                "LAST_EVENT"
            ].value
        )

        # ----------------------------------------------------------
        # 2. Switch disappears
        # ----------------------------------------------------------

        self.stop_server()

        self.assertFalse(
            asyncio.run(
                self.device.do_reads()
            )
        )

        self.assertEqual(
            self.device.pvs[
                "COMM"
            ].value,
            0,
        )

        self.assertTrue(
            self.device.pvs[
                "ERROR"
            ].value
        )

        self.assertEqual(
            self.device.pvs[
                "LAST_OK"
            ].value,
            first_last_ok,
        )

        self.assertEqual(
            self.device.pvs[
                "LAST_EVENT"
            ].value,
            remembered_event,
        )

        self.assertIsNone(
            self.device.t
        )

        # Initial hardware-read failure is not yet a reconnect failure.
        self.assertEqual(
            self.device._reconnect_fails,
            0,
        )

        # ----------------------------------------------------------
        # 3. Next poll attempts reconnect while switch is absent
        # ----------------------------------------------------------

        self.assertFalse(
            asyncio.run(
                self.device.do_reads()
            )
        )

        self.assertEqual(
            self.device._reconnect_fails,
            1,
        )

        self.assertGreater(
            self.device._reconnect_due,
            time.monotonic(),
        )

        self.assertIsNone(
            self.device.t
        )

        self.assertEqual(
            self.device.pvs[
                "COMM"
            ].value,
            0,
        )

        self.assertEqual(
            self.device.pvs[
                "LAST_OK"
            ].value,
            first_last_ok,
        )

        self.assertEqual(
            self.device.pvs[
                "LAST_EVENT"
            ].value,
            remembered_event,
        )

        # ----------------------------------------------------------
        # 4. A fresh switch returns in MIXED state
        #
        # This deliberately differs from the pre-fault PORT2 state.
        # Reconnect must READ it, not move it.
        # ----------------------------------------------------------

        self.restart_server(
            mask=1
        )

        self.device._reconnect_due = (
            0.0
        )

        self.assertTrue(
            asyncio.run(
                self.device.do_reads()
            )
        )

        self.assertIsNotNone(
            self.device.t
        )

        self.assertEqual(
            self.device.pvs[
                "COMM"
            ].value,
            1,
        )

        self.assertEqual(
            self.device.pvs[
                "MASK_RBV"
            ].value,
            1,
        )

        self.assertEqual(
            self.device.pvs[
                "STATE_RBV"
            ].value,
            "MIXED",
        )

        self.assertEqual(
            self.device.pvs[
                "ERROR"
            ].value,
            "Mixed switch state: mask 1",
        )

        self.assertGreater(
            self.device.pvs[
                "LAST_OK"
            ].value,
            first_last_ok,
        )

        self.assertEqual(
            self.device.pvs[
                "LAST_EVENT"
            ].value,
            remembered_event,
        )

        self.assertEqual(
            self.device.pvs[
                "WRITE_ENABLED"
            ].value,
            0,
        )

        self.assertEqual(
            self.device._reconnect_fails,
            0,
        )

        self.assertEqual(
            self.device._reconnect_due,
            0.0,
        )

        # Most important reconnect-safety assertion:
        # reconnect/startup must never command a route.
        request_log = (
            self.server
            .state
            .request_log
        )

        self.assertFalse(
            any(
                path.startswith(
                    "/SETP="
                )
                for path in request_log
            ),
            msg=(
                "Reconnect unexpectedly commanded "
                f"switch route: {request_log}"
            ),
        )

        self.assertTrue(
            any(
                path in (
                    "/MN?",
                    "/MN",
                )
                for path in request_log
            )
        )

        self.assertTrue(
            any(
                path in (
                    "/SN?",
                    "/SN",
                )
                for path in request_log
            )
        )

        self.assertTrue(
            any(
                path in (
                    "/SWPORT?",
                    "/SWPORT",
                )
                for path in request_log
            )
        )

        # ----------------------------------------------------------
        # 5. Hardware externally returns to coherent Port 1
        # ----------------------------------------------------------

        before_recovery_ok = (
            self.device.pvs[
                "LAST_OK"
            ].value
        )

        self.server.state.port_mask = (
            0
        )

        time.sleep(
            0.01
        )

        self.assertTrue(
            asyncio.run(
                self.device.do_reads()
            )
        )

        self.assertEqual(
            self.device.pvs[
                "MASK_RBV"
            ].value,
            0,
        )

        self.assertEqual(
            self.device.pvs[
                "STATE_RBV"
            ].value,
            "PORT1",
        )

        self.assertEqual(
            self.device.pvs[
                "ERROR"
            ].value,
            "",
        )

        self.assertGreater(
            self.device.pvs[
                "LAST_OK"
            ].value,
            before_recovery_ok,
        )

        self.assertEqual(
            self.device.pvs[
                "LAST_EVENT"
            ].value,
            remembered_event,
        )


if __name__ == "__main__":
    unittest.main()
