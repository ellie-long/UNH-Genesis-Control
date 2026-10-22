"""Cryo instrument emulators: LS224, MKS 902B, TPG 262, IONIVAC, LS455.

Each takes a ``source`` callable returning the value the instrument should
read *now*, in recorded units (K, Torr, gauss) -- or NaN when the record has
nothing usable, which each emulator reports the way that instrument would.
"""

from __future__ import annotations

import math
import re
import threading
from typing import Callable

from .. import conversions
from .base import Emulator

Source = Callable[[str], float]


def _lazy_driver(name: str):
    """Import a driver module for its protocol constants (softioc not needed
    for the constants, but the module imports it -- tolerate a stub)."""
    import importlib
    return importlib.import_module(f"target_ioc.instruments.{name}")


# ----------------------------------------------------------------- LS224
class LS224(Emulator):
    """Lake Shore 224 on TCP 7777, CR/LF.

    ``slots`` maps input (A, C1, ...) -> channel name (F1, ...).  ``inverse``
    maps input -> InverseCalibration when the IOC profile has a calibration
    file (the driver then reads SRDG? ohms); otherwise KRDG? is served.

    ``range_bits`` maps input -> extra RDGST? bits served with a valid
    reading: 16 (T.UNDER) / 32 (T.OVER) are what a real 224 reports when the
    reading is outside the span of the curve assigned *inside* the
    instrument -- the live target-stick Cernoxes answer 32 at room
    temperature -- which Genesis's driver accepts ("OK; 224 TEMP RANGE")
    because it converts SRDG? ohms with its own curve; any other bit
    (64 S.UNDER, 128 S.OVER) marks the reading invalid.  Empty by default.
    """

    name = "ls224"
    IDN = "LSCI,MODEL224,SIM0001,1.1"

    def __init__(self, slots: dict[str, str], source: Source, inverse: dict | None = None):
        super().__init__()
        self.slots = dict(slots)
        self.source = source
        self.inverse = dict(inverse or {})
        self.clamped: dict[str, int] = {}
        self.range_bits: dict[str, int] = {}

    def _kelvin(self, slot: str) -> float:
        ch = self.slots.get(slot)
        return self.source(ch) if ch else float("nan")

    def respond(self, request: bytes) -> bytes | None:
        cmd = request.decode("ascii", "replace").strip()
        word, _, arg = cmd.partition(" ")
        word, arg = word.upper(), arg.strip().upper()
        if word == "*IDN?":
            text = self.IDN
        elif word == "KRDG?":
            k = self._kelvin(arg)
            text = f"{(k if math.isfinite(k) else 0.0):+.6E}"
        elif word == "SRDG?":
            k = self._kelvin(arg)
            inv = self.inverse.get(arg)
            if not math.isfinite(k):
                ohms = 0.0
            elif inv is None:
                ohms = k                    # uncalibrated input: sensor units == K here
            else:
                ohms, clamped = inv.ohms(k)
                if clamped:
                    ch = self.slots.get(arg, arg)
                    self.clamped[ch] = self.clamped.get(ch, 0) + 1
            text = f"{ohms:+.6E}"
        elif word == "RDGST?":
            if math.isfinite(self._kelvin(arg)):
                text = f"{int(self.range_bits.get(arg, 0)) & 0xFF:03d}"
            else:
                text = "001"                 # bit 1 = invalid reading
        elif word == "INNAME?":
            text = f'"{self.slots.get(arg, "")}"'
        else:
            return None                      # the 224 ignores unknown commands
        return text.encode("ascii") + b"\r\n"

    def garbage(self, request: bytes) -> bytes:
        return b"ERR?\r\n"


# ----------------------------------------------------------------- MKS 902B
class MKS902B(Emulator):
    """MKS 900-series ASCII: @<addr><query>?;FF -> @<addr>ACK<value>;FF."""

    name = "mks902b"
    eol = b";FF"
    _REQ = re.compile(rb"@(\d+)([A-Z0-9]+)\?;FF")
    #: sim-only NAK code: the record has no value for this moment
    NAK_NO_VALUE = "999"

    def __init__(self, channel: str, source: Source, address: int = 253):
        super().__init__()
        self.channel = channel
        self.source = source
        self.address = address

    def frame(self, buf: bytes):
        out = []
        while True:
            i = buf.find(b";FF")
            if i < 0:
                return out, buf
            out.append(buf[: i + 3])
            buf = buf[i + 3:]

    def respond(self, request: bytes) -> bytes | None:
        m = self._REQ.search(request)
        if not m:
            return None
        addr, query = m.group(1).decode(), m.group(2).decode()
        if query.startswith("PR"):
            p = self.source(self.channel)
            if not math.isfinite(p):
                return f"@{addr}NAK{self.NAK_NO_VALUE};FF".encode()
            return f"@{addr}ACK{p:.3E};FF".encode()
        if query == "DT":
            return f"@{addr}ACK902B-SIM;FF".encode()
        if query == "SN":
            return f"@{addr}ACKSIM00001;FF".encode()
        return f"@{addr}NAK160;FF".encode()           # unrecognised message

    def garbage(self, request: bytes) -> bytes:
        return b"@253X#?;FF"

    def nak(self, request: bytes) -> bytes:
        m = self._REQ.search(request)
        addr = m.group(1).decode() if m else str(self.address)
        return f"@{addr}NAK8;FF".encode()


