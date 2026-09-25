"""Online deuteron-NMR fitter for Cooldown_Experimental_2024.vi.

Receives baseline-subtracted NMR sweeps from LabVIEW over a loopback-only socket,
fits them with the vendored McClellan Dulya spin-1 routines according to an
operator-chosen mode, and shows a live pyqtgraph window (raw line, cubic-subtracted
fit + I+/I- components, and a P/Q-vs-time strip chart).
"""
