"""Common Genesis Modbus/TCP support for Datexel-style devices."""

from __future__ import annotations

import logging
import time

from pyModbusTCP.client import ModbusClient

from .base_device import GenesisBaseDevice


log = logging.getLogger(__name__)

_HEALTH_PVS = {
    "COMM",
    "ERROR",
    "LAST_EVENT",
    "LAST_OK",
    "WRITE_ENABLED",
}


class ModbusDevice(GenesisBaseDevice):
    """Base class for Modbus/TCP devices."""

    def _create_connection(self):
        return ModbusConnection(
            self.settings["ip"],
            self.settings["port"],
            self.settings["timeout"],
        )

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
        """Generic Modbus read implementation."""

        if self.t is None:
            self._set_comm(False, "Instrument disconnected")
            self.reconnect()
            return False

        try:
            readings = self.t.read_all()

            if readings is None:
                raise OSError("instrument returned no readings")

            for i, channel in enumerate(self._skip_none_channels()):
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
                "%s: Modbus read failed: %s",
                self.device_name,
                exc,
            )
            self._handle_read_error(str(exc))
            return False

    def _process_reading(self, channel, raw_value):
        """Process a raw reading; child classes may override."""
        return raw_value


class ModbusConnection:
    """Generic Modbus/TCP connection handler."""

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)

        self.m = ModbusClient(
            host=self.host,
            port=self.port,
            unit_id=1,
            timeout=self.timeout,
            auto_open=True,
        )

    def read_all(self, start=40, number=8):
        return self.read_registers(start, number)

    def read_registers(self, start, number):
        """Read input registers starting at ``start``."""

        try:
            values = self.m.read_input_registers(
                start,
                number,
            )
        except Exception as exc:
            raise OSError(
                f"Modbus read failed on {self.host}: {exc}"
            ) from exc

        if values is None:
            raise OSError(
                f"Modbus read failed on {self.host}"
            )

        return values

    def set_register(self, number, value):
        """Write one holding register and require acknowledgement."""

        try:
            ok = self.m.write_single_register(
                number,
                value,
            )
        except Exception as exc:
            raise OSError(
                f"Modbus write failed on {self.host}: {exc}"
            ) from exc

        if not ok:
            raise OSError(
                f"Modbus write failed on {self.host}"
            )

        return True

    def close(self):
        client = getattr(self, "m", None)

        if client is not None:
            try:
                client.close()
            except Exception:
                log.exception(
                    "Error closing Modbus connection to %s:%s",
                    self.host,
                    self.port,
                )
