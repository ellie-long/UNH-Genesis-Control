"""
logic_devices/pid.py - a PID loop as a logic IOC.

"PID without a PID record" from the JLab talk: pythonSoftIOC has no EPICS PID
record, but all of Python is there, so the loop is an ordinary logic IOC that
cagets an input PV, runs simple_pid, and caputs an output PV.  Any PV in, any
PV out - the loop does not know or care which instrument is on either end.

Everything an operator needs to touch is a PV, so the loop is tuned live from
CS-Studio without restarting anything:

    <name>_SP       aOut     setpoint, in the input PV's units
    <name>_Kp/_Ki/_Kd aOut   gains; writing one re-tunes on the next cycle
    <name>_Auto     boolOut  1 = drive the output, 0 = compute but do not write
    <name>_In       aIn      last input reading
    <name>_Out      aIn      last output value written (or that would be)
    <name>_Err      aIn      setpoint - input
    <name>_OutLo/_OutHi aOut output clamp; also the integrator's anti-windup limits
    <name>_Status   stringIn why the loop is not running, '' when it is

Manual mode is not a pause: the loop keeps computing and keeps its integrator
in step with the output actually on the instrument (bumpless transfer), so
switching to Auto does not kick the heater.

Safety rules, all of them one-directional:
  * a missing or NaN input reading holds the last output and says so.  It never
    drives the output up to chase a reading that is not there;
  * `_OutLo`/`_OutHi` clamp every write, and the clamp is applied before the
    caput, not after;
  * `max_step` (settings.yaml) limits how far one cycle may move the output.

settings.yaml

    pid_fridge:
      module: 'logic_devices.pid'
      delay: 2
      name: 'PID_Fridge'          # PV prefix for this loop
      input_pv:  'F1_TI'          # what we regulate
      output_pv: 'Coldboard_VC'   # what we drive
      setpoint: 1.2
      kp: 1.0
      ki: 0.05
      kd: 0.0
      out_lo: 0.0
      out_hi: 5.0
      max_step: 0.5               # volts per cycle
      reverse: False              # True when more output means a LOWER input
      channels: [None]
"""

import logging
import math

import aioca
from softioc import alarm, builder

log = logging.getLogger(__name__)

try:                                    # the toolkit's choice
    from simple_pid import PID as _SimplePID
except ImportError:                     # keeps the IOC and its tests runnable
    _SimplePID = None


class _FallbackPID:
    """Positional PID with the same constructor and call signature as
    simple_pid.PID, for the subset this loop uses.  Used only when simple_pid
    is not installed; `pip install simple-pid` and this goes away."""

    def __init__(self, Kp, Ki, Kd, setpoint=0.0, output_limits=(None, None)):
        self.Kp, self.Ki, self.Kd = Kp, Ki, Kd
        self.setpoint = setpoint
        self.output_limits = output_limits
        self._integral = 0.0
        self._last_error = None
        self.auto_mode = True

    def set_auto_mode(self, enabled, last_output=None):
        self.auto_mode = enabled
        if enabled and last_output is not None:
            self._integral = float(last_output)     # bumpless transfer
            self._last_error = None

    def reset(self):
        self._integral = 0.0
        self._last_error = None

    def __call__(self, value, dt=1.0):
        error = self.setpoint - value
        self._integral += self.Ki * error * dt
        lo, hi = self.output_limits
        if lo is not None:
            self._integral = max(lo, self._integral)
        if hi is not None:
            self._integral = min(hi, self._integral)
        derivative = 0.0 if self._last_error is None else (error - self._last_error) / dt
        self._last_error = error
        out = self.Kp * error + self._integral + self.Kd * derivative
        if lo is not None:
            out = max(lo, out)
        if hi is not None:
            out = min(hi, out)
        return out


def _pid(kp, ki, kd, setpoint, limits):
    cls = _SimplePID or _FallbackPID
    return cls(kp, ki, kd, setpoint=setpoint, output_limits=limits)


