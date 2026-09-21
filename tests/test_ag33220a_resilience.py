"""Automated resilience tests for the Genesis Agilent 33220A path."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest

from genesis_common.scpi_base import ScpiDevice
from ssrf_ioc.ag33220a import DeviceConnection
from tools.sim_ag33220a import serve


class FakePV:
    def __init__(self, value=None):
        self.value = value
        self.alarm_state = None

    def set(self, value, process=None):
        self.value = value

    def set_alarm(self, severity=None, alarm=None):
        self.alarm_state = (severity, alarm)


class AgilentHealthHarness(ScpiDevice):
    """Minimal ScpiDevice using the real Genesis 33220A connection layer."""

    connection_class = DeviceConnection

    def _create_pvs(self):
        # Tests provide lightweight fake PVs manually.
        pass

    def __init__(self, port):
        self.device_name = "TEST:AG33220A"

        self.settings = {
            "ip": "127.0.0.1",
            "port": port,
            "timeout": 0.25,
            "settle": 0.0,
            "write_enabled": False,
        }

        self.channels = ["TRIG"]

        self.pvs = {
            "COMM": FakePV(0),
            "ERROR": FakePV(""),
            "LAST_EVENT": FakePV(""),
            "LAST_OK": FakePV(0.0),
            "WRITE_ENABLED": FakePV(0),
            "PULSE_FREQ_RBV": FakePV(0.0),
            "OUTPUT_RBV": FakePV(0),
        }

        self.t = None
        self._reconnect_fails = 0
        self._reconnect_due = 0.0

        self.alarmed = set()

        self._set_comm(False)

    def _post_connect(self):
        identity = self.t.idn()

        if "33220A" not in identity.upper():
            raise OSError(
                f"unexpected instrument identity: {identity}"
            )

    def _skip_none_channels(self, channels=None):
        if channels is None:
            channels = self.channels

        return [
            channel
            for channel in channels
            if "None" not in channel
        ]

    def _channel_pvs(self, channel):
        return [
            "PULSE_FREQ_RBV",
            "OUTPUT_RBV",
        ]

    def set_alarm(self, pv_name):
        self.alarmed.add(pv_name)

    def remove_alarm(self, pv_name):
        self.alarmed.discard(pv_name)

    def read_all(self, channel, index):
        state = self.t.read_state()

        return {
            "PULSE_FREQ_RBV": state["freq_hz"],
            "OUTPUT_RBV": int(state["output_on"]),
        }


class TestAgilent33220AResilience(unittest.TestCase):

    def setUp(self):
        self.server = serve(
            "127.0.0.1",
            0,
        )

        self.port = self.server.server_address[1]

        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )

        self.thread.start()

        self.device = AgilentHealthHarness(
            self.port
        )

    def tearDown(self):
        if self.device.t is not None:
            self.device._close_connection()

        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:
                pass

            try:
                self.server.server_close()
            except Exception:
                pass

        if self.thread is not None:
            self.thread.join(timeout=2)

    def stop_server(self):
        server = self.server
        thread = self.thread

        # Force the currently established TCP connection down.
        server.drop_clients()

        server.shutdown()
        server.server_close()

        thread.join(timeout=2)

        self.server = None
        self.thread = None

    def restart_server(self):
        self.server = serve(
            "127.0.0.1",
            self.port,
        )

        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )

        self.thread.start()

    def test_disconnect_backoff_and_recovery(self):
        # ----------------------------------------------------------
        # Healthy initial connection
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
            self.device.pvs["COMM"].value,
            1,
        )

        self.assertEqual(
            self.device.pvs["ERROR"].value,
            "",
        )

        self.assertAlmostEqual(
            self.device.pvs[
                "PULSE_FREQ_RBV"
            ].value,
            10.0,
            places=6,
        )

        self.assertEqual(
            self.device.pvs[
                "OUTPUT_RBV"
            ].value,
            0,
        )

        first_last_ok = self.device.pvs[
            "LAST_OK"
        ].value

        self.assertGreater(
            first_last_ok,
            0.0,
        )

        # LAST_EVENT should survive a communications fault.
        self.device.record_event(
            "Write blocked: read-only mode"
        )

        remembered_event = self.device.pvs[
            "LAST_EVENT"
        ].value

        # ----------------------------------------------------------
        # Forced instrument/network loss
        # ----------------------------------------------------------

        self.stop_server()

        self.assertFalse(
            asyncio.run(
                self.device.do_reads()
            )
        )

        self.assertEqual(
            self.device.pvs["COMM"].value,
            0,
        )

        self.assertTrue(
            self.device.pvs["ERROR"].value
        )

        # No successful read happened during the failure.
        self.assertEqual(
            self.device.pvs["LAST_OK"].value,
            first_last_ok,
        )

        # Operational history is preserved.
        self.assertEqual(
            self.device.pvs[
                "LAST_EVENT"
            ].value,
            remembered_event,
        )

        # Immediate reconnect should have failed and scheduled
        # the normal Genesis 2-second first backoff.
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

        # ----------------------------------------------------------
        # Instrument returns
        # ----------------------------------------------------------

        self.restart_server()

        # Avoid waiting two real seconds in a unit test.
        self.device._reconnect_due = 0.0

        # With t=None, the first polling call reconnects. By design,
        # it returns False because it does not perform the read cycle
        # until the next poll.
        self.assertFalse(
            asyncio.run(
                self.device.do_reads()
            )
        )

        self.assertIsNotNone(
            self.device.t
        )

        self.assertEqual(
            self.device.pvs["COMM"].value,
            1,
        )

        self.assertEqual(
            self.device.pvs["ERROR"].value,
            "",
        )

        # ----------------------------------------------------------
        # First complete post-recovery read
        # ----------------------------------------------------------

        time.sleep(0.01)

        self.assertTrue(
            asyncio.run(
                self.device.do_reads()
            )
        )

        self.assertEqual(
            self.device.pvs["COMM"].value,
            1,
        )

        self.assertEqual(
            self.device.pvs["ERROR"].value,
            "",
        )

        self.assertGreater(
            self.device.pvs["LAST_OK"].value,
            first_last_ok,
        )

        self.assertAlmostEqual(
            self.device.pvs[
                "PULSE_FREQ_RBV"
            ].value,
            10.0,
            places=6,
        )

        self.assertEqual(
            self.device.pvs[
                "OUTPUT_RBV"
            ].value,
            0,
        )

        self.assertEqual(
            self.device.pvs[
                "LAST_EVENT"
            ].value,
            remembered_event,
        )

        # Reconnecting never changes write permission.
        self.assertEqual(
            self.device.pvs[
                "WRITE_ENABLED"
            ].value,
            0,
        )

        # Successful reconnect resets backoff state.
        self.assertEqual(
            self.device._reconnect_fails,
            0,
        )

        self.assertEqual(
            self.device._reconnect_due,
            0.0,
        )


if __name__ == "__main__":
    unittest.main()