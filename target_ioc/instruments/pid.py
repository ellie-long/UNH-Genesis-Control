import asyncio
import logging
import time
from softioc import builder
from genesis_common.base_device import GenesisBaseDevice
import aioca


class Device(GenesisBaseDevice):
    """Self-contained PID Controller Device (no external PID dependency)

    Software PID controller that reads a process variable from one PV,
    computes a control output, and writes it to another PV via Channel Access.

    Compared to the simple_pid-backed ``pid_controller`` device, the control
    law here is implemented in-process (see :class:`PIDCore`) and adds:

      * True elapsed-time (``dt``) integration/derivative, measured with
        ``time.monotonic`` each cycle rather than assuming a fixed period.
      * Derivative-on-measurement (no derivative kick on setpoint changes).
      * Back-calculation integral anti-windup (``kaw``), which bleeds the
        integral term down smoothly while the output is saturated.
      * Bumpless Auto/Manual transfer: while in Manual the integrator tracks
        the manual output, so switching to Auto starts from the same value.
      * Optional feed-forward, either a constant and/or read from a PV.

    PV layout is identical to ``pid_controller`` so this device is a drop-in
    replacement -- change only ``module: 'target_ioc.instruments.pid'`` in
    settings.yaml.

    Config (``outs`` block in settings.yaml):
        kp, ki, kd:              PID gains
        setpoint:                initial setpoint
        min_output, max_output:  output limits (clamp + anti-windup range)
        kaw:                     anti-windup back-calculation gain (default 1.0)
        feedforward:             constant feed-forward added to output (default 0)
        auto_start:              True -> start in Auto, else Manual

    Optional top-level keys:
        input_pv:                PV to read as process value (required to control)
        output_pv:               PV to write the control output to
        feedforward_pv:          PV whose value is added as feed-forward
    """

    def __init__(self, device_name, settings):
        outs = settings['outs']
        self.pid_params = {
            'kp': outs.get('kp', 1.0),
            'ki': outs.get('ki', 0.0),
            'kd': outs.get('kd', 0.0),
            'setpoint': outs.get('setpoint', 0.0),
            'output_limits': (outs.get('min_output', 0.0), outs.get('max_output', 100.0)),
            'kaw': outs.get('kaw', 1.0),
        }
        self.auto_mode = outs.get('auto_start', False)
        self.feedforward = outs.get('feedforward', 0.0)

        self.input_pv = settings.get('input_pv')
        self.output_pv = settings.get('output_pv')
        self.feedforward_pv = settings.get('feedforward_pv')

        self._last_time = None  # monotonic timestamp of previous compute

        super().__init__(device_name, settings)

    def _create_pvs(self):
        """Create PID-specific PVs"""
        for channel in self._skip_none_channels():
            self.pvs[channel + "_SP"] = builder.aOut(
                channel + "_SP",
                initial_value=self.pid_params['setpoint'],
                on_update_name=self.do_sets,
                **self.sevr
            )
            self.pvs[channel + "_KP"] = builder.aOut(
                channel + "_KP",
                initial_value=self.pid_params['kp'],
                on_update_name=self.do_sets
            )
            self.pvs[channel + "_KI"] = builder.aOut(
                channel + "_KI",
                initial_value=self.pid_params['ki'],
                on_update_name=self.do_sets
            )
            self.pvs[channel + "_KD"] = builder.aOut(
                channel + "_KD",
                initial_value=self.pid_params['kd'],
                on_update_name=self.do_sets
            )
            # Process value (input) - read only
            self.pvs[channel + "_PV"] = builder.aIn(channel + "_PV", **self.sevr)
            # Control value (output) - read only for monitoring
            self.pvs[channel + "_CV"] = builder.aIn(channel + "_CV", **self.sevr)
            # Manual output value
            self.pvs[channel + "_MV"] = builder.aOut(
                channel + "_MV",
                initial_value=0.0,
                on_update_name=self.do_sets,
                **self.sevr
            )
            # Control mode: Manual (0) / Auto (1)
            self.pvs[channel + "_Mode"] = builder.mbbOut(
                channel + "_Mode",
                ("Manual", 'MINOR'),
                ("Auto", 0),
                initial_value=1 if self.auto_mode else 0,
                on_update_name=self.do_sets
            )
            # Output limits (clamp + anti-windup range)
            min_limit, max_limit = self.pid_params['output_limits']
            self.pvs[channel + "_DRVH"] = builder.aOut(
                channel + "_DRVH",
                initial_value=max_limit if max_limit is not None else 100.0,
                on_update_name=self.do_sets
            )
            self.pvs[channel + "_DRVL"] = builder.aOut(
                channel + "_DRVL",
                initial_value=min_limit if min_limit is not None else -100.0,
                on_update_name=self.do_sets
            )

    def _create_connection(self):
        """Create the in-process PID controller (no physical connection)"""
        return PIDCore(
            self.pid_params['kp'],
            self.pid_params['ki'],
            self.pid_params['kd'],
            setpoint=self.pid_params['setpoint'],
            output_limits=self.pid_params['output_limits'],
            kaw=self.pid_params['kaw'],
        )

    def connect(self):
        """Initialize PID controller"""
        super().connect()
        self._update_pid_params()

    def _update_pid_params(self):
        """Push gains, setpoint and output limits from PVs into the controller"""
        if self.t and self.channels[0] != "None":
            channel = self.channels[0]
            self.t.update_params(
                kp=self.pvs[channel + "_KP"].get(),
                ki=self.pvs[channel + "_KI"].get(),
                kd=self.pvs[channel + "_KD"].get(),
                setpoint=self.pvs[channel + "_SP"].get(),
                output_limits=(
                    self.pvs[channel + "_DRVL"].get(),
                    self.pvs[channel + "_DRVH"].get()
                )
            )

    def do_sets(self, new_value, pv):
        """Handle PV set operations"""
        gate_pv = pv.replace(self.device_name + ':', '')
        if not self.can_write(gate_pv):
            return

        pv_name = pv.replace(self.device_name + ':', '')

        if any(suffix in pv_name for suffix in ["_KP", "_KI", "_KD", "_SP", "_DRVH", "_DRVL"]):
            self._update_pid_params()

    async def _read_feedforward(self):
        """Return the total feed-forward: constant plus optional PV value."""
        ff = self.feedforward
        if self.feedforward_pv:
            ff += await aioca.caget(self.feedforward_pv, timeout=2)
        return ff

    async def do_reads(self):
        """Read input PV, compute PID output, and write to output PV"""
        try:
            # Real elapsed time since the previous cycle
            now = time.monotonic()
            dt = (now - self._last_time) if self._last_time is not None else 0.0
            self._last_time = now

            for channel in self._skip_none_channels():
                try:
                    input_value = await aioca.caget(self.input_pv, timeout=2)
                    self.pvs[channel + "_PV"].set(input_value)

                    ff = await self._read_feedforward()
                    mode = self.pvs[channel + "_Mode"].get()
                    if mode == 1:  # Auto mode
                        output = self.t.compute(input_value, dt, feedforward=ff)
                    else:  # Manual mode -- track so Auto transfer is bumpless
                        output = self.pvs[channel + "_MV"].get()
                        self.t.track(output, input_value, dt, feedforward=ff)

                    self.pvs[channel + "_CV"].set(output)
                    if self.output_pv:
                        await aioca.caput(self.output_pv, output, timeout=2)

                    self.remove_alarm(channel + "_PV")

                except aioca.CANothing as e:
                    logging.error(f"PID CA error: {e}")
                    self.set_alarm(channel + "_PV")
                    return False

            self._handle_read_success()
            return True

        except Exception as e:
            logging.error(f"PID control error: {e}")
            self._handle_read_error()
            return False


