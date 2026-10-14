"""Offline simulated cool-downs for Genesis.

``cooldown_sim`` runs the real, unmodified Genesis stack -- device IOCs,
cryo/ssRF/Microwave logic, archiver, online fitter and Phoebus screens --
against emulated instruments whose readings come from a past cool-down
(``data_record_*.csv``) or a synthetic one, with closed-loop target models so
an operator's actions change what the simulated target does.

Nothing in the live Genesis tree imports this package, and this package never
edits a live file: it generates loopback-only copies of the canonical profiles
into a run directory and launches the unchanged ``master_ioc.py`` against them.

Entry point: ``python -m cooldown_sim --help``.  See docs/COOLDOWN_SIM.md.
"""

from pathlib import Path

#: Repository root (the directory holding ``master_ioc.py``).  Every IOC the
#: simulator launches runs with this as its working directory, because several
#: drivers resolve relative paths (calibration CSVs, state tables) against cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Environment variable naming the directory of real past cool-downs
#: (e.g. ``Optimizied-McClellan-Fits/data``).  Real data never lives in git.
DATA_ENV = "GENESIS_REPLAY_DATA"

#: Environment variable that enables the opt-in full-stack integration tests.
INTEGRATION_ENV = "GENESIS_COOLDOWN_SIM"

#: Banner text shown on every sim-owned display and report.
SIMULATION_BANNER = "SIMULATION - NOT LIVE HARDWARE"
