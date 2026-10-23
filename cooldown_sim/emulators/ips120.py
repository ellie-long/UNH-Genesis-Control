"""Oxford Instruments IPS120-10 superconducting magnet power supply (emulator).

Built from the IPS120-10 operator's handbook (2006 edition, the copy in the
lab's private reference folder; page numbers below are that copy's), so the
Genesis magnet IOC can be exercised end to end without the real supply.

Protocol (section 8, pp36-45)
-----------------------------
ASCII, one command per line terminated by CR; the reply is terminated by CR
and starts with the command's letter (``R+00.000``, ``X00A0C3H1M00P00``,
``A``).  An unrecognised or refused command answers ``?`` + the command.
``Q`` sets the protocol and sends no reply.  Control commands (A, H, I, S,
J, T, M, P) are refused unless in REMOTE (C1 / C3).  A1 / A2 are refused while
clamped (A4); A0 is the only way out of the clamp (p21, p23).

Physics
-------
* switch open (heater ON, ``H1``): the magnet current follows the output
  current, which sweeps toward the target at the set rate (A/min); the output
  voltage is L dI/dt + I R_leads;
* switch closed (heater OFF): the magnet is persistent at the current it had
  when the heater went off (``R16``); the output (the leads) moves in
  IMMEDIATE mode at the fast lead rate, flagged as SWEEP LIMITING (p40);
* the heater takes ``switch_open_s`` to open the switch and
  ``switch_close_s`` to close it (the manual: 10-15 s);
* heater on with output != persistent current: the supply's own ``H1``
  refuses; ``H2`` (no check) opens it anyway, and the magnet current jumps to
  the output current (what the student's H2 check exists to prevent);
* faults (on top of the base class's comm faults): ``quench()`` (system
  status 1, magnet current to 0, trip current recorded), ``power_cycle()``
  (clamped, heater off, local & locked, Q0; a persistent magnet stays
  persistent, a driven one "slowly runs down" -- here: caught persistent at
  its current when the heater lost power, the open question 93 d).

Time is wall time (``time.monotonic``), scaled by ``time_scale`` for tests.
"""

from __future__ import annotations

import math
import threading
import time

from .base import Emulator

CR = b"\r"

#: control commands refused in LOCAL (p36: "only recognised when in REMOTE")
CONTROL = frozenset("AHIJMPST")


