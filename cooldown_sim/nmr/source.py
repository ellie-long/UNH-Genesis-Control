"""Live NMR sweeps from the target model: the simulated stand-in for the LabVIEW DAQ.

Each sweep is what the Q-meter would show at sim time t:

    raw = Q-curve background + slow drift + deuteron line(P, Q, B) + white noise

with the signal model ported from the McClellan test-cooldown generator
(optimized-mcclellan-fits/test_data_generator) so its amplitudes and noise
match real 2026-06 sweeps: P = 50 % peaks at 0.68 V in Operating mode, white
noise 8e-4 V per bin, cubic drift ~4e-4 V.  The line uses the two-temperature
lineshape (deuteron_model.model_double_nonbolt with rN, rP from the model's
separate P and Q), so a tensor polarization off the Boltzmann curve reaches
the fitter.

Baselines behave like the DAQ's: ``baseline_sub = raw - stored baseline``.
The stored baseline is whatever the Q-meter showed when it was taken -- so
an operator who takes a baseline WITHOUT first shifting the line off-window
(Sim Control "Line offset") subtracts the line itself, exactly the real
mistake.  A clean baseline is stored automatically at session start.
"""

from __future__ import annotations

import math
import threading

import numpy as np

from ..model import physics as ph
from ..model.target import NMR_DEFAULT_SPAN_MHZ

# instrument constants (2026-06; see the generator's header)
PRESET = "irradiated d-butanol (Dec 2025, 13th/15th)"
W = 451                        # points per sweep (NMR Number)
DEFAULT_SPAN_MHZ = NMR_DEFAULT_SPAN_MHZ
PEAK_AT_50_V = 0.68            # Operating-mode peak of a P = 50 % line
NOISE_V = 8e-4                 # white noise per bin, Operating mode
DRIFT_V = 4e-4                 # cubic drift coefficient scale
DRIFT_TAU_S = 3600.0           # drift correlation time


def q_curve(x: np.ndarray) -> np.ndarray:
    """Operating-mode Q-curve on x in [-1, 1) (2026-06 shape, from the generator)."""
    return 0.41 * x ** 2 + 0.04 * x - 0.22


class NMRSource:
    def __init__(self, model, timeline, seed: int = 0, preset: str = PRESET):
        import online_fitter.vendored  # noqa: F401  (puts deuteron_model on sys.path)
        import deuteron_model as dm
        self.dm = dm
        self.model = model
        self.tl = timeline
        self.c = dict(dm.MATERIALS[preset])
        self.rng = np.random.default_rng(seed)
        self._lock = threading.Lock()
        self._drift = np.zeros(4)
        self._drift_t = None
        self.baseline: np.ndarray | None = None
        self.baseline_note = ""
        self._seen_requests = 0
        # amplitude calibration: a P = 50 % Boltzmann line peaks at PEAK_AT_50_V
        freqs = 32.283 + DEFAULT_SPAN_MHZ * (np.arange(W) / W - 0.5)
        r50 = ph.asymmetry_ratios(0.5, ph.q_boltzmann(0.5))
        unit = self._line(freqs, 32.283, *r50, 1.0, 0.0)
        self.amp = PEAK_AT_50_V / float(np.max(np.abs(unit)))

    # ------------------------------------------------------------ geometry
    def window(self, t: float) -> tuple[float, float]:
        """Sweep (centre, span) MHz: Sim Control's setting, else the record's."""
        return self.model.nmr_window(t)

    def _line(self, freqs, larmor, rN, rP, amp, phase_rad):
        c = self.c
        return amp * self.dm.model_double_nonbolt(
            freqs, larmor, c["omegQ1"], c["omegQ2"], c["A1"], c["eta1"], c["eta2"],
            rN, rP, c["K"], 1.0, phase_rad)

    def _drift_step(self, t: float) -> np.ndarray:
        """Slow Ornstein-Uhlenbeck drift of the cubic background coefficients."""
        if self._drift_t is None:
            self._drift_t = t
        dt = max(t - self._drift_t, 0.0)
        a = math.exp(-dt / DRIFT_TAU_S)
        self._drift = a * self._drift + math.sqrt(1 - a * a) * DRIFT_V * self.rng.normal(size=4)
        self._drift_t = t
        return self._drift

    # -------------------------------------------------------------- sweeps
    def sweep(self, t: float) -> dict:
        """One sweep at sim time t: dict(centfreq, span, raw, baseline_sub, truth...)."""
        with self._lock:
            s = self.model.advance(t)
            inp = self.model.inputs
            B = self.model.field_T(t)
            cf, span = self.window(t)
            freqs = cf + span * (np.arange(W) / W - 0.5)
            x = np.linspace(-1.0, 1.0, W, endpoint=False)
            d = self._drift_step(t)
            background = q_curve(x) + d[0] + d[1] * x + d[2] * x ** 2 + d[3] * x ** 3
            if B > 0.5:
                rN, rP = ph.asymmetry_ratios(s.P, s.Q)
                larmor = ph.deuteron_larmor_MHz(B) + inp.line_offset_MHz
                phase = math.radians(self.c["phase_deg"] + inp.nmr_phase_offset_deg)
                signal = self._line(freqs, larmor, rN, rP, self.amp, phase)
            else:
                signal = np.zeros(W)
            noise = NOISE_V * max(inp.nmr_noise_x, 0.0) * self.rng.normal(size=W)
            raw = background + signal + noise
            if self.baseline is None:
                self.baseline = background.copy()          # clean start-up baseline
                self.baseline_note = "clean baseline stored at session start"
            if inp.baseline_requests != self._seen_requests:
                self._seen_requests = inp.baseline_requests
                self.baseline = raw.copy()
                on_window = B > 0.5 and abs(inp.line_offset_MHz) < 0.6 * span
                self.baseline_note = ("baseline taken WITH the line on-window (it will be subtracted)"
                                      if on_window else "baseline taken with the line off-window")
            return {"centfreq_mhz": cf, "span_mhz": span, "raw": raw,
                    "baseline_sub": raw - self.baseline, "true_P": 100 * s.P, "true_Q": 100 * s.Q,
                    "B": B, "baseline_note": self.baseline_note}
