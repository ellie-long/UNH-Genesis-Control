"""Opt-in full-stack simulated cool-down.

Launches the real Genesis cryo IOCs against the emulators for a short window
of a past cool-down and requires a PASS report.  Skipped unless
GENESIS_COOLDOWN_SIM=1 and GENESIS_REPLAY_DATA point at past cool-downs:

    GENESIS_COOLDOWN_SIM=1 GENESIS_REPLAY_DATA=.../Optimizied-McClellan-Fits/data \\
        python -m pytest -q tests/cooldown_sim -m cooldown_sim
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
    pytest.mark.skipif(not os.environ.get("GENESIS_REPLAY_DATA"),
                       reason="needs GENESIS_REPLAY_DATA (past cool-downs)"),
]


def _run(*args, timeout=900):
    return subprocess.run([sys.executable, "-m", "cooldown_sim", *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)


def _latest_report():
    runs = sorted(p for p in (ROOT / "logs" / "cooldown_sim").glob("*") if (p / "report.json").exists())
    return json.loads((runs[-1] / "report.json").read_text()), runs[-1]


def test_cryo_regression_on_real_window_passes():
    proc = _run("run", "--cooldown", "2026-06", "--start", "2026-06-17 09:00",
                "--duration", "10", "--speed", "60", "--legs", "cryo")
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    report, run = _latest_report()
    assert report["verdict"] == "PASS"
    names = {c["name"]: c for c in report["checks"]}
    assert names["read-only proof"]["verdict"] == "PASS"
    assert names["processes"]["verdict"] == "PASS"
    assert names["expected PVs never served (_Conn)"]["verdict"] == "KNOWN"
    assert (run / "channel_map_check.html").exists()
    assert any(p.name.startswith("UNH_GEN_CRYO_FRIDGE_F1_TI_") for p in (run / "archive" / "data").iterdir())


def test_guard_refuses_beside_a_live_port():
    import socket
    with socket.socket() as srv:
        try:
            srv.bind(("127.0.0.1", 5116))           # the live LS224 fridge IOC CA port
        except OSError:
            pytest.skip("port 5116 already in use on this host")
        srv.listen()
        proc = _run("guard", timeout=60)
    assert proc.returncode == 2
    assert "5116" in proc.stdout
