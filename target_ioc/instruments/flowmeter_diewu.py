"""
devices/instruments/flowmeter_diewu.py - Teledyne Hastings flowmeters on the
DIEWU serial-to-Ethernet gateway.

Two meters, one box
-------------------
    separate flowmeter    192.168.1.251:5013
    left backing pump     192.168.1.251:5012

Same gateway at 192.168.1.251 that already carries the MKS 902B, the
Pfeiffer TPG and the Lake Shore 455 - one serial port per TCP port, bytes
passed through untouched.  Each port is its own socket, so these run as two
independent IOCs even though one box serves them, exactly as the gauges do.

Why this is a different driver from flowmeter_pi.py
---------------------------------------------------
The same instrument, reached two different ways, and the difference is worth
a separate file rather than a flag:

    flowmeter_pi     right pump.  A Raspberry Pi owns the serial conversation
                     (flowmeter_pi_headless.py) and re-serves the latest value
                     on TCP 5010.  The IOC is a client of THAT server, so it
                     can get a reply meaning "the Pi is fine but its serial
                     side is not" - which is a status, not a link failure.

    flowmeter_diewu  these two.  Nothing sits in between: the gateway is a
                     wire, and this driver holds the meter's own conversation.
                     There is no such thing as a server-level error reply.
                     Silence here means the link is down, full stop.

Collapsing the two into one module would mean a driver that sometimes has a
middleman and sometimes does not, with a branch in the parse to prove it.
Two short files each say one true thing.

The conversation
----------------
    send     "F" + CR
    receive  a number, then ">"        e.g. b"19.004000>"

Taken from flowmeter_diewu_test.py in Genesis/flowmeter, which is the script
that was proven against both meters.  The terminator is ">", NOT a newline -
read_until(b"\\n") would block until the timeout on every single poll.

discard_input() first, every time.  A serial gateway keeps bytes buffered
across TCP connections, so the first reply read after a reconnect can belong
to the PREVIOUS conversation - a reading from before the gap, published as
though it were current.  Every ASCII driver on this transport clears the pipe
before a query for that reason.

The correction factor
---------------------
The meter reports a raw number; 28/19 converts it to SLM.  That constant
lives in settings.yaml, not here and not on any other machine, for the same
reason the Lake Shore 224 curves do: a calibration constant belongs with the
configuration that is reviewed, version controlled and tested.

Both numbers are served - _Raw as the meter reported it, _SLM after the
factor - so a disagreement later can be traced to the constant rather than
argued about.  The trends plot _Raw, which is the number the instrument
actually produced.

PVs per channel (channel 'LPump' -> UNH:LPump_SLM, ...)

    _SLM     slm    corrected flow: raw * correction
    _Raw     slm    what the meter actually reported
    _Status  text   OK, or why the last reply did not yield a number
    _Conn    bool   built by ScpiDevice: is this IOC talking to the gateway

settings.yaml
    flowmeter_left:
      module: 'devices.instruments.flowmeter_diewu'
      autostart: True
      ip: '192.168.1.251'      # the DIEWU gateway, not the meter
      port: 5012               # which serial port of the gateway
      timeout: 3
      delay: 2
      request: 'F'
      correction: 1.4736842105 # 28/19
      channels: [LPump]
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
    """EPICS side: the raw reading, the corrected one, and why if there is none."""
    CRYO_NUMERIC_SUFFIXES = ('_SLM', '_Raw')

    def _create_pvs(self):
        # _Conn is not built here: ScpiDevice.__init__ creates one per channel
        # for every driver on this transport, and sets it from whether the IOC
        # is actually talking to the gateway.
        for ch in self._skip_none_channels():
            self.pvs[ch + "_SLM"] = builder.aIn(ch + "_SLM", EGU="slm", PREC=4,
                                                **self.analog)
            self.pvs[ch + "_Raw"] = builder.aIn(ch + "_Raw", EGU="slm", PREC=4,
                                                **self.analog)
            self.pvs[ch + "_Status"] = builder.stringIn(ch + "_Status")

    connection_class = None                      # set below

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
    """One command/reply with the meter, through the gateway."""

    request = "F"

    def read_flow(self):
        """-> (raw flow, status).

        A missing reading is NaN, never 0.0.  A flowmeter reporting 0 slm
        looks like a closed valve, which is a plausible and wrong thing to
        believe - and on a backing pump line it is the reading somebody would
        act on.

        Silence IS an exception here, unlike in flowmeter_pi.py: with no
        server in between, no reply means the link to the gateway has failed,
        and OSError is what BaseDevice catches to alarm the PVs and reconnect.
        read_until() already raises OSError on a timeout.
        """
        self.discard_input()
        self.send(self.request + "\r")
        raw = self.read_until(TERMINATOR)
        text = raw.split(TERMINATOR, 1)[0].decode("ascii", errors="replace").strip()
        if not text:
            return float("nan"), "EMPTY_REPLY"

        match = FLOAT_RE.search(text.encode("ascii", errors="replace"))
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
