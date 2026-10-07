"""
devices/instruments/mks902b.py - MKS 902B absolute piezo vacuum transducer.

Port of Genesis/Pressure/manometer.py.  The 902B has RS-232 only and is wired
to a serial-to-Ethernet gateway, so the transport is devices/serial_tcp_base.py
(see that file for why this is not scpi_base).

Protocol (MKS 900 series ASCII, 9600 8N1):

    request  @<addr><query>?;FF        e.g.  @253PR4?;FF
    reply    @<addr>ACK<value>;FF      e.g.  @253ACK7.600E+2;FF
    refusal  @<addr>NAK<code>;FF

PR4 is used because it answers in scientific notation, which parses cleanly
over the whole 0.1 - 1000 Torr range (PR1..PR3 are fractional).  The value is
in the transducer's own unit; factory default is Torr.

PVs per channel (channel 'Cell' -> UNH:Cell_PI, ...):

    _PI      Torr   pressure
    _Status  text   OK, or the NAK code / parse problem the gauge gave back
    _IDN     text   device type + serial, read once at start-up

settings.yaml
    mks902b:
      module: 'target_ioc.instruments.mks902b'
      ip: '192.168.1.251'      # the gateway
      port: 5004               # gateway serial port the 902B is on
      timeout: 2
      delay: 2
      address: 253             # 902B device address (factory default)
      query: 'PR4'
      channels: [Cell]
"""

import logging
import re

from softioc import builder

from genesis_common.serial_tcp_base import SerialTcpConnection, SerialTcpDevice

log = logging.getLogger(__name__)

TERMINATOR = b";FF"
ACK_RE = re.compile(rb"ACK\s*([-+0-9.eE]+)\s*;FF")
NAK_RE = re.compile(rb"NAK\s*([0-9]+)\s*;FF")


class Device(SerialTcpDevice):
    """EPICS side: one pressure PV per channel, plus status and identity."""

    def _create_pvs(self):
        for ch in self._skip_none_channels():
            self.pvs[ch + "_PI"] = builder.aIn(ch + "_PI", EGU="Torr", PREC=4, **self.analog)
            self.pvs[ch + "_Status"] = builder.stringIn(ch + "_Status")
            self.pvs[ch + "_IDN"] = builder.stringIn(ch + "_IDN")

    connection_class = None                     # set below

    def _create_connection(self):
        conn = super()._create_connection()
        conn.address = int(self.settings.get("address", 253))
        conn.query_name = str(self.settings.get("query", "PR4")).upper().strip("?")
        return conn

    def read_outs(self):
        """Identity once at start-up; a gauge that cannot say what it is usually
        means the gateway is passing bytes for a different serial port."""
        if self.t is None:
            return
        for ch in self._skip_none_channels():
            try:
                self.pvs[ch + "_IDN"].set(self.t.device_type()[:39])
                log.info("%s: %s", ch, self.pvs[ch + "_IDN"].get())
            except OSError as e:
                log.error("%s: identity read failed: %s", ch, e)

    def read_all(self, channel, index):
        pressure, status = self.t.read_pressure()
        return {channel + "_PI": pressure,
                channel + "_Status": status[:39]}


class DeviceConnection(SerialTcpConnection):
    """The 902B's ASCII vocabulary."""

    address = 253
    query_name = "PR4"

    def _exchange(self, query):
        """One @<addr><query>?;FF round-trip; returns the raw reply bytes."""
        self.discard_input()
        self.send(f"@{self.address}{query}?;FF")
        return self.read_until(TERMINATOR)

    def read_pressure(self):
        """-> (Torr, status text).  A refusal is a value the gauge REFUSED to
        give, not a communication failure, so it is reported as a status rather
        than raised: the PV alarms through the status, the IOC keeps polling."""
        raw = self._exchange(self.query_name)
        nak = NAK_RE.search(raw)
        if nak:
            return float("nan"), f"NAK {nak.group(1).decode()}"
        m = ACK_RE.search(raw)
        if not m:
            raise OSError(f"{self.host}: unparsed 902B reply {raw!r}")
        try:
            return float(m.group(1)), "OK"
        except ValueError as e:
            raise OSError(f"{self.host}: bad 902B value {m.group(1)!r}") from e

    def device_type(self):
        """DT? = device type, SN? = serial number; joined for the _IDN PV."""
        parts = []
        for query in ("DT", "SN"):
            try:
                raw = self._exchange(query)
                m = re.search(rb"ACK\s*(.+?)\s*;FF", raw)
                if m:
                    parts.append(m.group(1).decode("ascii", errors="replace"))
            except OSError:
                pass
        return " ".join(parts) or "MKS 902B"


Device.connection_class = DeviceConnection
