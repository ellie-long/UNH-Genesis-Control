"""IPS120-10 serial protocol: the student's IPSClient and decoding, on Genesis's gateway.

Student (Magnet_V3) -> here:
    IPSClient.query          IPSClient.query      (+ echo check, stale-input drain)
    parse_numeric_response   parse_numeric_response
    read_demand_current      IPSClient.read_demand_current        R0
    read_persistent_current  IPSClient.read_persistent_current    R16
    read_setpoint            IPSClient.read_setpoint              R5
    read_sweep_rate          IPSClient.read_sweep_rate            R6
    read_status              IPSClient.read_status                X
    decode_status            decode_status        (+ system / limit digits)
    get_heater_status / get_activity_status / get_motion_status  (same)

Added: R1 (voltage), R2 (measured current), R17-R24 (trip, heater current,
safe limits, lead resistance, inductance), V, and fixed-format setters.

Manual references are to the 2006 edition (pp36-40).
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass

# ------------------------------------------------------------ decoding tables
ACTIVITY_MEANING = {"0": "HOLD", "1": "TO SET POINT", "2": "TO ZERO", "4": "CLAMPED"}
CONTROL_MEANING = {"0": "LOCAL / LOCKED", "1": "REMOTE / LOCKED", "2": "LOCAL / UNLOCKED",
                   "3": "REMOTE / UNLOCKED", "4": "AUTO-RUN-DOWN", "5": "AUTO-RUN-DOWN",
                   "6": "AUTO-RUN-DOWN", "7": "AUTO-RUN-DOWN"}
HEATER_MEANING = {"0": "OFF - MAGNET AT ZERO", "1": "ON - SWITCH OPEN", "2": "OFF - MAGNET AT FIELD",
                  "5": "HEATER FAULT", "8": "NO SWITCH"}
MOTION_MEANING = {"0": "AT REST", "1": "SWEEPING", "2": "SWEEP LIMITING", "3": "SWEEPING + SWEEP LIMITING"}
# review issue 2: the student decoded only A, C, H and M; the first two digits
# carry quench / overheat / fault and the limit flags (p39)
SYSTEM_MEANING = {"0": "NORMAL", "1": "QUENCHED", "2": "OVER HEATED", "4": "WARMING UP", "8": "FAULT"}
LIMIT_MEANING = {"0": "NORMAL", "1": "ON POSITIVE VOLTAGE LIMIT", "2": "ON NEGATIVE VOLTAGE LIMIT",
                 "4": "OUTSIDE NEGATIVE CURRENT LIMIT", "8": "OUTSIDE POSITIVE CURRENT LIMIT"}


class IPSError(OSError):
    """The supply refused a command ('?' reply) or answered out of protocol."""


@dataclass(frozen=True)
class Status:
    """The 15-character X reply, decoded (p39-40)."""
    raw: str
    system: str
    limits: str
    activity: str
    control: str
    heater: str
    mode: str
    motion: str

    @property
    def quenched(self) -> bool:
        return self.system == "1"

    @property
    def fault(self) -> bool:
        """Anything but a normal system state."""
        return self.system != "0"

    @property
    def heater_on(self) -> bool:
        return self.heater == "1"

    @property
    def heater_off(self) -> bool:
        return self.heater in ("0", "2")

    @property
    def at_rest(self) -> bool:
        return self.motion == "0"

    @property
    def clamped(self) -> bool:
        return self.activity == "4"

    def text(self) -> dict:
        return {
            "system": SYSTEM_MEANING.get(self.system, f"UNKNOWN X{self.system}"),
            "limits": LIMIT_MEANING.get(self.limits, f"UNKNOWN {self.limits}"),
            "activity": ACTIVITY_MEANING.get(self.activity, f"UNKNOWN A{self.activity}"),
            "control": CONTROL_MEANING.get(self.control, f"UNKNOWN C{self.control}"),
            "heater": HEATER_MEANING.get(self.heater, f"UNKNOWN H{self.heater}"),
            "motion": MOTION_MEANING.get(self.motion, f"UNKNOWN M{self.motion}"),
        }


_STATUS = re.compile(r"X(\d)(\d)A(\d)C(\d)H(\d)M(\d)(\d)P(\d)(\d)")


def decode_status(status: str) -> Status:
    """Decode ``XmnAnCnHnMmnPmn`` (p39).  The fields are found by their
    letters, so a reply with extra trailing characters still decodes;
    anything without every field is refused."""
    s = status.strip()
    m = _STATUS.match(s)
    if not m:
        raise IPSError(f"not an IPS status word: {status!r}")
    system, limits, activity, control, heater, mode, motion = m.groups()[:7]
    return Status(raw=s, system=system, limits=limits, activity=activity, control=control,
                  heater=heater, mode=mode, motion=motion)


def get_xmn_status(status) -> str:
    """The student's (V4) ``Xmn``: the leading system / limit digits."""
    s = str(status).strip()
    return s[:3] if len(s) >= 3 and s.startswith("X") else "X??"


