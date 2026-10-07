import logging
from softioc import builder
from genesis_common.base_device import GenesisBaseDevice
import aioca


class Device(GenesisBaseDevice):
    """General-purpose Feed-Forward IOC

    Computes a feed-forward signal from one or more source PVs and publishes it
    as ``{channel}_CV``. Point a ``pid.py`` controller's ``feedforward_pv`` at
    that PV and its contribution is summed into the control output (and absorbed
    bumplessly while the PID is in Manual).

    Each source is read, passed through a configurable transform, and the
    contributions are summed:

        raw   = sum( transform_i(source_i) )
        CV    = clamp( Scale * raw + Bias , min_output, max_output )

    ``Scale``, ``Bias`` and ``Enable`` are live-tunable PVs. When ``Enable`` is
    Off the output is forced to 0 (pure feedback). If a source PV cannot be
    read, its last good contribution is held and an alarm is raised on that
    source's input PV, so a single dropout does not zero the feed-forward.

    Transforms (per source, ``type`` key):
        linear:  y = gain * x + offset            (gain default 1, offset 0)
        poly:    y = c0 + c1*x + c2*x^2 + ...      (coeffs, ascending order)
        lookup:  piecewise-linear over (x, y) table breakpoints, clamped at ends

    Config (settings.yaml):
        output_pv:   optional PV to also caput the result to (standalone use);
                     not needed when a pid.py reads {channel}_CV directly.
        egu:         engineering units for the output PV (default '')
        prec:        display precision for the output PV (default 3)
        sources:     list of source definitions, e.g.
            - pv: 'TGT:MEOP:PID_Temp_SP'
              type: 'poly'
              coeffs: [0.0, 0.5, 0.01]
              desc: 'setpoint feed-forward'
            - pv: 'TGT:MEOP:OVC_TI'
              type: 'lookup'
              table: [[4.0, 10.0], [10.0, 25.0], [20.0, 60.0]]
            - pv: 'TGT:MEOP:SomeFlow'
              type: 'linear'
              gain: 2.0
              offset: 1.0
        outs:
            bias:        constant added after scaling (default 0)
            scale:       overall multiplier on the summed sources (default 1)
            min_output:  output clamp low  (default None -> no low clamp)
            max_output:  output clamp high (default None -> no high clamp)
            enable:      start enabled? (default True)
        channels:        single channel name, e.g. [FF_Heater]
    """

    def __init__(self, device_name, settings):
        outs = settings.get('outs', {}) or {}
        self.ff_params = {
            'bias': outs.get('bias', 0.0),
            'scale': outs.get('scale', 1.0),
            'min_output': outs.get('min_output', None),
            'max_output': outs.get('max_output', None),
            'enable': outs.get('enable', True),
        }
        self.egu = settings.get('egu', '')
        self.prec = settings.get('prec', 3)
        self.output_pv = settings.get('output_pv')

        # Build a transform callable for each configured source.
        self.sources = []
        for cfg in settings.get('sources', []) or []:
            self.sources.append({
                'pv': cfg['pv'],
                'desc': cfg.get('desc', cfg['pv']),
                'transform': _build_transform(cfg),
                'last': 0.0,  # last good contribution (held on read failure)
            })

        super().__init__(device_name, settings)

    def _create_pvs(self):
        """Create feed-forward output, tuning, and per-source monitoring PVs"""
        for channel in self._skip_none_channels():
            # Computed feed-forward output -- read by pid.py's feedforward_pv
            self.pvs[channel + "_CV"] = builder.aIn(
                channel + "_CV",
                initial_value=0.0,
                EGU=self.egu,
                PREC=self.prec,
                **self.sevr
            )
            # Live-tunable overall scale and bias
            self.pvs[channel + "_Scale"] = builder.aOut(
                channel + "_Scale",
                initial_value=self.ff_params['scale']
            )
            self.pvs[channel + "_Bias"] = builder.aOut(
                channel + "_Bias",
                initial_value=self.ff_params['bias'],
                EGU=self.egu
            )
            # Enable / disable feed-forward
            self.pvs[channel + "_Enable"] = builder.mbbOut(
                channel + "_Enable",
                ("Off", 'MINOR'),
                ("On", 0),
                initial_value=1 if self.ff_params['enable'] else 0
            )
            # Per-source monitoring: raw input and its contribution
            for i, src in enumerate(self.sources, start=1):
                self.pvs[f"{channel}_In{i}"] = builder.aIn(
                    f"{channel}_In{i}", initial_value=0.0, **self.sevr
                )
                self.pvs[f"{channel}_FF{i}"] = builder.aIn(
                    f"{channel}_FF{i}", initial_value=0.0, EGU=self.egu, PREC=self.prec
                )

    def _create_connection(self):
        """No hardware connection for a compute-only device."""
        return None

    def _clamp(self, value):
        lo = self.ff_params['min_output']
        hi = self.ff_params['max_output']
        if lo is not None and value < lo:
            return lo
        if hi is not None and value > hi:
            return hi
        return value

    async def do_reads(self):
        """Read sources, compute the feed-forward value, and publish it."""
        try:
            for channel in self._skip_none_channels():
                enabled = self.pvs[channel + "_Enable"].get() == 1

                raw = 0.0
                for i, src in enumerate(self.sources, start=1):
                    try:
                        x = await aioca.caget(src['pv'], timeout=2)
                        contribution = src['transform'](x)
                        src['last'] = contribution
                        self.pvs[f"{channel}_In{i}"].set(x)
                        self.remove_alarm(f"{channel}_In{i}")
                    except aioca.CANothing as e:
                        # Hold last good contribution rather than zeroing the FF.
                        logging.error(f"Feed-forward source {src['pv']} read error: {e}")
                        contribution = src['last']
                        self.set_alarm(f"{channel}_In{i}")

                    self.pvs[f"{channel}_FF{i}"].set(contribution)
                    raw += contribution

                if enabled:
                    scale = self.pvs[channel + "_Scale"].get()
                    bias = self.pvs[channel + "_Bias"].get()
                    output = self._clamp(scale * raw + bias)
                else:
                    output = 0.0

                self.pvs[channel + "_CV"].set(output)
                if (
                    self.output_pv
                    and bool(self.settings.get("write_enabled", False))
                ):
                    await aioca.caput(self.output_pv, output, timeout=2)

            self._handle_read_success()
            return True

        except Exception as e:
            logging.error(f"Feed-forward error: {e}")
            self._handle_read_error()
            return False