class Device:
    def __init__(self, device_name, settings):
        self.device_name = device_name
        self.settings = settings
        self.name = settings.get("name", "PID")
        self.input_pv = settings["input_pv"]
        self.output_pv = settings["output_pv"]
        self.dt = float(settings.get("delay", 2))
        self.max_step = float(settings.get("max_step", 0)) or None
        self.reverse = bool(settings.get("reverse", False))

        self.last_output = None
        self.pvs = {}
        self._create_pvs()
        self.pid = _pid(self._f("Kp"), self._f("Ki"), self._f("Kd"),
                        self._f("SP"), self._limits())

    # ------------------------------------------------------------------ PVs
    def _create_pvs(self):
        n, s = self.name, self.settings
        out_lo = float(s.get("out_lo", 0.0))
        out_hi = float(s.get("out_hi", 100.0))
        self.pvs[f"{n}_SP"] = builder.aOut(f"{n}_SP", initial_value=float(s.get("setpoint", 0.0)),
                                           on_update=self._retune)
        self.pvs[f"{n}_Kp"] = builder.aOut(f"{n}_Kp", initial_value=float(s.get("kp", 1.0)),
                                           on_update=self._retune)
        self.pvs[f"{n}_Ki"] = builder.aOut(f"{n}_Ki", initial_value=float(s.get("ki", 0.0)),
                                           on_update=self._retune)
        self.pvs[f"{n}_Kd"] = builder.aOut(f"{n}_Kd", initial_value=float(s.get("kd", 0.0)),
                                           on_update=self._retune)
        self.pvs[f"{n}_OutLo"] = builder.aOut(f"{n}_OutLo", initial_value=out_lo,
                                              on_update=self._retune)
        self.pvs[f"{n}_OutHi"] = builder.aOut(f"{n}_OutHi", initial_value=out_hi,
                                              on_update=self._retune)
        self.pvs[f"{n}_Auto"] = builder.boolOut(f"{n}_Auto", ZNAM="Manual", ONAM="Auto",
                                                initial_value=0, on_update=self._on_auto)
        self.pvs[f"{n}_In"] = builder.aIn(f"{n}_In", initial_value=0.0)
        self.pvs[f"{n}_Out"] = builder.aIn(f"{n}_Out", initial_value=0.0)
        self.pvs[f"{n}_Err"] = builder.aIn(f"{n}_Err", initial_value=0.0)
        self.pvs[f"{n}_Status"] = builder.stringIn(f"{n}_Status", initial_value="starting")

    def _f(self, suffix):
        return float(self.pvs[f"{self.name}_{suffix}"].get())

    def _limits(self):
        return (self._f("OutLo"), self._f("OutHi"))

    def _retune(self, value=None):
        """Any gain, setpoint or clamp changed: apply it to the controller."""
        self.pid.setpoint = self._f("SP")
        self.pid.Kp, self.pid.Ki, self.pid.Kd = self._f("Kp"), self._f("Ki"), self._f("Kd")
        self.pid.output_limits = self._limits()

    def _on_auto(self, value):
        """Going to Auto starts the integrator from the output the instrument
        is actually at, so the first cycle does not step the heater."""
        if value:
            self.pid.set_auto_mode(True, last_output=self.last_output)
            log.info("%s: auto, resuming from output %s", self.name, self.last_output)
        else:
            log.info("%s: manual", self.name)

    def connect(self):
        self.pvs[f"{self.name}_Status"].set("idle")

    # -------------------------------------------------------------- the loop
    def _full(self, pv):
        return pv if ":" in pv else f"{self.device_name}:{pv}"

    def _status(self, text, severity=0):
        self.pvs[f"{self.name}_Status"].set(text[:39])
        self.pvs[f"{self.name}_Out"].set_alarm(
            severity=severity, alarm=alarm.NO_ALARM if not severity else alarm.STATE_ALARM)

    def _clamp_step(self, out):
        """Never move the output more than max_step in one cycle."""
        if self.max_step and self.last_output is not None:
            delta = out - self.last_output
            if abs(delta) > self.max_step:
                out = self.last_output + math.copysign(self.max_step, delta)
        return out

    async def do_reads(self):
        n = self.name
        try:
            value = float(await aioca.caget(self._full(self.input_pv), timeout=2))
        except Exception as e:                      # noqa: BLE001
            self._status(f"no input: {self.input_pv}", severity=2)
            log.warning("%s: could not read %s (%s)", n, self.input_pv, e)
            return True
        if math.isnan(value):
            self._status("input is NaN, holding", severity=2)
            return True

        if self.last_output is None:                # first cycle: learn the output
            try:
                self.last_output = float(await aioca.caget(self._full(self.output_pv), timeout=2))
            except Exception:                       # noqa: BLE001
                self.last_output = self._f("OutLo")

        self.pvs[f"{n}_In"].set(value)
        self.pvs[f"{n}_Err"].set(self._f("SP") - value)

        # A reverse-acting loop (more output -> colder) is the same controller
        # with the sign of the error flipped, which simple_pid does by feeding
        # it the mirrored input rather than negative gains.
        measured = (2 * self._f("SP") - value) if self.reverse else value
        out = self.pid(measured, dt=self.dt)
        lo, hi = self._limits()
        out = min(hi, max(lo, self._clamp_step(out)))
        self.pvs[f"{n}_Out"].set(out)

        if not self.pvs[f"{n}_Auto"].get():
            self._status("manual")
            return True

        try:
            await aioca.caput(self._full(self.output_pv), out, timeout=2)
            self.last_output = out
            self._status("")
        except Exception as e:                      # noqa: BLE001
            self._status(f"no output: {self.output_pv}", severity=2)
            log.warning("%s: could not write %s (%s)", n, self.output_pv, e)
        return True