class PIDCore:
    """Self-contained positional PID controller.

    Implements derivative-on-measurement, back-calculation anti-windup, and a
    tracking mode for bumpless Auto/Manual transfer. All timing is driven by an
    explicit ``dt`` (seconds) supplied by the caller.
    """

    def __init__(self, kp, ki, kd, setpoint=0.0, output_limits=(None, None), kaw=1.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.setpoint = setpoint
        self.lo, self.hi = output_limits
        self.kaw = kaw
        self.reset()

    def reset(self):
        """Clear integrator and derivative/history state."""
        self._integral = 0.0
        self._prev_pv = None
        self._last_output = 0.0

    def update_params(self, **kwargs):
        """Update gains, setpoint and output limits."""
        if 'kp' in kwargs:
            self.kp = kwargs['kp']
        if 'ki' in kwargs:
            self.ki = kwargs['ki']
        if 'kd' in kwargs:
            self.kd = kwargs['kd']
        if 'setpoint' in kwargs:
            self.setpoint = kwargs['setpoint']
        if 'output_limits' in kwargs:
            self.lo, self.hi = kwargs['output_limits']

    def _clamp(self, value):
        if self.lo is not None and value < self.lo:
            return self.lo
        if self.hi is not None and value > self.hi:
            return self.hi
        return value

    def _derivative(self, pv, dt):
        """Derivative on measurement; zero on the first sample."""
        if self._prev_pv is None or dt <= 0:
            return 0.0
        return -self.kd * (pv - self._prev_pv) / dt

    def compute(self, pv, dt, feedforward=0.0):
        """Compute the control output for process value ``pv`` over ``dt`` seconds."""
        if dt <= 0:
            # No time has passed; keep the previous output but track pv for
            # the next derivative estimate.
            self._prev_pv = pv
            return self._last_output

        error = self.setpoint - pv
        d = self._derivative(pv, dt)

        self._integral += self.ki * error * dt
        output_pre = self.kp * error + self._integral + d + feedforward
        output = self._clamp(output_pre)

        # Back-calculation anti-windup: unwind the integrator while saturated.
        if output != output_pre:
            self._integral += self.kaw * (output - output_pre) * dt

        self._prev_pv = pv
        self._last_output = output
        return output

    def track(self, output, pv, dt, feedforward=0.0):
        """Manual/tracking mode.

        Holds the externally supplied ``output`` and back-computes the
        integrator so that a subsequent :meth:`compute` starts from the same
        value (bumpless transfer). Also advances the derivative history.
        """
        error = self.setpoint - pv
        d = self._derivative(pv, dt)
        # Choose integral so that kp*error + integral + d + ff == output
        self._integral = output - self.kp * error - d - feedforward
        self._prev_pv = pv
        self._last_output = output
