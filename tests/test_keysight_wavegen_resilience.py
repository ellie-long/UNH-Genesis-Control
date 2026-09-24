"""Automated resilience tests for the Genesis Keysight WaveGen path."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
import time
import unittest

import yaml

from ssrf_ioc.keysight_wavegen import Device
from tools.sim_keysight_wavegen import start_server


class FakePV:
    """Minimal PV stand-in for Genesis health-state testing."""

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


class KeysightHealthHarness(Device):
    """Exercise real Genesis VISA reconnect logic without a SoftIOC."""

    def _create_pvs(self):
        """No-op because lightweight fake PVs are installed manually."""
        pass

    def __init__(self, port):
        # Deliberately bypass the normal SoftIOC/BaseDevice constructor.
        # The harness exercises the real Device, VisaDevice, and
        # DeviceConnection reconnect path.

        self.device_name = (
            "TEST:WGEN"
        )

        repo_root = (
            Path(__file__)
            .resolve()
            .parents[1]
        )

        config_path = (
            repo_root
            / "configs"
            / "ssrf"
            / "keysight_wavegen"
            / "settings.yaml"
        )

        with config_path.open() as stream:
            config = yaml.safe_load(
                stream
            )

        self.settings = copy.deepcopy(
            config[
                "keysight_wavegen"
            ]
        )

        self.settings[
            "resource"
        ] = (
            "TCPIP0::127.0.0.1::"
            f"{port}::SOCKET"
        )

        # Resilience regression is always read-only.
        self.settings[
            "write_enabled"
        ] = False

        self.channels = [
            "WGEN"
        ]

        self.pvs = {
            # Genesis health behavior
            "COMM": FakePV(0),
            "ERROR": FakePV(""),
            "LAST_EVENT": FakePV(""),
            "LAST_OK": FakePV(0.0),
            "WRITE_ENABLED": FakePV(0),

            # Identity
            "IDN": FakePV(""),

            # Command synchronization
            "OUTPUT_CMD": FakePV(0),
            "PERIOD_CMD": FakePV(0.0),
            "VHIGH_CMD": FakePV(0.0),
            "VLOW_CMD": FakePV(0.0),

            # Scalar readbacks
            "OUTPUT_RBV": FakePV(0),
            "PERIOD_RBV": FakePV(0.0),
            "VHIGH_RBV": FakePV(0.0),
            "VLOW_RBV": FakePV(0.0),

            # Waveform verification state
            "WAVEFORM_READY": FakePV(0),
            "ARB_POINTS_RBV": FakePV(0),
        }

        self.t = None

        self._reconnect_fails = 0
        self._reconnect_due = 0.0

        self.alarmed = set()

        self._set_comm(False)

    def _skip_none_channels(
        self,
        channels=None,
    ):
        if channels is None:
            channels = self.channels

        return [
            channel
            for channel in channels
            if "None" not in channel
        ]

    def _channel_pvs(
        self,
        channel,
    ):
        return list(
            self.READBACK_PVS
        )

    def set_alarm(
        self,
        pv_name,
    ):
        self.alarmed.add(
            pv_name
        )

    def remove_alarm(
        self,
        pv_name,
    ):
        self.alarmed.discard(
            pv_name
        )


class TestKeysightWaveGenResilience(
    unittest.TestCase
):

    def setUp(self):
        (
            self.server,
            self.thread,
            self.simulator,
        ) = start_server(
            "127.0.0.1",
            0,
            verbose=False,
        )

        self.port = (
            self.server
            .server_address[1]
        )

        self.device = (
            KeysightHealthHarness(
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
        """Drop clients and stop the simulated WaveGen."""

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
        self.simulator = None

    def restart_server(self):
        """Restart the simulated instrument on the same TCP port."""

        (
            self.server,
            self.thread,
            self.simulator,
        ) = start_server(
            "127.0.0.1",
            self.port,
            verbose=False,
        )

    def test_disconnect_backoff_and_recovery(
        self
    ):
        # ----------------------------------------------------------
        # 1. Healthy initial connection/read
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
                "OUTPUT_RBV"
            ].value,
            0,
        )

        self.assertAlmostEqual(
            self.device.pvs[
                "PERIOD_RBV"
            ].value,
            0.01,
            places=8,
        )

        self.assertAlmostEqual(
            self.device.pvs[
                "VHIGH_RBV"
            ].value,
            0.7,
            places=8,
        )

        self.assertAlmostEqual(
            self.device.pvs[
                "VLOW_RBV"
            ].value,
            -0.7,
            places=8,
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

        # Preserve operational history through a communication fault.
        self.device.record_event(
            "Waveform staged: 8192 pts"
        )

        remembered_event = (
            self.device.pvs[
                "LAST_EVENT"
            ].value
        )

        # Pretend a waveform was previously verified. Reconnection
        # must invalidate this because the instrument may have changed.
        self.device.pvs[
            "WAVEFORM_READY"
        ].set(1)

        self.device.pvs[
            "ARB_POINTS_RBV"
        ].set(8192)

        # ----------------------------------------------------------
        # 2. Instrument disappears
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

        # LAST_OK must freeze while communication is lost.
        self.assertEqual(
            self.device.pvs[
                "LAST_OK"
            ].value,
            first_last_ok,
        )

        # Communication faults must not erase operational history.
        self.assertEqual(
            self.device.pvs[
                "LAST_EVENT"
            ].value,
            remembered_event,
        )

        # The first failed hardware read disconnects the device,
        # but it is not itself a reconnect attempt.
        self.assertEqual(
            self.device._reconnect_fails,
            0,
        )

        self.assertIsNone(
            self.device.t
        )

        # A subsequent polling cycle now attempts the reconnect while
        # the instrument is still absent. That failed reconnect is what
        # starts the production backoff counter/timer.
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

        # Health semantics remain faulted while reconnect fails.
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

        # ----------------------------------------------------------
        # 3. Instrument returns on same VISA/TCP resource
        # ----------------------------------------------------------

        self.restart_server()

        # Do not sleep through production reconnect delays in a unit test.
        self.device._reconnect_due = 0.0

        # The Keysight VISA path reconnects and completes the
        # normal measurement read in the same polling cycle.
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
                "ERROR"
            ].value,
            "",
        )

        # Reconnection must invalidate previous waveform verification.
        self.assertEqual(
            self.device.pvs[
                "WAVEFORM_READY"
            ].value,
            0,
        )

        self.assertEqual(
            self.device.pvs[
                "ARB_POINTS_RBV"
            ].value,
            0,
        )

        # ----------------------------------------------------------
        # 4. First successful polling cycle after reconnect
        # ----------------------------------------------------------

        time.sleep(0.01)

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

        self.assertGreater(
            self.device.pvs[
                "LAST_OK"
            ].value,
            first_last_ok,
        )

        self.assertEqual(
            self.device.pvs[
                "OUTPUT_RBV"
            ].value,
            0,
        )

        self.assertAlmostEqual(
            self.device.pvs[
                "PERIOD_RBV"
            ].value,
            0.01,
            places=8,
        )

        self.assertAlmostEqual(
            self.device.pvs[
                "VHIGH_RBV"
            ].value,
            0.7,
            places=8,
        )

        self.assertAlmostEqual(
            self.device.pvs[
                "VLOW_RBV"
            ].value,
            -0.7,
            places=8,
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
            self.device.pvs[
                "WAVEFORM_READY"
            ].value,
            0,
        )

        self.assertEqual(
            self.device.pvs[
                "ARB_POINTS_RBV"
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


if __name__ == "__main__":
    unittest.main()
