"""
devices/instruments/flowmeter_pi.py - the right-pump flowmeter, via a Raspberry Pi.

The instrument
--------------
A Teledyne Hastings flowmeter on the right pump line.  It is a USB CDC device
(/dev/ttyACM*) on the Pi at 192.168.1.24, which is what distinguishes it from
the Manometer Low gauge's FTDI adapter (/dev/ttyUSB*) on the same Pi - two
different device classes, so they can never be confused for one another.

Unlike the manometer, this one does NOT stream: it answers a command ("F" by
default) with a number terminated by ">".

How it reaches the network
--------------------------
Differently from the manometer, and deliberately so.

The manometer's gauge is dumb and streams, so tools/pi_gateway.py forwards its
bytes untouched and the IOC parses them.  This flowmeter's Pi-side program
(flowmeter_pi_headless.py) already owns the serial conversation - it polls the
meter on its own schedule and serves the latest value over TCP 5010, answering
any request with

    <raw>>\\r\\n              e.g. "19.004000>\\r\\n"
    ERROR <reason>>\\r\\n     when it has no reading yet

That is a sound design for a command/response instrument: the Pi keeps one
conversation going rather than letting every client re-ask the meter.  So this
driver is a client of that server, not of a raw serial stream.

The correction factor lives HERE, not on the Pi
-----------------------------------------------
The Pi serves the RAW number.  Its console applies 28/19 to print SLM, but the
TCP reply does not.

Putting the correction in settings.yaml rather than leaving it on the Pi is the
same decision this deployment makes for the Lake Shore 224 curves: a
calibration constant belongs with the configuration that is reviewed, version
controlled and tested, not in a script on a second machine that nobody diffs.
It also means both numbers are visible - _Raw as the meter reported it and
_SLM after correction - so a disagreement later can be traced to the constant
rather than argued about.

PVs per channel (channel 'RPump' -> UNH:RPump_SLM, ...)

    _SLM     slm    corrected flow: raw * correction
    _Raw     slm    what the meter actually reported
    _Status  text   OK, or why the last reply did not yield a number

settings.yaml
    flowmeter_pi:
      module: 'devices.instruments.flowmeter_pi'
      autostart: True
      ip: '192.168.1.24'       # the Raspberry Pi
      port: 5010               # flowmeter_pi_headless.py --tcp-port
      timeout: 3
      delay: 2
      request: 'F'             # anything; the server replies with its latest
      correction: 1.4736842105 # 28/19, from the Pi script's --correction
      channels: [RPump]
"""

import logging
import math
import re

from softioc import builder

from genesis_common.serial_tcp_base import SerialTcpConnection, SerialTcpDevice

log = logging.getLogger(__name__)

FLOAT_RE = re.compile(rb"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
TERMINATOR = b">"


from genesis_common.cryo_readonly_health import CryoReadOnlyHealth


class Device(CryoReadOnlyHealth, SerialTcpDevice):
    """EPICS side: corrected flow, the raw reading, and why if there is none."""
    CRYO_NUMERIC_SUFFIXES = ('_SLM', '_Raw')

    def _create_pvs(self):
        for ch in self._skip_none_channels():
            self.pvs[ch + "_SLM"] = builder.aIn(ch + "_SLM", EGU="slm", PREC=4,
                                                **self.analog)
            self.pvs[ch + "_Raw"] = builder.aIn(ch + "_Raw", EGU="slm", PREC=4,
                                                **self.analog)
            self.pvs[ch + "_Status"] = builder.stringIn(ch + "_Status")

    connection_class = None                     # set below

    def _create_connection(self):
        conn = super()._create_connection()
        conn.request = str(self.settings.get("request", "F"))
        return conn

    def read_all(self, channel, index):
        raw, status = self.t.read_flow()
        correction = float(self.settings.get("correction", 1.0))
        corrected = raw * correction if not math.isnan(raw) else float("nan")
        return {channel + "_Raw": raw,
                channel + "_SLM": corrected,
                channel + "_Status": status[:39]}


class DeviceConnection(SerialTcpConnection):
    """One request/reply with the Pi's flowmeter server."""

    request = "F"

    def read_flow(self):
        """-> (raw slm, status).

        The server's own error reply ("ERROR ...>") means the Pi is alive but
        its serial side is not - so it is a STATUS, not an exception: the link
        to the Pi is fine and the IOC should keep polling.  Silence from the Pi
        IS an exception, because that is the link failing, and OSError is what
        the base class catches to alarm the PVs and reconnect.

        A missing reading is NaN, never 0.0.  A flowmeter reporting 0 slm looks
        like a closed valve, which is a plausible and wrong thing to believe.
        """
        self.discard_input()
        self.send(self.request + "\r")
        raw = self.read_until(TERMINATOR)
        text = raw.decode("ascii", errors="replace").strip()
        if not text:
            raise OSError(f"{self.host}: no reply from the flowmeter server")

        if b"ERROR" in raw:
            # Pass the Pi's own words through rather than inventing a summary.
            reason = text.replace("ERROR", "").replace(">", "").strip()
            return float("nan"), (reason or "server has no reading")[:39]

        match = FLOAT_RE.search(raw)
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
