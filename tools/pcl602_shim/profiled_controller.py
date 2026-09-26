"""Resolution-aware controller. Public positions are fixed 1/256-step units.

Never reinterpret historical pulses using a new divisor: each change captures a
canonical/raw anchor pair. No position-register rewrite is needed when switching.
Zero-reference status is informational; motion is allowed before referencing.
"""
import math
from controller import Axis
from motion import AxisConfig

class ProfiledController:
    def __init__(self, raw, axes, profiles):
        self.raw = raw
        self.axes = axes
        self.profiles = profiles
        self.profile = -1
        self.factor = {}
        self.raw_anchor = {}
        self.position_anchor = {}
        self.referenced = {a: False for a in Axis}
        self.rates = {}
        self._validate()
        raw.emergency_stop()
        if any(raw.busy(a) for a in Axis):
            raise RuntimeError('axes must be stopped before shim startup')
        for a in Axis:
            self.factor[a] = 4 if raw.outputs(a) & 2 else 1
            self.raw_anchor[a] = raw.position(a)
            self.position_anchor[a] = 0
        self.set_profile(0)

    def _validate(self):
        if len(self.profiles) != 3:
            raise ValueError('exactly three motion profiles required')
        for a in Axis:
            c = self.axes[a.value]
            scale = c['steps_per_cm_256']
            if not math.isfinite(scale) or scale <= 0:
                raise ValueError('steps_per_cm_256 must be finite and positive')
            if c['physical_sign'] not in (-1, 1):
                raise ValueError('physical_sign must be -1 or 1')
        previous = 0
        for p in self.profiles:
            if p['resolution'] not in (256, 64):
                raise ValueError('resolution must be 256 or 64')
            speed = p['speed_cm_s']
            if not math.isfinite(speed) or speed <= previous:
                raise ValueError('profile speeds must be positive and increasing')
            previous = speed
            for a in Axis:
                self._rates(a, p)

    def _rates(self, a, profile):
        scale = self.axes[a.value]['steps_per_cm_256'] * profile['resolution'] / 256
        values = [profile[k] * scale for k in ('base_cm_s', 'speed_cm_s', 'accel_cm_s2')]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('profile rates must be finite')
        base, speed, accel = [int(math.floor(v + .5)) for v in values]
        if not 1 <= base < speed <= 50000 or base > 5000 or not 100 <= accel <= 9999999:
            raise ValueError('profile exceeds controller speed/acceleration ranges')
        return base, speed, accel

    def verify_resolution(self, a):
        factor = 4 if self.raw.outputs(a) & 2 else 1
        if factor != self.factor[a]:
            raise RuntimeError('resolution changed outside shim; re-reference required')

    def position(self, a):
        self.verify_resolution(a)
        return self.position_anchor[a] + (self.raw.position(a) - self.raw_anchor[a]) * self.factor[a]

    def set_position(self, a, value):
        if self.raw.busy(a):
            raise ValueError('cannot reference a moving axis')
        self.verify_resolution(a)
        self.raw.set_position(a, 0)
        if self.raw.position(a) != 0:
            raise RuntimeError('position zero readback failed')
        self.raw_anchor[a] = 0
        self.position_anchor[a] = int(value)
        self.referenced[a] = True

    def step_quantum(self, a):
        return self.factor[a]

    def move_relative(self, a, steps):
        self.verify_resolution(a)
        # Round toward zero so coarse quantization cannot exceed a checked target.
        pulses = (abs(int(steps)) // self.factor[a]) * (1 if steps >= 0 else -1)
        if abs(pulses) > 8388607:
            raise ValueError('move exceeds single-command hardware pulse limit')
        if not pulses:
            raise ValueError('distance is below one pulse at selected speed')
        current = self.position(a)
        target = current + pulses * self.factor[a]
        c = self.axes[a.value]
        AxisConfig(c['minimum'], c['maximum'], coordinate_limit=2_147_483_647,
                   limits_enabled=c.get('limits_enabled', True)).validate_target(target, current)
        raw_position = self.raw.position(a)
        if not -8388607 <= raw_position + pulses <= 8388607:
            # Rebase only the controller counter, without moving or changing the
            # canonical physical position. Prevent accumulated mixed-mode counts
            # from exhausting the hardware register on repeated round trips.
            self.raw.set_position(a, 0)
            if self.raw.position(a) != 0:
                raise RuntimeError('counter rebase verification failed')
            self.raw_anchor[a] = 0
            self.position_anchor[a] = current
        self.raw.move_relative(a, pulses)

    def set_profile(self, index):
        if type(index) is not int or not 0 <= index < len(self.profiles):
            raise ValueError('invalid speed profile')
        if any(self.raw.busy(a) for a in Axis):
            raise ValueError('speed changes require both axes idle')
        profile = self.profiles[index]
        # Collect both anchors before changing any output; preserve fractional
        # coarse-step offsets exactly as integer fine-step coordinates.
        positions = {a: self.position(a) for a in Axis}
        counts = {a: self.raw.position(a) for a in Axis}
        factor = 256 // profile['resolution']
        for a in Axis:
            self.raw.set_output(a, 2, factor == 4)
            actual = self.raw.outputs(a)
            if bool(actual & 2) != (factor == 4):
                raise RuntimeError('resolution output verification failed')
            self.factor[a] = factor
            self.raw_anchor[a] = counts[a]
            self.position_anchor[a] = positions[a]
            base, speed, accel = self._rates(a, profile)
            # Lower start rate first to avoid transient B >= M on any transition.
            self.raw.set_base_speed(a, 1)
            self.raw.set_max_speed(a, speed)
            self.raw.set_base_speed(a, base)
            self.raw.set_acceleration(a, accel)
            self.raw.set_jog_speed(a, speed)
            if self.raw.profile_rates(a) != (base, speed, accel):
                raise RuntimeError('speed profile verification failed')
            self.rates[a] = (base, speed, accel)
        self.profile = index

    def __getattr__(self, name):
        return getattr(self.raw, name)
