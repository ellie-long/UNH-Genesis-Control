r"""
devices/instruments/tpg262.py - Pfeiffer TPG 261/262 dual vacuum gauge controller.

Port of Genesis/Pressure/tpg262_diewu_tcp_gui.py.  RS-232 only, so it hangs on
a serial-to-Ethernet gateway (see devices/serial_tcp_base.py).

Protocol (Pfeiffer "mnemonics", 9600 or 19200 8N1) - a two-step handshake that
no other instrument in this lab uses:

    ->  PRX<CR><LF>          ask for both channels
    <-  <ACK=0x06><CR><LF>   "understood"
    ->  <ENQ=0x05>           "now send it"
    <-  0,7.500E+2,0,3.0E-3  status1,pressure1,status2,pressure2

The status digit is the one that matters operationally: 0 is a real reading, and
1-6 mean underrange, overrange, sensor error, sensor off, no sensor or an
identification error.  A number with a non-zero status is NOT a pressure, so
this driver publishes NaN and puts the reason in the _Status PV instead of
letting 0.0 look like a good vacuum.

PVs per channel (channels [Pump, OVC] -> UNH:Pump_PI, UNH:OVC_PI, ...):

    _PI      pressure in the controller's own display unit (see _Unit)
    _Status  OK / Underrange / Sensor off / ...

and once per IOC:  _Unit (mbar, Torr or Pa) and _IDN.

Both channels come from ONE PRX exchange: read_all() asks the instrument when
it is called for the first channel and reuses that answer for the second, so a
polling cycle is one round-trip no matter how many channels are configured.

settings.yaml
    tpg262:
      module: 'target_ioc.instruments.tpg262'
      ip: '192.168.1.251'      # the gateway
      port: 5011               # gateway serial port the TPG 262 is on
      timeout: 2
      delay: 2
      channels: [Pump, OVC]    # gauge 1, gauge 2; use None for an empty slot
"""

import logging

from softioc import builder

from genesis_common.serial_tcp_base import SerialTcpConnection, SerialTcpDevice

log = logging.getLogger(__name__)

ACK = b"\x06"
ENQ = b"\x05"
NAK = b"\x15"

STATUS = {"0": "OK", "1": "Underrange", "2": "Overrange", "3": "Sensor error",
          "4": "Sensor off", "5": "No sensor", "6": "Identification error"}
UNITS = {"0": "mbar", "1": "Torr", "2": "Pa"}


class Device(SerialTcpDevice):

    def _create_pvs(self):
        for ch in self._skip_none_channels():
            self.pvs[ch + "_PI"] = builder.aIn(ch + "_PI", EGU="mbar", PREC=4, **self.analog)
            self.pvs[ch + "_Status"] = builder.stringIn(ch + "_Status")
        first = next(iter(self._skip_none_channels()), "TPG")
        self.pvs[first + "_Unit"] = builder.stringIn(first + "_Unit")
        self.pvs[first + "_IDN"] = builder.stringIn(first + "_IDN")
        self._info_channel = first

    connection_class = None                     # set below

    def read_outs(self):
        if self.t is None:
            return
        ch = self._info_channel
        try:
            unit = self.t.unit()
            self.pvs[ch + "_Unit"].set(unit[:39])
            self.pvs[ch + "_IDN"].set(self.t.gauge_ids()[:39])
            # keep the EGU field honest: the controller, not settings.yaml,
            # decides what its numbers mean
            for name in (c + "_PI" for c in self._skip_none_channels()):
                try:
                    self.pvs[name].EGU = unit
                except Exception as e:          # noqa: BLE001 - cosmetic only
                    log.debug("could not set EGU on %s: %s", name, e)
            log.info("TPG 262: %s, unit %s", self.pvs[ch + "_IDN"].get(), unit)
        except OSError as e:
            log.error("TPG 262: start-up read failed: %s", e)

    def read_all(self, channel, index):
        if index == 1:
            self._latest = self.t.read_pressures()   # one PRX for all channels
        status, pressure = self._latest[index - 1]
        return {channel + "_PI": pressure,
                channel + "_Status": status[:39]}


class DeviceConnection(SerialTcpConnection):
    """The Pfeiffer ACK/ENQ handshake."""

    def query(self, mnemonic):
        self.discard_input()
        self.send(mnemonic.encode("ascii") + b"\r\n")
        reply = self.read_until(b"\n")
        if reply.startswith(NAK):
            raise OSError(f"{self.host}: TPG refused '{mnemonic}' (NAK)")
        if not reply.startswith(ACK):
            raise OSError(f"{self.host}: expected ACK from TPG, got {reply!r} "
                          f"(wrong gateway port, or baud rate mismatch?)")
        self.send(ENQ)
        return self.read_until(b"\n").decode("ascii", errors="replace").strip()

    def read_pressures(self):
        """-> [(status text, value), (status text, value)] for gauge 1 and 2.

        A non-zero status means the number is meaningless, so it is replaced by
        NaN: an operator screen must not show 'sensor off' as 0 mbar."""
        fields = [f.strip() for f in self.query("PRX").split(",")]
        if len(fields) != 4:
            raise OSError(f"{self.host}: expected 4 PRX fields, got {fields!r}")
        out = []
        for code, value in ((fields[0], fields[1]), (fields[2], fields[3])):
            text = STATUS.get(code, f"Status {code}")
            try:
                number = float(value)
            except ValueError as e:
                raise OSError(f"{self.host}: bad PRX value {value!r}") from e
            out.append((text, number if code == "0" else float("nan")))
        return out

    def unit(self):
        return UNITS.get(self.query("UNI").strip(), "mbar")

    def gauge_ids(self):
        """TID = the sensor types fitted, the closest thing this box has to *IDN?."""
        return "TPG 26x " + self.query("TID")


Device.connection_class = DeviceConnection
