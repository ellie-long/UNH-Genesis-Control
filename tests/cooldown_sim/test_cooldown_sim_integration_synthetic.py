"""Opt-in full-stack run on the synthetic day: needs NO past data.

    GENESIS_COOLDOWN_SIM=1 python -m pytest -q tests/cooldown_sim -m cooldown_sim
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.cooldown_sim,
    pytest.mark.skipif(os.environ.get("GENESIS_COOLDOWN_SIM") != "1",
                       reason="full-stack simulated cool-down: set GENESIS_COOLDOWN_SIM=1"),
]


def test_synthetic_cooldown_needs_no_data():
    """The model must reproduce the designed record, the microwave chain must
    accept RF ON, and the calibrated fitter must match the model's truth."""
    env = {k: v for k, v in os.environ.items() if k != "GENESIS_REPLAY_DATA"}
    proc = subprocess.run([sys.executable, "-m", "cooldown_sim", "run", "--source", "synthetic",
                           "--start", "2000-01-01 02:00", "--duration", "45", "--speed", "60",
                           "--legs", "cryo,mw,nmr", "--scenario", "synthetic-test"],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    runs = sorted(p for p in (ROOT / "logs" / "cooldown_sim").glob("*synthetic-test*")
                  if (p / "report.json").exists())
    report = json.loads((runs[-1] / "report.json").read_text())
    names = {c["name"]: c for c in report["checks"]}
    assert names["model vs record P"]["verdict"] == "PASS"
    assert names["nmr fitter vs model truth"]["verdict"] == "PASS"
    assert names["microwave RF ON requests"]["verdict"] == "PASS"
