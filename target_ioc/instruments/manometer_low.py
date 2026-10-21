"""
devices/instruments/manometer_low.py - the Manometer Low gauge, via a Raspberry Pi.

The instrument
--------------
A Kurt J. Lesker gauge, labelled KJL-SPARC-3.0, serial SK20K001.  It has a
serial port and no network interface, and it STREAMS: it emits a line on its
own, unprompted, and answers no command.  So there is nothing to ask it for -
the driver's whole job is to catch a fresh line and read the number out of it.

How it reaches the network
--------------------------
Through a Raspberry Pi at 192.168.1.24 running tools/pi_gateway.py, which
forwards the serial bytes to a TCP port untouched.  That makes it, from the
IOC's point of view, exactly the same arrangement as the four gauges behind the
DIEWU gateway on 192.168.1.251 - so this uses serial_tcp_base.py like they do,
with no new transport and no new dependency.

The parse, and where it came from
---------------------------------
NOT invented here.  It is the rule from the group's own working Pi reader
(manometer_low/manometer_low_headless.py), which has been run against this
gauge on the bench:

    read a line, strip it, drop the first `substring_start` characters
    (4 by default), and take the first floating-point token.  Torr.

The leading characters that get dropped are the gauge's own prefix.  Keeping
the same rule - and the same default - means the PV and the Pi's CSV agree
about what the gauge said, which is the only way a disagreement later can be
blamed on something real.  If the prefix ever changes, change it in
settings.yaml (`substring_start:`) rather than here.

Streaming, and why a stale line is not a reading
------------------------------------------------
Between two polls the gateway's socket buffer fills with lines nobody read.
Taking the first one would report a pressure from up to `delay` seconds ago,
and after a pause it would report one from minutes ago while looking perfectly
healthy - the failure mode this deployment has been bitten by twice.  So
do_reads() discards what is buffered and waits for the NEXT line, which is by
construction current.

Vapour-pressure temperature
---------------------------
With `vapor_pressure: True` the driver also publishes the ITS-90 temperature
of the helium this pressure belongs to - the same conversion the lab's
LabVIEW front panel does to produce "Vap.Pres.T", and the same published
equation.  The maths and both of its bands live in devices/its90_helium.py;
here it is one call on the number the gauge just sent.

This is not a second-best thermometer.  Below the lambda point the saturated
vapour pressure IS the temperature scale, and it needs no calibration, so
_VapT is the reading to trust when it disagrees with a resistor.  It is NaN
outside 0.86 Torr .. 1470 Torr (1.25 K .. 5 K), where the ITS-90 polynomials
are not defined, with the reason in _VapStatus.

PVs per channel (channel 'Mano' -> UNH:Mano_PI, ...)

    _PI        Torr   pressure, the number the gauge just sent
    _Status    text   OK, or why the last line did not yield one
    _Raw       text   the line itself, for when the parse looks wrong
    _VapT      K      ITS-90 vapour-pressure temperature  (vapor_pressure: True)
    _VapPhase  text   He-II superfluid / He-I normal / below or above scale
    _VapStatus text   OK, or why there is no temperature for this pressure

settings.yaml
    manometer_low:
      module: 'devices.instruments.manometer_low'
      autostart: True
      ip: '192.168.1.24'       # the Raspberry Pi
      port: 5030               # tools/pi_gateway.py --listen
      timeout: 3               # > the gauge's own line period
      delay: 2
      substring_start: 4       # the gauge's prefix, dropped before parsing
      vapor_pressure: True     # also publish _VapT / _VapPhase / _VapStatus
      pressure_unit: 'Torr'    # 'Torr' (default) or 'Pa' - what _PI is in
      vapor_bands: 'calibration/vapor_pressure_he4.csv'   # the equations
      channels: [Mano]
"""

import logging
import math
import os
import re

from softioc import builder

from genesis_common import its90_helium
from genesis_common.serial_tcp_base import SerialTcpConnection, SerialTcpDevice

log = logging.getLogger(__name__)

