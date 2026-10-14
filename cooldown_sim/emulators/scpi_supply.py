"""Siglent SPD1000X-series supply (SPD1168X coldboard, SPD1305X microwave UCA).

Stateful: setpoints, output, timer and OVP/OCP persist; measured values follow
the state through a resistive load, entering constant-current mode when the
load would draw more than the current limit.

Both Genesis drivers speak to it unchanged:

* ``target_ioc/instruments/spd1000x.py`` (coldboard; read-only profile, so a
  correct replay shows ZERO write commands here -- the read-only proof);
* ``microwave_ioc/spd1305x`` (UCA; writes enabled; RF ON needs 5.0 V on).

Every command that changes state is recorded as a write.
"""

from __future__ import annotations

import math
import threading

from .base import Emulator

#: Commands that change the supply's output state.  SYSTem:REMote is not one:
#: the read-only coldboard driver sends it on every connect, and it changes no
#: setpoint or output, so it must not count against the read-only proof.
_WRITE_PREFIXES = ("CH1:VOLT", "CH1:CURR", "OUTP", "TIME", "OVP ", "OCP ")


class ScpiSupply(Emulator):
    name = "scpi_supply"
    eol = b"\n"

    def __init__(self, model: str = "SPD1305X", *, voltage: float = 0.0, current: float = 0.0,
                 output: bool = False, load_ohms: float = 1.0e4, max_voltage: float = 30.0,
                 max_current: float = 5.0):
        super().__init__()
        self.model = model
        self._lock = threading.Lock()
        self.v_set = float(voltage)
        self.i_set = float(current)
        self.output = bool(output)
        self.timer = False
        self.ovp = max_voltage
        self.ocp = max_current
        self.load_ohms = float(load_ohms)
        self.max_voltage = max_voltage
        self.max_current = max_current
        self.errors: list[str] = []

    # ------------------------------------------------------------ physics
    def measured(self) -> tuple[float, float, bool]:
        """(volts, amps, constant_current) at the output terminals."""
        with self._lock:
            if not self.output:
                return 0.0, 0.0, False
            v, r = self.v_set, max(self.load_ohms, 1e-6)
            i = v / r
            if i > self.i_set:
                return self.i_set * r, self.i_set, True
            return v, i, False

    @property
    def power_w(self) -> float:
        v, i, _ = self.measured()
        return v * i

    def set_state(self, *, voltage=None, current=None, output=None, load_ohms=None) -> None:
        """Out-of-band state change (scenario / Sim Control), not a wire write."""
        with self._lock:
            if voltage is not None:
                self.v_set = float(voltage)
            if current is not None:
                self.i_set = float(current)
            if output is not None:
                self.output = bool(output)
            if load_ohms is not None:
                self.load_ohms = float(load_ohms)

    # ------------------------------------------------------------- wire
    def is_write(self, request: bytes) -> bool:
        cmd = request.decode("ascii", "replace").strip().upper()
        return not cmd.endswith("?") and "?" not in cmd and cmd.startswith(_WRITE_PREFIXES)

    def respond(self, request: bytes) -> bytes | None:
        raw = request.decode("ascii", "replace").strip()
        cmd = raw.upper()
        if cmd == "*IDN?":
            return f"Siglent Technologies,{self.model},SIM0001,1.01.01.02.05\n".encode()
        if cmd.startswith("MEAS"):
            v, i, _cc = self.measured()
            if "VOLT" in cmd:
                return f"{v:.3f}\n".encode()
            if "CURR" in cmd:
                return f"{i:.3f}\n".encode()
            if "POWE" in cmd:
                return f"{v * i:.3f}\n".encode()
        if cmd in ("CH1:VOLTAGE?", "CH1:VOLT?"):
            return f"{self.v_set:.3f}\n".encode()
        if cmd in ("CH1:CURRENT?", "CH1:CURR?"):
            return f"{self.i_set:.3f}\n".encode()
        if cmd in ("SYSTEM:STATUS?", "SYST:STAT?"):
            _v, _i, cc = self.measured()
            word = (0x10 if self.output else 0) | (0x40 if self.timer else 0) | (0x01 if cc else 0)
            return f"0x{word:02X}\n".encode()
        if cmd == "OVP?":
            return f"{self.ovp:.3f}\n".encode()
        if cmd == "OCP?":
            return f"{self.ocp:.3f}\n".encode()
        if cmd in ("SYSTEM:ERROR?", "SYST:ERR?"):
            return (self.errors.pop(0) if self.errors else "0,No Error").encode() + b"\n"
        # -------- writes (no reply)
        try:
            if cmd.startswith(("CH1:VOLTAGE ", "CH1:VOLT ")):
                value = float(raw.split()[-1])
                if 0 <= value <= self.max_voltage:
                    with self._lock:
                        self.v_set = value
                else:
                    self.errors.append("-222,Data out of range")
            elif cmd.startswith(("CH1:CURRENT ", "CH1:CURR ")):
                value = float(raw.split()[-1])
                if 0 <= value <= self.max_current:
                    with self._lock:
                        self.i_set = value
                else:
                    self.errors.append("-222,Data out of range")
            elif cmd.startswith("OUTPUT CH1,") or cmd.startswith("OUTP CH1,"):
                with self._lock:
                    self.output = cmd.endswith(",ON")
            elif cmd.startswith("TIMER CH1,") or cmd.startswith("TIME CH1,"):
                self.timer = cmd.endswith(",ON")
            elif cmd.startswith("OVP "):
                self.ovp = float(raw.split()[-1])
            elif cmd.startswith("OCP "):
                self.ocp = float(raw.split()[-1])
            elif cmd.startswith(("SYSTEM:REMOTE", "SYST:REM")):
                pass
            else:
                self.errors.append("-113,Undefined header")
        except ValueError:
            self.errors.append("-104,Data type error")
        return None

    def garbage(self, request: bytes) -> bytes:
        return b"#!garbage!#\n"