# ----------------------------------------------------------------- TPG 262
class TPG262(Emulator):
    """Pfeiffer ACK/ENQ handshake.  Values served in the display unit, Torr by
    default (UNI = 1), as the live controller is set (2026-10-20) and as Ells
    wants it kept (2026-10-22: Genesis sets it to Torr whenever it is not).
    ``UNI,<n>`` changes the unit (0 mbar, 1 Torr, 2 Pa) like the real one.

    ``sources`` is (gauge1 channel, gauge2 channel); a NaN reading is sent
    with ``nan_status`` (default '4' Sensor off) and value 0, which the driver
    publishes as NaN with that reason.
    """

    name = "tpg262"
    ACK, ENQ, NAK = b"\x06", b"\x05", b"\x15"

    #: display unit code -> factor from Torr
    UNIT_FROM_TORR = {"0": conversions.TORR_TO_MBAR, "1": 1.0, "2": 133.322368}

    def __init__(self, channels: tuple[str, str], source: Source, nan_status: str = "4",
                 unit: str = "1"):
        super().__init__()
        if unit not in self.UNIT_FROM_TORR:
            raise ValueError(f"TPG 262 unit code {unit!r}: 0 mbar, 1 Torr or 2 Pa")
        self.unit = unit
        self.channels = channels
        self.source = source
        self.nan_status = nan_status
        self.status_override: dict[str, str] = {}
        self._pending = threading.local()
        self._last = "PRX"

    def frame(self, buf: bytes):
        out = []
        while buf:
            if buf[:1] == self.ENQ:
                out.append(self.ENQ)
                buf = buf[1:]
                continue
            i = buf.find(b"\n")
            j = buf.find(self.ENQ)
            if i < 0 or (0 <= j < i):
                if j < 0:
                    return out, buf
                # garbage before an ENQ: drop it
                buf = buf[j:]
                continue
            out.append(buf[: i + 1])
            buf = buf[i + 1:]
        return out, buf

    def _gauge(self, ch: str) -> tuple[str, float]:
        if ch in self.status_override:
            return self.status_override[ch], 0.0
        torr = self.source(ch)
        if not math.isfinite(torr):
            return self.nan_status, 0.0
        return "0", torr * self.UNIT_FROM_TORR[self.unit]

    def respond(self, request: bytes) -> bytes | None:
        if request == self.ENQ:
            mnemonic = self._last
            if mnemonic == "PRX":
                (s1, v1), (s2, v2) = (self._gauge(c) for c in self.channels)
                return f"{s1},{v1:.4E},{s2},{v2:.4E}\r\n".encode()
            if mnemonic == "UNI":
                return f"{self.unit}\r\n".encode()
            if mnemonic == "TID":
                return b"PKR,IKR\r\n"
            return self.NAK + b"\r\n"
        mnemonic = request.decode("ascii", "replace").strip().upper()
        if mnemonic.startswith("UNI,"):                     # set the display unit
            code = mnemonic[4:].strip()
            if code not in self.UNIT_FROM_TORR:
                return self.NAK + b"\r\n"
            self.unit = code
            self._last = "UNI"
            return self.ACK + b"\r\n"
        if mnemonic in ("PRX", "UNI", "TID"):
            self._last = mnemonic
            return self.ACK + b"\r\n"
        return self.NAK + b"\r\n"

    def nak(self, request: bytes) -> bytes:
        return self.NAK + b"\r\n"

    def garbage(self, request: bytes) -> bytes:
        return b"#$%\r\n"


# ----------------------------------------------------------------- IONIVAC
class IONIVAC(Emulator):
    """Streams 7-byte telegrams 07 05 .. hi lo .. about ten times a second.

    A NaN reading streams nothing, which the driver reports as
    "IONIVAC sent nothing" -- exactly what a gauge that has gone quiet does.
    """

    name = "ionivac"
    stream_interval_s = 0.1

    def __init__(self, channel: str, source: Source):
        super().__init__()
        self.channel = channel
        self.source = source
        try:
            self.header = bytes(_lazy_driver("ionivac").FRAME_HEADER)
        except Exception:      # noqa: BLE001 - constants only
            self.header = bytes((7, 5))

    def frame(self, buf: bytes):
        return [], b""                     # it never listens

    def respond(self, request: bytes) -> bytes | None:
        return None

    def stream(self) -> bytes | None:
        count = conversions.torr_to_ionivac_count(self.source(self.channel))
        if count is None:
            return None
        return self.header + bytes((0, 0, (count >> 8) & 0xFF, count & 0xFF, 0))

    def garbage(self, request: bytes) -> bytes:
        return bytes((1, 2, 3, 4, 5, 6, 8))


# ----------------------------------------------------------------- LS455
class LS455(Emulator):
    """Lake Shore 455 gaussmeter, CR/LF.  Field served in gauss (UNIT? -> 1)."""

    name = "ls455"
    IDN = "LSCI,MODEL455,SIM0001,1.0"

    def __init__(self, channel: str, source: Source):
        super().__init__()
        self.channel = channel
        self.source = source

    def respond(self, request: bytes) -> bytes | None:
        cmd = request.decode("ascii", "replace").strip().upper()
        if cmd == "*IDN?":
            text = self.IDN
        elif cmd == "RDGFIELD?":
            g = self.source(self.channel)
            text = f"{g:+.5E}" if math.isfinite(g) else "OL"     # overload: no number
        elif cmd == "UNIT?":
            text = "1"
        else:
            return None
        return text.encode("ascii") + b"\r\n"