# Same expression as the group's Pi reader, so the two agree token for token.
FLOAT_RE = re.compile(rb"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
TERMINATOR = b"\n"


from genesis_common.cryo_readonly_health import CryoReadOnlyHealth


class Device(CryoReadOnlyHealth, SerialTcpDevice):
    """EPICS side: one pressure PV per channel, plus status and the raw line."""
    CRYO_NUMERIC_SUFFIXES = ('_PI', '_VapT')

    #: does this gauge's pressure get converted to an ITS-90 temperature?
    def _wants_vapor(self):
        return bool(self.settings.get("vapor_pressure", False))

    def _create_pvs(self):
        unit = str(self.settings.get("pressure_unit", "Torr"))
        for ch in self._skip_none_channels():
            self.pvs[ch + "_PI"] = builder.aIn(ch + "_PI", EGU=unit[:15], PREC=6,
                                               **self.analog)
            self.pvs[ch + "_Status"] = builder.stringIn(ch + "_Status")
            self.pvs[ch + "_Raw"] = builder.stringIn(ch + "_Raw")
            if self._wants_vapor():
                self.pvs[ch + "_VapT"] = builder.aIn(ch + "_VapT", EGU="K",
                                                     PREC=4, **self.analog)
                self.pvs[ch + "_VapPhase"] = builder.stringIn(ch + "_VapPhase")
                self.pvs[ch + "_VapStatus"] = builder.stringIn(ch + "_VapStatus")

    connection_class = None                     # set below

    def _create_connection(self):
        conn = super()._create_connection()
        conn.substring_start = int(self.settings.get("substring_start", 4))
        return conn

    def read_outs(self):
        """Load the vapour-pressure bands once, at start-up.

        `vapor_bands:` names a CSV of (Name, Pmin_Pa, Pmax_Pa, B, C, A0..A9).
        It is how a band ITS-90 does not define - anything below 1.25 K, which
        is where this fridge actually runs - gets added without touching code.
        """
        super().read_outs() if hasattr(super(), "read_outs") else None
        if not self._wants_vapor():
            return
        name = self.settings.get("vapor_bands", its90_helium.DEFAULT_BAND_FILE)
        path = name if os.path.isabs(name) else os.path.join(os.getcwd(), name)
        try:
            bands = its90_helium.load_bands(path)
        except OSError as e:
            log.warning("%s: no vapour-pressure band file (%s); using the two "
                        "built-in ITS-90 bands", self.device_name, e)
            return
        its90_helium.use_bands(bands)
        for band in bands:
            log.info("%s: vapour-pressure band %-12s %9.4g .. %9.4g Torr",
                     self.device_name, band["name"],
                     band["p_min"] / its90_helium.TORR_TO_PA,
                     band["p_max"] / its90_helium.TORR_TO_PA)

    def read_all(self, channel, index):
        pressure, status, raw = self.t.read_pressure()
        out = {channel + "_PI": pressure,
               channel + "_Status": status[:39],
               channel + "_Raw": raw[:39]}
        if not self._wants_vapor():
            return out
        # The ITS-90 equations are defined for pascals.  The gauge reports
        # Torr, so the conversion is explicit and in one place - a factor of
        # 133 here would not raise anything, it would just publish a
        # believable wrong temperature.
        if str(self.settings.get("pressure_unit", "Torr")).lower() in ("pa", "pascal"):
            kelvin, why = its90_helium.pascals_to_kelvin(pressure)
            torr = pressure / its90_helium.TORR_TO_PA if pressure == pressure else pressure
        else:
            kelvin, why = its90_helium.torr_to_kelvin(pressure)
            torr = pressure
        out[channel + "_VapT"] = kelvin
        out[channel + "_VapPhase"] = its90_helium.phase_of(torr)[:39]
        # A bad LINE already says why on _Status; repeating it on _VapStatus
        # would send somebody looking for a conversion problem.
        out[channel + "_VapStatus"] = (status if status != "OK"
                                       else (why or "OK"))[:39]
        return out


class DeviceConnection(SerialTcpConnection):
    """One line of the gauge's stream."""

    substring_start = 4

    def read_pressure(self):
        """-> (Torr, status, raw line).

        A line that arrives but does not parse is a STATUS, not an exception:
        the link is fine and the IOC should keep polling, so the PV goes NaN
        with a reason rather than the whole IOC alarming and reconnecting.  A
        line that never arrives IS an exception - that is the link failing, and
        OSError is what the base class catches to alarm and reconnect.

        The value is NaN, never 0.0, when there is no reading.  A vacuum gauge
        reporting 0 Torr looks like a perfect vacuum, which is the most
        dangerous thing a broken gauge can say.
        """
        # Throw away whatever accumulated since the last poll, then wait for
        # the next line: with a streaming instrument, the buffered one is old.
        self.discard_input()
        raw = self.read_until(TERMINATOR)
        text = raw.decode("ascii", errors="replace").strip()
        if not text:
            raise OSError(f"{self.host}: no line from the gauge within the timeout")

        subset = raw[max(0, self.substring_start):]
        match = FLOAT_RE.search(subset)
        if not match:
            return float("nan"), "PARSE_ERR", text

        try:
            value = float(match.group(0))
        except ValueError:
            return float("nan"), "PARSE_ERR", text
        if not math.isfinite(value):
            return float("nan"), "NOT_FINITE", text
        return value, "OK", text


Device.connection_class = DeviceConnection
