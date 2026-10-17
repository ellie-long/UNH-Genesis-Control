"""McClellan line-fit polarization as the hindcast's truth."""

import numpy as np
import pytest

from cooldown_sim.data import mcclellan
from cooldown_sim.data.records import parse_timestamp


def write_results(root, name="2024-09-Cooldown"):
    d = root / name / "main"
    d.mkdir(parents=True)
    (d / "main_results.csv").write_text(
        "date,timestamp,tag,spin_up,relaxation,mode,fit_type,P_area,Q_eq,P_fit,Q_fit,r\n"
        "2024-09-18,9/18/2024 10:00:00 AM,equilibrium,,,Real,equilibrium,30.5,7.1,30.2,7.0,\n"
        "2024-09-18,9/18/2024 10:00:20 AM,equilibrium,,,Real,equilibrium,,,31.0,7.2,\n"
        "2024-09-18,9/18/2024 10:00:40 AM,ssRF,,,Real,hole-burnt,12.0,1.0,12.0,1.0,\n"
        "2024-09-18,9/18/2024 10:01:00 AM,uncategorized,,,Phase,unclassified,,,,,\n"
        "2024-09-18,9/18/2024 10:02:00 AM,equilibrium,,,Real,equilibrium,250.0,,,,\n",
        encoding="utf-8")


def test_equilibrium_lines_only_area_then_fit(tmp_path):
    write_results(tmp_path)
    t, p = mcclellan.equilibrium_p("2024-09", tmp_path)
    assert list(p) == [30.5, 31.0]                    # hole-burnt, unclassified and |P|>=100 dropped
    assert t[0] == pytest.approx(parse_timestamp("9/18/2024 10:00:00 AM"))
    assert mcclellan.equilibrium_p("2025-07", tmp_path) is None


def test_on_grid_takes_the_median_within_the_bin():
    t = np.array([0.0, 10.0, 20.0, 200.0])
    p = np.array([10.0, 20.0, 40.0, 5.0])
    g = mcclellan.on_grid(t, p, np.array([10.0, 100.0, 200.0]), 30.0)
    assert g[0] == pytest.approx(20.0) and np.isnan(g[1]) and g[2] == pytest.approx(5.0)


def test_results_root_beside_the_records(tmp_path, monkeypatch):
    monkeypatch.delenv(mcclellan.ENV, raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "results").mkdir()
    assert mcclellan.results_root(tmp_path / "data") == (tmp_path / "results").resolve()
