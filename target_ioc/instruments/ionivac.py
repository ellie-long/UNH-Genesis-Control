r"""
devices/instruments/ionivac.py - Leybold IONIVAC combination vacuum gauge.

Port of Genesis/Pressure/ionivac_gui_tcp.py.  RS-232 only, on a serial-to-
Ethernet gateway (see devices/serial_tcp_base.py).

This one is different from every other instrument in the deployment: it is
never asked for anything.  It TALKS, continuously, in 7-byte binary telegrams
that start with the two bytes 07 05:

    byte  0 1     header, always 07 05
    byte  4 5     the reading, as a 16-bit count  (byte4 * 256 + byte5)
    pressure(Torr) = 10 ** (count / 4000 - 12.625)      (from the LabVIEW original)

So a polling cycle here is "take everything the gateway has buffered, find the
NEWEST complete telegram, convert it".  Older telegrams in the buffer are
history and are dropped on purpose - a control screen wants the present value,
not a queue of stale ones.  If nothing has arrived since the last cycle the
read raises OSError, the channel alarms and the IOC reconnects; on a gauge that
should speak about ten times a second, silence is a fault.

PVs per channel (channel 'Ion' -> UNH:Ion_PI, ...):

    _PI      Torr   pressure
    _Raw     counts the 16-bit number the telegram carried (useful when the
                    conversion above ever has to be re-checked against the gauge)

settings.yaml
    ionivac:
      module: 'target_ioc.instruments.ionivac'
      ip: '192.168.1.251'      # the gateway
      port: 5005               # gateway serial port the IONIVAC is on
      timeout: 2
      delay: 1
      channels: [Ion]
"""

import logging
import math

from softioc import builder

from genesis_common.serial_tcp_base import SerialTcpConnection, SerialTcpDevice

log = logging.getLogger(__name__)

FRAME_HEADER = bytes((7, 5))
FRAME_SIZE = 7


class Device(SerialTcpDevice):

    def _create_pvs(self):
        for ch in self._skip_none_channels():
            self.pvs[ch + "_PI"] = builder.aIn(ch + "_PI", EGU="Torr", PREC=4, **self.analog)
            self.pvs[ch + "_Raw"] = builder.longIn(ch + "_Raw", EGU="counts")

    connection_class = None                     # set below

    def read_all(self, channel, index):
        pressure, raw = self.t.read_pressure()
        return {channel + "_PI": pressure, channel + "_Raw": raw}


class DeviceConnection(SerialTcpConnection):
    """A listener, not a talker."""

    def latest_frame(self):
        """The newest complete 07 05 telegram waiting on the socket."""
        data = self.read_available()
        if not data:
            raise OSError(f"{self.host}:{self.port}: IONIVAC sent nothing "
                          f"(gateway port wrong, or the gauge is off?)")
        # scan from the end: the last complete frame is the current reading
        for start in range(len(data) - FRAME_SIZE, -1, -1):
            if data[start:start + 2] == FRAME_HEADER:
                return data[start:start + FRAME_SIZE]
        raise OSError(f"{self.host}:{self.port}: no 07 05 telegram in "
                      f"{len(data)} bytes (baud rate mismatch?)")

    @staticmethod
    def pressure_from_frame(frame):
        raw = frame[4] * 256 + frame[5]
        pressure = 10.0 ** (raw / 4000.0 - 12.625)
        if not math.isfinite(pressure) or pressure <= 0:
            raise OSError(f"IONIVAC: impossible pressure from raw {raw}")
        return pressure, raw

    def read_pressure(self):
        return self.pressure_from_frame(self.latest_frame())


Device.connection_class = DeviceConnection
