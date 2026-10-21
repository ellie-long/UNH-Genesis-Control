"""
devices/instruments/ami135.py - the MAGNET liquid-helium level monitor.

The instrument
--------------
An American Magnetics (AMI) Model 135 helium level monitor.  Unlike the
fridge's Model 1700, this one has no Ethernet of its own: it is RS-232, wired
to the DIEWU serial-to-Ethernet gateway at 192.168.1.250, on the port the
box exposes as TCP 23.

That is the SAME gateway, and the same IP, as the IPS120-10 magnet power
supply on 5001 - a different serial port of one box.  Each port is its own
socket, so this IOC and the magnet IOC do not compete, even though the
address they print is identical.  Worth knowing before somebody sees
192.168.1.250 in two places and assumes a mistake.

Port 23 is telnet's well-known number, but nothing here speaks Telnet: the
gateway passes bytes through untouched, exactly as it does for the gauges on
5004, 5005, 5007 and 5011.  See devices/serial_tcp_base.py.

The conversation
----------------
    send     'LEVEL' + CRLF
    receive  a line terminated by CRLF, holding a number

Taken verbatim from the user's own Pressure/magnet_lhe_level_gui.py.

The echo
--------
The AMI 135's DIP switches can enable command echo, and this one has it on:
the first line back is the command itself, and the reading is the SECOND
line.  So a reply equal to the command is swallowed and another line read.
Without that, every poll would try to parse a number out of the word LEVEL,
fail, and report PARSE_ERR forever - while the instrument was answering
perfectly.

Units
-----
The 135 reports centimetres; 0.393 converts to inches, which is what the
cryostat screens and the LabVIEW panel show.  The constant lives in
settings.yaml with the other calibrations, not here, so a probe re-zeroed or
re-scaled is a configuration change somebody can see in a diff.

PVs per channel (channel 'Magnet' -> UNH:Magnet_LI, ...)

    _LI      inch   level: raw * scale
    _Raw     cm     what the monitor actually reported
    _Status  text   OK, or why the last reply did not yield a number
    _Conn    bool   built by ScpiDevice: is this IOC talking to the gateway

settings.yaml
    lhe_magnet:
      module: 'devices.instruments.ami135'
      autostart: True
      ip: '192.168.1.250'      # the DIEWU gateway - same box as the IPS120-10
      port: 23                 # a different serial port of that box
      timeout: 5               # this one is slow to answer
      delay: 5
      query: 'LEVEL'
      scale: 0.393             # centimetres -> inches
      channels: [Magnet]
"""

import logging
import math
import re

from softioc import builder

from genesis_common.serial_tcp_base import SerialTcpConnection, SerialTcpDevice

log = logging.getLogger(__name__)

FLOAT_RE = re.compile(rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")
EOL = b"\r\n"


from genesis_common.cryo_readonly_health import CryoReadOnlyHealth


class Device(CryoReadOnlyHealth, SerialTcpDevice):
    """EPICS side: inches for the screens, centimetres as reported."""
    CRYO_NUMERIC_SUFFIXES = ('_LI', '_Raw')

    def _create_pvs(self):
        for ch in self._skip_none_channels():
            self.pvs[ch + "_LI"] = builder.aIn(ch + "_LI", EGU="inch", PREC=3,
                                               **self.analog)
            self.pvs[ch + "_Raw"] = builder.aIn(ch + "_Raw", EGU="cm", PREC=3,
                                                **self.analog)
            self.pvs[ch + "_Status"] = builder.stringIn(ch + "_Status")

    connection_class = None                     # set below

    def _create_connection(self):
        conn = super()._create_connection()
        conn.query = str(self.settings.get("query", "LEVEL"))
        return conn

    def read_all(self, channel, index):
        raw, status = self.t.read_level()
        scale = float(self.settings.get("scale", 0.393))
        level = raw * scale if not math.isnan(raw) else float("nan")
        return {channel + "_Raw": raw,
                channel + "_LI": level,
                channel + "_Status": status[:39]}


class DeviceConnection(SerialTcpConnection):
    """One command/reply through the gateway, past the echo."""

    query = "LEVEL"
    eol = EOL

    def read_level(self):
        """-> (level in cm, status).

        A missing reading is NaN, never 0.0 - an empty magnet dewar is a
        plausible and alarming thing to believe on no evidence.
        """
        command = self.query.strip().upper() or "LEVEL"
        self.discard_input()
        self.send(command.encode("ascii") + EOL)

        reply = self.read_until(EOL).decode("ascii", errors="replace").strip()
        if reply.upper() == command:
            # The DIP-switch echo.  The reading is the next line.
            reply = self.read_until(EOL).decode("ascii", errors="replace").strip()

        if not reply:
            return float("nan"), "EMPTY_REPLY"
        match = FLOAT_RE.search(reply.encode("ascii", errors="replace"))
        if not match:
            return float("nan"), "PARSE_ERR"
        try:
            value = float(match.group(0))
        except ValueError:
            return float("nan"), "PARSE_ERR"
        if not math.isfinite(value):
            return float("nan"), "NOT_FINITE"
        return value, "OK"


Device.connection_class = DeviceConnection
