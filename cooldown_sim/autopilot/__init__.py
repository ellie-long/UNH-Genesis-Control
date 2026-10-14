"""Autopilot: replays an operator's actions through operator command PVs only.

Every write goes through :class:`cooldown_sim.autopilot.ca.RestrictedCA`,
which refuses any PV not in ``operator_pvs.yaml`` -- the autopilot can do
nothing a human at the Phoebus screens could not, and never writes a
device-level PV.
"""