def _build_transform(cfg):
    """Return a callable x -> y for a source config block."""
    kind = cfg.get('type', 'linear')

    if kind == 'linear':
        gain = cfg.get('gain', 1.0)
        offset = cfg.get('offset', 0.0)
        return lambda x: gain * x + offset

    if kind == 'poly':
        coeffs = list(cfg.get('coeffs', [0.0]))  # ascending: c0 + c1*x + ...
        def poly(x):
            result = 0.0
            for power, c in enumerate(coeffs):
                result += c * (x ** power)
            return result
        return poly

    if kind == 'lookup':
        table = sorted(cfg.get('table', []), key=lambda pt: pt[0])
        if not table:
            return lambda x: 0.0
        xs = [pt[0] for pt in table]
        ys = [pt[1] for pt in table]
        def lookup(x):
            # Clamp beyond the table ends
            if x <= xs[0]:
                return ys[0]
            if x >= xs[-1]:
                return ys[-1]
            # Piecewise-linear interpolation
            for j in range(1, len(xs)):
                if x <= xs[j]:
                    x0, x1 = xs[j - 1], xs[j]
                    y0, y1 = ys[j - 1], ys[j]
                    if x1 == x0:
                        return y1
                    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
            return ys[-1]
        return lookup

    raise ValueError(f"Unknown feed-forward transform type: {kind!r}")
