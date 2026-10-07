"""Common Genesis support for legacy Telnet/raw-TCP instrument drivers."""

from __future__ import annotations

import logging
import time

from .base_device import GenesisBaseDevice
from .telnet_socket import TelnetSocket


log = logging.getLogger(__name__)

_HEALTH_PVS = {
    "COMM",
    "ERROR",
    "LAST_EVENT",
    "LAST_OK",
    "WRITE_ENABLED",
}


class TelnetDevice(GenesisBaseDevice):
    """Base class for Telnet/raw serial-over-Ethernet devices."""

    def _channel_pvs(self, channel):
        return [
            name
            for name in self.pvs
            if (
                name == channel
                or name.startswith(channel + "_")
            )
            and name not in _HEALTH_PVS
        ]

    def _handle_read_success(self, channels=None):
        for channel in self._skip_none_channels(channels):
            for name in self._channel_pvs(channel):
                self.remove_alarm(name)

        self.pvs["LAST_OK"].set(time.time())
        self._set_comm(True)

    def _handle_read_error(self, message="Instrument read failed", channels=None):
        for channel in self._skip_none_channels(channels):
            for name in self._channel_pvs(channel):
                self.set_alarm(name)

        self._set_comm(False, message)
        self.reconnect()

    async def do_reads(self):
        """Generic Telnet read implementation."""

        if self.t is None:
            self._set_comm(False, "Instrument disconnected")
            self.reconnect()
            return False

        try:
            readings = self.t.read_all()

            if readings is None:
                raise OSError("instrument returned no readings")

            for i, channel in enumerate(self.channels):
                if "None" in channel:
                    continue

                processed_value = self._process_reading(
                    channel,
                    readings[i],
                )
                self.pvs[channel].set(processed_value)

            self._handle_read_success()
            return True

        except (
            OSError,
            TypeError,
            ValueError,
            IndexError,
            AttributeError,
        ) as exc:
            log.warning(
                "%s: Telnet read failed: %s",
                self.device_name,
                exc,
            )
            self._handle_read_error(str(exc))
            return False

    def _process_reading(self, channel, raw_value):
        """Process a raw reading; child classes may override."""
        return raw_value


class TelnetConnection:
    """Generic buffered TCP/Telnet-compatible connection."""

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.tn = None

        try:
            self.tn = TelnetSocket(
                self.host,
                port=self.port,
                timeout=self.timeout,
            )
        except OSError as exc:
            raise OSError(
                f"connect to {self.host}:{self.port} failed: {exc}"
            ) from exc

    def close(self):
        tn = self.tn
        self.tn = None

        if tn is not None:
            tn.close()
