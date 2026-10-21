"""
devices/instruments/ami1700.py - the FRIDGE liquid-helium level probe.

The instrument
--------------
An American Magnetics (AMI) Model 1700 helium level instrument at
192.168.1.27, with its own Ethernet port.  No gateway in front of it: the
instrument speaks SCPI straight over TCP, on AMI's factory command port 7180
rather than the 5025 most LXI gear uses.

That is the only reason this is not simply another ScpiDevice entry - the
port number - and settings.yaml carries it, so nothing here hard-codes it.

The conversation
----------------
    send     'MEASure:HE:LEVel?' + CR
    receive  a number, terminated by LF     e.g. b'18.273\\n'

    send     '*IDN?' + CR
    receive  'AMI,MODEL 1700,...' + LF

Taken verbatim from the user's own Pressure/fridge_lhe_level_gui.py, which
is the program proven against this instrument.  Note the asymmetry: the
command ends in CR, the reply ends in LF.  Reading until CR would hang until
the timeout on every poll, and reading a fixed number of bytes would cut a
four-digit level short.

Units
-----
The 1700 reports inches directly, so scale is 1.0 and offset 0.0 by default.
Both live in settings.yaml rather than here: if the probe is ever re-zeroed
or swapped for one calibrated in centimetres, that is a configuration change
reviewed in a diff, not a code change.

Both numbers are served - _Raw as the instrument reported it and _LI after
scale and offset - so a disagreement later can be traced to the constants
rather than argued about.

PVs per channel (channel 'Fridge' -> UNH:Fridge_LI, ...)

    _LI      inch   level: raw * scale + offset
    _Raw     inch   what the instrument actually reported
    _Status  text   OK, or why the last reply did not yield a number
    _IDN     text   identity, read once at connect (channel 1 only)
    _Conn    bool   built by ScpiDevice: is this IOC talking to the probe

settings.yaml
    lhe_fridge:
      module: 'devices.instruments.ami1700'
      autostart: True
      ip: '192.168.1.27'       # the instrument itself, no gateway
      port: 7180               # AMI's factory SCPI command port
      timeout: 3
      delay: 5                 # a helium level does not move in two seconds
      query: 'MEASure:HE:LEVel?'
      scale: 1.0
      offset: 0.0
      channels: [Fridge]
"""

import logging
import math
import re

from softioc import builder

from genesis_common.serial_tcp_base import SerialTcpConnection, SerialTcpDevice

log = logging.getLogger(__name__)

FLOAT_RE = re.compile(rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")
REPLY_EOL = b"\n"            # the reply ends in LF, the command in CR
COMMAND_EOL = "\r"


from genesis_common.cryo_readonly_health import CryoReadOnlyHealth


class Device(CryoReadOnlyHealth, SerialTcpDevice):
    """EPICS side: the level, the raw reading, and why if there is none."""
    CRYO_NUMERIC_SUFFIXES = ('_LI', '_Raw')

    def _create_pvs(self):
        # _Conn comes from ScpiDevice.__init__, one per channel.
        for index, ch in enumerate(self._skip_none_channels()):
            self.pvs[ch + "_LI"] = builder.aIn(ch + "_LI", EGU="inch", PREC=3,
                                               **self.analog)
            self.pvs[ch + "_Raw"] = builder.aIn(ch + "_Raw", EGU="inch", PREC=3,
                                                **self.analog)
            self.pvs[ch + "_Status"] = builder.stringIn(ch + "_Status")
            if index == 0:
                self.pvs[ch + "_IDN"] = builder.stringIn(ch + "_IDN")

    connection_class = None                     # set below

    def _create_connection(self):
        conn = super()._create_connection()
        conn.query = str(self.settings.get("query", "MEASure:HE:LEVel?"))
        conn.idn_query = str(self.settings.get("idn_query", "*IDN?"))
        return conn

    def _post_connect(self):
        super()._post_connect()
        # Identity once, at connect, not every poll: it never changes, and a
        # level probe polled every few seconds does not need a second
        # round-trip to prove it is still the same instrument.
        for index, ch in enumerate(self._skip_none_channels()):
            if index == 0 and (ch + "_IDN") in self.pvs:
                try:
                    self.pvs[ch + "_IDN"].set(self.t.read_idn()[:39])
                except OSError as exc:
                    log.warning("%s: no identity: %s", ch, exc)

    def read_all(self, channel, index):
        raw, status = self.t.read_level()
        scale = float(self.settings.get("scale", 1.0))
        offset = float(self.settings.get("offset", 0.0))
        level = raw * scale + offset if not math.isnan(raw) else float("nan")
        return {channel + "_Raw": raw,
                channel + "_LI": level,
                channel + "_Status": status[:39]}


class DeviceConnection(SerialTcpConnection):
    """One SCPI query, CR out and LF back."""

    query = "MEASure:HE:LEVel?"
    idn_query = "*IDN?"

    def _ask(self, command):
        self.discard_input()
        self.send(command.rstrip("\r\n") + COMMAND_EOL)
        return self.read_until(REPLY_EOL).decode("ascii", errors="replace").strip()

    def read_idn(self):
        return self._ask(self.idn_query)

    def read_level(self):
        """-> (level, status).

        A missing reading is NaN, never 0.0.  A helium level of 0 inches
        means an empty cryostat, which is a plausible and alarming thing to
        believe on no evidence.

        Silence is an OSError, which is what BaseDevice catches to alarm the
        PVs and reconnect: nothing sits between this IOC and the instrument
        that could be alive while the instrument is not.
        """
        reply = self._ask(self.query)
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
