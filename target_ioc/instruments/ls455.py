r"""
devices/instruments/ls455.py - Lake Shore Model 455 DSP gaussmeter (fringe field).

Port of Genesis/Pressure/fringe_gaussmeter_tcp.py.  RS-232 only (7 data bits,
odd parity, 19200 on the lab's gateway port), so it goes through
devices/serial_tcp_base.py rather than scpi_base.

Protocol: SCPI-ish ASCII terminated by CR/LF -

    *IDN?        -> LSCI,MODEL455,<serial>,<firmware>
    RDGFIELD?    -> +1.23456E+00        field in the instrument's display unit
    UNIT?        -> 1..4  (G, T, Oe, A/m)

PVs per channel (channel 'Fringe' -> UNH:Fringe_Field, ...):

    _Field    the reading in the instrument's own unit  (see _Unit)
    _FieldT   the same reading converted to tesla, so alarm limits and archived
              history stay meaningful even if someone changes the front-panel
              unit
    _Unit     G / T / Oe / A/m, read at start-up and on every cycle
    _IDN      identity

The conversion is a scale and an offset from settings.yaml
(``tesla_scale``/``tesla_offset``, the same two numbers the original script took
on the command line); the default 1e-4 is gauss -> tesla.

settings.yaml
    ls455:
      module: 'target_ioc.instruments.ls455'
      ip: '192.168.1.251'      # the gateway
      port: 5007               # gateway serial port the 455 is on
      timeout: 2
      delay: 1
      tesla_scale: 1.0e-4      # tesla = (reading - offset) * scale
      tesla_offset: 0.0
      channels: [Fringe]
"""

import logging
import re

from softioc import builder

from genesis_common.serial_tcp_base import SerialTcpConnection, SerialTcpDevice

log = logging.getLogger(__name__)

FLOAT_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
UNITS = {"1": "G", "2": "T", "3": "Oe", "4": "A/m"}


class Device(SerialTcpDevice):

    def _create_pvs(self):
        for ch in self._skip_none_channels():
            self.pvs[ch + "_Field"] = builder.aIn(ch + "_Field", PREC=5, **self.analog)
            self.pvs[ch + "_FieldT"] = builder.aIn(ch + "_FieldT", EGU="T", PREC=6, **self.analog)
            self.pvs[ch + "_Unit"] = builder.stringIn(ch + "_Unit")
            self.pvs[ch + "_IDN"] = builder.stringIn(ch + "_IDN")

    connection_class = None                     # set below

    def read_outs(self):
        if self.t is None:
            return
        for ch in self._skip_none_channels():
            try:
                self.pvs[ch + "_IDN"].set(self.t.idn()[:39])
                log.info("%s: %s", ch, self.pvs[ch + "_IDN"].get())
            except OSError as e:
                log.error("%s: identity read failed: %s", ch, e)

    def read_all(self, channel, index):
        field = self.t.read_field()
        unit = self.t.unit()
        scale = float(self.settings.get("tesla_scale", 1.0e-4))
        offset = float(self.settings.get("tesla_offset", 0.0))
        values = {channel + "_Field": field,
                  channel + "_Unit": unit[:39],
                  channel + "_FieldT": (field - offset) * scale}
        try:                                     # the unit is the EGU of _Field
            self.pvs[channel + "_Field"].EGU = unit
        except Exception:                        # noqa: BLE001 - cosmetic only
            pass
        return values


class DeviceConnection(SerialTcpConnection):
    """Lake Shore 455 ASCII, CR/LF terminated."""

    eol = b"\r\n"

    def idn(self):
        return self.query_line("*IDN?")

    def read_field(self):
        raw = self.query_line("RDGFIELD?")
        m = FLOAT_RE.search(raw)
        if not m:
            raise OSError(f"{self.host}: unparsed 455 field reply {raw!r}")
        return float(m.group(0))

    def unit(self):
        return UNITS.get(self.query_line("UNIT?").strip(), "G")


Device.connection_class = DeviceConnection