class IPS120(Emulator):
    name = "ips120"
    eol = CR

    def __init__(self, *, inductance_h: float = 38.0, lead_resistance_mohm: float = 12.0,
                 heater_ma: float = 40.0, amps_per_tesla: float = 9.7332,
                 safe_limits_a: tuple[float, float] = (-0.1, 67.9),
                 fast_lead_rate_a_per_min: float = 240.0,
                 switch_open_s: float = 12.0, switch_close_s: float = 12.0,
                 r20_mode: str = "setting", h1_match_tolerance_a: float = 0.0005,
                 initial_current_a: float = 0.0, initial_persistent: bool = False,
                 time_scale: float = 1.0, clock=time.monotonic):
        super().__init__()
        self.L = float(inductance_h)
        self.r_leads = float(lead_resistance_mohm) / 1000.0
        self.heater_ma = float(heater_ma)
        self.amps_per_tesla = float(amps_per_tesla)
        self.safe_neg, self.safe_pos = safe_limits_a
        self.fast_rate = float(fast_lead_rate_a_per_min)
        self.switch_open_s, self.switch_close_s = float(switch_open_s), float(switch_close_s)
        # R20: the configured heater current (Sup 15, manual p52), or the
        # measured heater current (the student's V4 reading: ~0 with the heater
        # off).  The real supply's behaviour is not known yet, so both exist.
        if r20_mode not in ("setting", "measured"):
            raise ValueError("r20_mode must be 'setting' or 'measured'")
        self.r20_mode = r20_mode
        self.h1_tolerance = float(h1_match_tolerance_a)
        self.time_scale = float(time_scale)
        self._clock = clock
        self._lock = threading.RLock()
        # front-panel / remote state
        self.control = 3                 # C3: remote & unlocked (the lab's operating state)
        self.activity = 0                # A0 hold
        self.resolution = 0              # Q0 / Q4
        self.target = float(initial_current_a)
        self.rate = 1.0                  # A/min
        self.output = float(initial_current_a)   # demand / output current (R0)
        self.magnet = float(initial_current_a)   # current in the magnet coil
        self.persistent_current = float(initial_current_a)
        self.trip_current = 0.0
        self.system = 0                  # X m: 0 normal, 1 quenched, 2 over heated, 8 fault
        self.limits_flag = 0             # X n
        self.voltage = 0.0
        # heater / switch: True = heater powered; switch_open follows it after a delay
        self.heater_on = not initial_persistent
        self.switch_open = not initial_persistent
        self._switch_change_at = None    # scaled time at which the switch reaches the heater state
        self._last = self._now()
        self.last_command = ""

    # ------------------------------------------------------------- time
    def _now(self) -> float:
        return self._clock() * self.time_scale

    def advance(self) -> None:
        """Move the physics up to now (called before every reply)."""
        with self._lock:
            now = self._now()
            dt = max(0.0, now - self._last)
            self._last = now
            if self._switch_change_at is not None and now >= self._switch_change_at:
                self._switch_change_at = None
                if self.heater_on and not self.switch_open:
                    self.switch_open = True
                    self.magnet = self.output          # an H2 at a mismatch: the current jumps
                elif not self.heater_on and self.switch_open:
                    self.switch_open = False
                    self.persistent_current = self.magnet
            goal = {1: self.target, 2: 0.0}.get(self.activity)
            before = self.output
            if goal is not None and dt > 0:
                rate = self.rate if self.switch_open else self.fast_rate
                step = rate / 60.0 * dt
                if abs(goal - self.output) <= step:
                    self.output = goal
                else:
                    self.output += math.copysign(step, goal - self.output)
            if self.switch_open:
                self.magnet = self.output
            didt = (self.output - before) / dt if dt > 0 else 0.0
            self.voltage = (self.L * didt if self.switch_open else 0.0) + self.output * self.r_leads
            self.limits_flag = 8 if self.output > self.safe_pos else 4 if self.output < self.safe_neg else 0

    def _moving(self) -> bool:
        goal = {1: self.target, 2: 0.0}.get(self.activity)
        return goal is not None and abs(goal - self.output) > 1e-9

    # ------------------------------------------------------------- faults
    def quench(self) -> None:
        """The magnet goes normal: field to zero, trip current recorded."""
        with self._lock:
            self.advance()
            self.trip_current = self.magnet
            self.magnet = 0.0
            self.persistent_current = 0.0
            self.output = 0.0
            self.system = 1
            self.activity = 0

    def power_cycle(self) -> None:
        """Mains lost and restored (p47): clamped, heater off, local & locked."""
        with self._lock:
            self.advance()
            if self.switch_open:                       # driven: the heater lost power
                self.persistent_current = self.magnet  # (question 93 d) caught persistent
            self.heater_on = self.switch_open = False
            self._switch_change_at = None
            self.output = 0.0
            self.activity = 4
            self.control = 0
            self.resolution = 0
            self.voltage = 0.0

    # ------------------------------------------------------------- framing
    def frame(self, buf: bytes) -> tuple[list[bytes], bytes]:
        out = []
        while True:
            i = buf.find(CR)
            if i < 0:
                return out, buf
            out.append(buf[: i + 1])
            buf = buf[i + 1:]

    def is_write(self, request: bytes) -> bool:
        c = request.decode("ascii", "replace").strip()[:1].upper()
        return c in CONTROL or c in ("C", "Q")

    def nak(self, request: bytes) -> bytes:
        return b"?" + request.strip() + CR

    def garbage(self, request: bytes) -> bytes:
        return b"R+\x7f\x00??" + CR

    # ------------------------------------------------------------- replies
    def _num(self, letter: str, value: float, decimals: int = 3) -> bytes:
        d = decimals + (1 if self.resolution in (4, 6) else 0)
        return f"{letter}{value:+0{d + 4}.{d}f}".encode() + CR

    def _status(self) -> str:
        if self.heater_on and self.switch_open:
            h = 1
        elif self.heater_on:
            h = 1                                      # on, switch still opening
        else:
            h = 2 if abs(self.persistent_current) > 1e-6 else 0
        moving = self._moving()
        n = (1 if self.switch_open else 2) if moving else 0
        return f"X{self.system}{self.limits_flag}A{self.activity}C{self.control}H{h}M0{n}P00"

    def respond(self, request: bytes) -> bytes | None:
        text = request.decode("ascii", "replace").strip()
        if not text:
            return None
        with self._lock:
            self.advance()
            self.last_command = text
            c, arg = text[0].upper(), text[1:]
            refuse = b"?" + text.encode() + CR
            if c == "Q":
                if arg in ("0", "2", "4", "6"):
                    self.resolution = int(arg)
                return None
            if c == "V":
                # the manual's form (p39); note it does not echo 'V'
                return b"IPS120-10  Version 3.07  (c) OXFORD 1996 (emulator)" + CR
            if c == "X":
                return self._status().encode() + CR
            if c == "R":
                return self._read(arg, refuse)
            if c == "C":
                if arg not in ("0", "1", "2", "3"):
                    return refuse
                self.control = int(arg)
                return b"C" + CR
            if c in CONTROL and self.control not in (1, 3):
                return refuse                          # LOCAL: control commands refused
            if c == "A":
                if arg not in ("0", "1", "2", "4"):
                    return refuse
                a = int(arg)
                if self.activity == 4 and a in (1, 2):
                    return refuse                      # clamped: only HOLD unclamps (p34)
                self.activity = a
                return b"A" + CR
            if c == "I":
                try:
                    value = float(arg)
                except ValueError:
                    return refuse
                if not (self.safe_neg <= value <= self.safe_pos) or not math.isfinite(value):
                    return refuse
                self.target = value
                return b"I" + CR
            if c == "S":
                try:
                    value = float(arg)
                except ValueError:
                    return refuse
                if not (0.0 < value <= 120.0):
                    return refuse
                self.rate = value
                return b"S" + CR
            if c == "H":
                if arg not in ("0", "1", "2"):
                    return refuse
                h = int(arg)
                if self._moving():
                    return refuse                      # p21: the button only works when stable
                if h == 0:
                    if self.heater_on:
                        self.heater_on = False
                        self._switch_change_at = self._now() + self.switch_close_s
                    return b"H" + CR
                if h == 1 and abs(self.output - self.persistent_current) > self.h1_tolerance \
                        and not self.switch_open:
                    return refuse                      # the supply's own current-match check
                if not self.heater_on:
                    self.heater_on = True
                    self._switch_change_at = self._now() + self.switch_open_s
                return b"H" + CR
            return refuse

    def _r20(self) -> float:
        if self.r20_mode == "setting":
            return self.heater_ma
        return self.heater_ma if self.heater_on else 0.0

    def _read(self, arg: str, refuse: bytes) -> bytes:
        try:
            n = int(arg)
        except ValueError:
            return refuse
        values = {
            0: self.output, 1: self.voltage, 2: self.magnet if self.switch_open else self.output,
            4: self.output, 5: self.target, 6: self.rate,
            7: self.output / self.amps_per_tesla, 8: self.target / self.amps_per_tesla,
            9: self.rate / self.amps_per_tesla, 15: 10.0,
            16: self.persistent_current, 17: self.trip_current,
            18: self.persistent_current / self.amps_per_tesla,
            19: self.trip_current / self.amps_per_tesla,
            20: self._r20(), 21: self.safe_neg, 22: self.safe_pos,
            23: self.r_leads * 1000.0, 24: self.L,
        }
        if n not in values:
            return refuse
        return self._num("R", values[n])
