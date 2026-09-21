"""Automated resilience tests for the Genesis SMC100A connection path."""

from __future__ import annotations

import asyncio
import threading
import time
import unittest

from genesis_common.scpi_base import ScpiDevice
from ssrf_ioc.smc100a import DeviceConnection
from tools.sim_smc100a import serve


class FakePV:
    """Minimal PV stand-in for testing Genesis health semantics."""

    def __init__(self, value=None):
        self.value = value
        self.alarm_state = None

    def set(self, value, process=None):
        self.value = value

    def set_alarm(self, severity=None, alarm=None):
        self.alarm_state = (severity, alarm)


class SMCHealthHarness(ScpiDevice):
    """Exercise Genesis reconnect logic without starting a SoftIOC."""

    connection_class = DeviceConnection

    def _create_pvs(self):
        """No-op: resilience tests provide lightweight fake PVs manually."""
        pass

    def __init__(self, port):
        # Deliberately do not call the SoftIOC/BaseDevice constructor.
        # This harness tests the transport and Genesis health state only.
        self.device_name = "TEST:SMC"

        self.settings = {
            "ip": "127.0.0.1",
            "port": port,
            "timeout": 0.25,
            "settle": 0.0,
            "write_enabled": False,
        }

        self.channels = ["SMC"]

        self.pvs = {
            "COMM": FakePV(0),
            "ERROR": FakePV(""),
            "LAST_EVENT": FakePV(""),
            "LAST_OK": FakePV(0.0),
            "WRITE_ENABLED": FakePV(0),
            "FREQ_RBV": FakePV(0.0),
        }

        self.t = None

        self._reconnect_fails = 0
        self._reconnect_due = 0.0

        self.alarmed = set()

        self._set_comm(False)

    def _post_connect(self):
        identity = self.t.idn()

        if "SMC100A" not in identity.upper():
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
        return ["FREQ_RBV"]

    def set_alarm(self, pv_name):
        self.alarmed.add(pv_name)

    def remove_alarm(self, pv_name):
        self.alarmed.discard(pv_name)

    def read_all(self, channel, index):
        state = self.t.read_state()

        return {
            "FREQ_RBV": state["freq_mhz"],
        }


class TestSMC100AResilience(unittest.TestCase):

    def setUp(self):
        # Port 0 asks the OS for an unused local TCP port.
        self.server = serve("127.0.0.1", 0)
        self.port = self.server.server_address[1]

        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

        self.device = SMCHealthHarness(self.port)

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
        """Drop active clients and stop the simulated instrument."""

        server = self.server
        thread = self.thread

        server.drop_clients()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

        self.server = None
        self.thread = None

    def restart_server(self):
        """Restart a fresh simulated instrument on the same TCP port."""

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
        # 1. Healthy initial connection/read
        # ----------------------------------------------------------

        self.assertTrue(self.device.connect())

        self.assertTrue(
            asyncio.run(self.device.do_reads())
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
            self.device.pvs["FREQ_RBV"].value,
            33.0,
            places=6,
        )

        first_last_ok = self.device.pvs["LAST_OK"].value

        self.assertGreater(
            first_last_ok,
            0.0,
        )

        # Operational history must survive communication faults.
        self.device.record_event(
            "Write blocked: read-only mode"
        )

        remembered_event = (
            self.device.pvs["LAST_EVENT"].value
        )

        # ----------------------------------------------------------
        # 2. Instrument disappears
        # ----------------------------------------------------------

        self.stop_server()

        self.assertFalse(
            asyncio.run(self.device.do_reads())
        )

        self.assertEqual(
            self.device.pvs["COMM"].value,
            0,
        )

        self.assertTrue(
            self.device.pvs["ERROR"].value
        )

        # No successful read occurred while disconnected.
        self.assertEqual(
            self.device.pvs["LAST_OK"].value,
            first_last_ok,
        )

        # Fault reporting must not overwrite operational history.
        self.assertEqual(
            self.device.pvs["LAST_EVENT"].value,
            remembered_event,
        )

        # The first failed reconnect schedules the 2 s backoff.
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
        # 3. Instrument returns on the same address
        # ----------------------------------------------------------

        self.restart_server()

        # Do not make the unit test sleep for the full production
        # backoff interval. Allow the next retry immediately.
        self.device._reconnect_due = 0.0

        # With no active connection, this cycle reconnects but does
        # not yet perform the measurement read.
        self.assertFalse(
            asyncio.run(self.device.do_reads())
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
        # 4. First successful read after reconnect
        # ----------------------------------------------------------

        time.sleep(0.01)

        self.assertTrue(
            asyncio.run(self.device.do_reads())
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
            self.device.pvs["FREQ_RBV"].value,
            33.0,
            places=6,
        )

        self.assertEqual(
            self.device.pvs["LAST_EVENT"].value,
            remembered_event,
        )

        self.assertEqual(
            self.device.pvs["WRITE_ENABLED"].value,
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


if __name__ == "__main__":
    unittest.main()