def decode_xmn(status) -> str:
    """The student's (V4) ``XMN_MEANING``, with the manual's codes (p32:
    m 1 / 2 / 4 / 8, n 1 / 2 / 4 / 8; V4 numbers them 1-4 in sequence)."""
    xmn = get_xmn_status(status)
    if "?" in xmn:
        return "UNKNOWN"
    return (f"{SYSTEM_MEANING.get(xmn[1], f'UNKNOWN m={xmn[1]}')} | "
            f"{LIMIT_MEANING.get(xmn[2], f'UNKNOWN n={xmn[2]}')}")


# the student's accessors, kept for parity
def get_heater_status(status: str) -> str:
    return decode_status(status).heater


def get_activity_status(status: str) -> str:
    return decode_status(status).activity


def get_motion_status(status: str) -> str:
    return decode_status(status).motion


def parse_numeric_response(response: str) -> float:
    """``R+00.600`` -> 0.6."""
    if not response:
        raise IPSError("empty IPS response")
    text = response[1:] if response.startswith("R") else response
    if text == "":
        raise IPSError(f"incomplete IPS numeric response: {response!r}")
    try:
        value = float(text)
    except ValueError as exc:
        raise IPSError(f"bad IPS number: {response!r}") from exc
    if not math.isfinite(value):
        raise IPSError(f"bad IPS number: {response!r}")
    return value


def format_current(amps: float, decimals: int = 3) -> str:
    """Fixed-point amps for I / S commands (review issue 10: never 1e-05 or
    48.66600000000001)."""
    amps = float(amps)
    if not math.isfinite(amps):
        raise ValueError(f"not a finite number: {amps!r}")
    return f"{amps:.{decimals}f}"


class IPSClient:
    """One conversation at a time with the supply through a serial gateway.

    ``transport`` is a genesis_common.serial_tcp_base.SerialTcpConnection (or
    anything with send / read_until / discard_input / close).  The lock keeps
    the polling loop and a running sequence from interleaving on the socket,
    as in the student's client.
    """

    terminator = b"\r"

    def __init__(self, transport):
        self.t = transport
        self.lock = threading.Lock()

    def close(self):
        self.t.close()

    def query(self, command: str) -> str | None:
        """Send one command and return the reply (``None`` for ``Q``, which
        answers nothing)."""
        if "\r" in command or "\n" in command:
            raise ValueError("IPS command must not contain line terminators")
        with self.lock:
            # review issue 4: drop anything left over from an earlier
            # conversation (a late reply must never answer this command)
            self.t.discard_input()
            self.t.send(command.encode("ascii") + self.terminator)
            if command[:1].upper() == "Q":
                return None
            reply = self.t.read_until(self.terminator).decode("ascii", errors="replace").strip()
        if reply.startswith("?"):
            raise IPSError(f"IPS refused {command!r}: {reply!r}")
        # every reply starts with the command's letter (p36), except Q (no
        # reply) and V, whose reply is the version text (p39: "IPS120-10 ...")
        if command[:1].upper() == "V":
            if not reply:
                raise IPSError("empty reply to V")
            return reply
        if not reply or reply[0].upper() != command[0].upper():
            raise IPSError(f"reply {reply!r} does not answer {command!r}")
        return reply

    # ------------------------------------------------------------- reads
    def read(self, n: int) -> float:
        return parse_numeric_response(self.query(f"R{int(n)}"))

    def read_demand_current(self) -> float:          # R0
        return self.read(0)

    def read_voltage(self) -> float:                 # R1
        return self.read(1)

    def read_measured_current(self) -> float:        # R2
        return self.read(2)

    def read_setpoint(self) -> float:                # R5
        return self.read(5)

    def read_sweep_rate(self) -> float:              # R6
        return self.read(6)

    def read_persistent_current(self) -> float:      # R16
        return self.read(16)

    def read_trip_current(self) -> float:            # R17
        return self.read(17)

    def read_heater_current(self) -> float:          # R20, mA (most likely the setting)
        return self.read(20)

    def read_safe_limits(self) -> tuple[float, float]:   # R21, R22
        return self.read(21), self.read(22)

    def read_lead_resistance(self) -> float:         # R23, milliohm (stored)
        return self.read(23)

    def read_inductance(self) -> float:              # R24, henry (stored)
        return self.read(24)

    def read_status(self) -> Status:                 # X
        return decode_status(self.query("X"))

    def read_version(self) -> str:                   # V
        return self.query("V")

    # ------------------------------------------------------------- writes
    def remote_unlocked(self):                       # C3 (Ells: the operating state)
        self.query("C3")

    def hold(self):                                  # A0 (also leaves the clamp)
        self.query("A0")

    def to_setpoint(self):                           # A1
        self.query("A1")

    def to_zero(self):                               # A2
        self.query("A2")

    def set_target_current(self, amps: float):       # I
        self.query("I" + format_current(amps))

    def set_sweep_rate(self, amps_per_min: float):   # S
        self.query("S" + format_current(amps_per_min))

    def heater_off(self):                            # H0
        self.query("H0")

    def heater_on(self):                             # H1: the supply's own current-match check (V4)
        self.query("H1")

    def heater_on_unchecked(self):                   # H2: no check (V3; V4 never sends it)
        self.query("H2")
