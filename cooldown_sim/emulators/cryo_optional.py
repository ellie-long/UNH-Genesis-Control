"""Emulators for the optional cryo instruments (Utsav's cryo GUI completeness work):
Teledyne Hastings flowmeters (via the keysight Pi's server, or straight through
the TXI040 gateway), the AMI 1700 / AMI 135 helium level meters, and the
Manometer-Lo stream (KJLC XCG-HT-FB-1 on a KJL-SPARC, forwarded by the Pi).

Each speaks exactly what the unchanged driver in ``target_ioc/instruments``
expects; the wire formats come from those drivers' docstrings and from
``tests/test_cryo_device_parsers.py``.  As in :mod:`.cryo`, ``source(channel)``
returns the value in RECORDED units (slm, inch, Torr) or NaN when the record
has nothing usable; the emulator converts to what the instrument puts on the
wire (raw flow before the profile's 28/19 correction, centimetres for the
AMI 135) so the driver's own constants convert it back.

A NaN reading is answered the way that link reports "no number":

* Pi flowmeter server  -> ``ERROR no reading>``   (driver: NaN, status "no reading")
* gateway flowmeter    -> ``>``                    (driver: NaN, EMPTY_REPLY)
* AMI 1700 / AMI 135   -> an empty line            (driver: NaN, EMPTY_REPLY)
* Manometer-Lo stream  -> a line with no number    (driver: NaN, PARSE_ERR)

so the IOC stays connected and flags the reading, never inventing a 0.
"""

from __future__ import annotations

import math

from .base import Emulator
from .cryo import Source


def _split_cr_lf(buf: bytes) -> tuple[list[bytes], bytes]:
    """Requests terminated by CR, LF or CR LF (empty ones dropped)."""
    out = []
    while True:
        idx = [i for i in (buf.find(b"\r"), buf.find(b"\n")) if i >= 0]
        if not idx:
            return out, buf
        i = min(idx)
        line, buf = buf[:i], buf[i + 1:]
        if line.strip():
            out.append(line + b"\r")


# ------------------------------------------------------- Hastings flowmeter
class HastingsFlowmeter(Emulator):
    """Teledyne Hastings flowmeter: ``F`` + CR -> ``<raw>>``.

    ``via_pi=True`` is the right-pump meter behind the keysight Pi's
    ``flowmeter_pi_headless.py`` server (TCP 5010), which answers any request
    with ``<raw>>\\r\\n`` or ``ERROR <reason>>\\r\\n``; ``via_pi=False`` is a
    meter straight through the TXI040 gateway (5012 left Roots, 5013
    separator), which answers ``<raw>>`` and has no error reply of its own.

    The raw number is the record's SLM divided by the profile's
    ``correction`` (28/19): the driver multiplies it back for ``_SLM``.
    """

    name = "flowmeter"

    def __init__(self, channel: str, source: Source, correction: float = 1.0,
                 via_pi: bool = False):
        super().__init__()
        self.channel = channel
        self.source = source
        self.correction = float(correction) or 1.0
        self.via_pi = bool(via_pi)
        self.eol = b">\r\n" if via_pi else b">"

    def frame(self, buf: bytes):
        return _split_cr_lf(buf)

    def respond(self, request: bytes) -> bytes | None:
        if not self.via_pi and request.strip().upper() != b"F":
            return None                      # the meter ignores what it does not know
        slm = self.source(self.channel)
        if not math.isfinite(slm):
            return b"ERROR no reading" + self.eol if self.via_pi else b">"
        return f"{slm / self.correction:.6f}".encode("ascii") + self.eol

    def garbage(self, request: bytes) -> bytes:
        return b"#?~" + self.eol

    def nak(self, request: bytes) -> bytes:
        # the Pi's own refusal: alive, but its serial side is not
        return b"ERROR serial port not open" + self.eol if self.via_pi else self.garbage(request)


