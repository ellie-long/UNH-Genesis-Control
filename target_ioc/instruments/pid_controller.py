import asyncio
import logging
from softioc import builder
from simple_pid import PID
from genesis_common.base_device import GenesisBaseDevice
import aioca


class Device(GenesisBaseDevice):
    """PID Controller Device (simple_pid backend)

    Software PID controller that reads a process variable from one PV,
    computes a control output, and writes it to another PV via Channel Access.

    This uses ``simple_pid``, which handles output clamping and integral
    anti-windup natively via ``output_limits``. Auto/Manual transitions are
    made bumpless with ``PID.set_auto_mode``.

    Config (``outs`` block in settings.yaml):
        kp, ki, kd:              PID gains
        setpoint:                initial setpoint
        min_output, max_output:  output limits (also drive anti-windup)
        auto_start:              True -> start in Auto, else Manual
        p_on_measurement:        proportional-on-measurement (default False)
        d_on_measurement:        derivative-on-measurement (default True,
                                 avoids derivative kick on setpoint changes)
    """

    def __init__(self, device_name, settings):
        outs = settings['outs']
        self.pid_params = {
            'kp': outs.get('kp', 1.0),
            'ki': outs.get('ki', 0.0),
            'kd': outs.get('kd', 0.0),
            'setpoint': outs.get('setpoint', 0.0),
            'output_limits': (outs.get('min_output', 0.0), outs.get('max_output', 100.0)),
            'p_on_measurement': outs.get('p_on_measurement', False),
            'd_on_measurement': outs.get('d_on_measurement', True),
        }
        self.auto_mode = outs.get('auto_start', False)

        self.input_pv = settings.get('input_pv')
        self.output_pv = settings.get('output_pv')

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
            # Output limits (feed simple_pid's output_limits / anti-windup)
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
        """Create PID connection (no physical connection needed)"""
        return PIDConnection(self.pid_params)

    def connect(self):
        """Initialize PID controller"""
        super().connect()
        # Sync the controller with the current PV values
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

        # Update controller parameters when any tuning PV changes
        if any(suffix in pv_name for suffix in ["_KP", "_KI", "_KD", "_SP", "_DRVH", "_DRVL"]):
            self._update_pid_params()

    async def do_reads(self):
        """Read input PV, compute PID output, and write to output PV"""
        try:
            for channel in self._skip_none_channels():
                try:
                    input_value = await aioca.caget(self.input_pv, timeout=2)
                    self.pvs[channel + "_PV"].set(input_value)

                    mode = self.pvs[channel + "_Mode"].get()
                    if mode == 1:  # Auto mode
                        # Bumpless transfer: prime the controller with the last
                        # manual output the first cycle after leaving Manual.
                        self.t.to_auto(self.pvs[channel + "_MV"].get())
                        output = self.t.compute(input_value)
                    else:  # Manual mode
                        self.t.to_manual()
                        output = self.pvs[channel + "_MV"].get()

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


class PIDConnection:
    """simple_pid controller wrapper"""

    def __init__(self, params):
        self.pid = PID(
            params['kp'],
            params['ki'],
            params['kd'],
            setpoint=params['setpoint'],
            output_limits=params['output_limits'],
            proportional_on_measurement=params['p_on_measurement'],
            differential_on_measurement=params['d_on_measurement'],
        )

    def compute(self, input_value):
        """Compute PID output for the given process value"""
        return self.pid(input_value)

    def update_params(self, **kwargs):
        """Update gains, setpoint and output limits"""
        if 'kp' in kwargs:
            self.pid.Kp = kwargs['kp']
        if 'ki' in kwargs:
            self.pid.Ki = kwargs['ki']
        if 'kd' in kwargs:
            self.pid.Kd = kwargs['kd']
        if 'setpoint' in kwargs:
            self.pid.setpoint = kwargs['setpoint']
        if 'output_limits' in kwargs:
            self.pid.output_limits = kwargs['output_limits']

    def to_manual(self):
        """Freeze the controller (Manual). No-op if already manual."""
        if self.pid.auto_mode:
            self.pid.set_auto_mode(False)

    def to_auto(self, last_output):
        """Resume auto control, seeding integral so output starts at
        ``last_output`` (bumpless). No-op if already in auto."""
        if not self.pid.auto_mode:
            self.pid.set_auto_mode(True, last_output=last_output)

    def reset(self):
        """Reset controller internal state"""
        self.pid.reset()