# ----------------------------------------------------------------- AMI 1700
class AMI1700(Emulator):
    """AMI Model 1700 level instrument, SCPI on TCP 7180: commands end in CR,
    replies in CR LF (the driver reads to LF and strips).

    ``MEASure:HE:LEVel?`` -> inches (``(level - offset) / scale`` with the
    profile's scale/offset, i.e. inches as-is for the live 1.0 / 0.0).
    """

    name = "ami1700"
    IDN = "AMI,MODEL 1700,SIM0001,1.0"
    #: SCPI long/short forms of the level query the emulator accepts
    LEVEL_QUERIES = {"MEASURE:HE:LEVEL?", "MEAS:HE:LEV?", "MEASURE:HE:LEV?", "MEAS:HE:LEVEL?"}

    def __init__(self, channel: str, source: Source, scale: float = 1.0, offset: float = 0.0):
        super().__init__()
        self.channel = channel
        self.source = source
        self.scale = float(scale) or 1.0
        self.offset = float(offset)

    def frame(self, buf: bytes):
        return _split_cr_lf(buf)

    def respond(self, request: bytes) -> bytes | None:
        cmd = request.decode("ascii", "replace").strip().upper()
        if cmd == "*IDN?":
            return self.IDN.encode("ascii") + b"\r\n"
        if cmd in self.LEVEL_QUERIES:
            inch = self.source(self.channel)
            if not math.isfinite(inch):
                return b"\r\n"
            return f"{(inch - self.offset) / self.scale:.3f}\r\n".encode("ascii")
        return None                          # SCPI: an unknown command gets no reply

    def garbage(self, request: bytes) -> bytes:
        return b"ERR?#~\x7f\r\n"


# ------------------------------------------------------------------ AMI 135
class AMI135(Emulator):
    """AMI Model 135 level monitor on the TXI017 gateway (TCP 23), CR LF.

    ``LEVEL`` -> the command echoed (the lab unit's DIP switches enable echo)
    then the level in centimetres to 0.1 cm, the resolution the 2020 record
    shows (steps of 0.0393 in = 0.1 cm x 0.393).
    """

    name = "ami135"

    def __init__(self, channel: str, source: Source, scale: float = 0.393, echo: bool = True):
        super().__init__()
        self.channel = channel
        self.source = source
        self.scale = float(scale) or 0.393
        self.echo = bool(echo)

    def frame(self, buf: bytes):
        return _split_cr_lf(buf)

    def respond(self, request: bytes) -> bytes | None:
        cmd = request.decode("ascii", "replace").strip().upper()
        head = (cmd.encode("ascii") + b"\r\n") if self.echo else b""
        if cmd != "LEVEL":
            return head or None
        inch = self.source(self.channel)
        if not math.isfinite(inch):
            return head + b"\r\n"
        return head + f"{inch / self.scale:.1f}\r\n".encode("ascii")

    def garbage(self, request: bytes) -> bytes:
        return b"LEVEL\r\n?#~\r\n" if self.echo else b"?#~\r\n"


# ------------------------------------------------------------- Manometer-Lo
class KJLSparcStream(Emulator):
    """Manometer-Lo as the IOC sees it: the KJL-SPARC's unprompted line stream,
    forwarded untouched by ``tools/pi_gateway.py`` on the keysight Pi (5030).

    The driver drops the first ``substring_start`` (4) characters of each line
    and takes the first number, in Torr.  The real line layout is not
    documented in the repository, so the emulator uses ``prefix`` (four
    characters, no digits) + ``%.4E`` + CR LF.  It never listens.
    """

    name = "manometer_low"
    stream_interval_s = 0.25

    def __init__(self, channel: str, source: Source, prefix: str = "Pr: "):
        super().__init__()
        self.channel = channel
        self.source = source
        self.prefix = prefix

    def frame(self, buf: bytes):
        return [], b""

    def respond(self, request: bytes) -> bytes | None:
        return None

    def stream(self) -> bytes | None:
        torr = self.source(self.channel)
        body = f"{torr:.4E}" if math.isfinite(torr) else "----"
        return f"{self.prefix}{body}\r\n".encode("ascii")

    def garbage(self, request: bytes) -> bytes:
        return f"{self.prefix}#?~\r\n".encode("ascii")
