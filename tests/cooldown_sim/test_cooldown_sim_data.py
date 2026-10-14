"""Data layer: era-aware data_record reading, blast logs, sample-and-hold.

The era fixtures carry the REAL header layouts of four DAQ eras (2020, 2021,
2025 comma-delimited, 2026) with SYNTHETIC values.  Real cool-downs are only
read by the opt-in test at the bottom (GENESIS_REPLAY_DATA).
"""

import math
import os
from pathlib import Path

import numpy as np
import pytest

from cooldown_sim.data import aliases, blast_log, records
from cooldown_sim.data.timeline import Timeline

ERAS = Path(__file__).with_name("fixtures") / "eras"


@pytest.mark.parametrize("name, f1_key", [
    ("era2020.csv", "F1"), ("era2021.csv", "F1"), ("era2025comma.csv", "F1"), ("era2026.csv", "F1")])
def test_every_era_resolves_core_channels(name, f1_key):
    table = records.read_slow(ERAS / name)
    assert len(table) == 3
    np.testing.assert_allclose(table.numeric[f1_key], [1.0, 1.1, 1.2])
    np.testing.assert_allclose(table.numeric["M1"], [4.20, 4.21, 4.22])
    assert np.all(np.diff(table.t) == 20.0)


def test_2020_era_aliases_and_absences():
    table = records.read_slow(ERAS / "era2020.csv")
    assert "manometer" in table.numeric                 # single-range 2020 manometer
    assert "cold_cathode" not in table.numeric           # not logged in 2020
    assert "fringe_g" not in table.numeric
    np.testing.assert_allclose(table.numeric["mw_rf_status"], [1, 0, 0])   # 'Off' -> 0
    np.testing.assert_allclose(table.numeric["online_p"][:2], [12.5, -0.25])
    assert math.isnan(table.numeric["online_p"][2])


def test_modern_era_text_and_sentinels():
    table = records.read_slow(ERAS / "era2026.csv")
    assert list(table.text["nmr_status"]) == ["Polarization", "", "Baseline"]   # '0' placeholder
    assert list(table.text["vp_range"]) == ["Hi", "Lo", "OVER"]
    f = table.numeric["mw_freq_ghz"]
    assert f[0] == pytest.approx(140.1) and math.isnan(f[1])   # -257.67 GHz RF-off sentinel
    assert table.numeric["fringe_g"][0] == pytest.approx(1.25)


def test_comma_delimited_era():
    hdr, delim = records.read_header(ERAS / "era2025comma.csv")
    assert delim == ","
    assert records.read_slow(ERAS / "era2025comma.csv").numeric["roots_pressure"][0] == pytest.approx(7.5)


def test_timestamps_are_lab_local():
    t = records.parse_timestamp("6/17/2026 1:00:00 PM")
    assert records.format_epoch(t) == "2026-06-17 13:00:00"
    # zero-padded and unpadded spellings agree
    assert records.parse_timestamp("06/17/2026 01:00:00 PM") == t
    assert math.isnan(records.parse_timestamp("garbage"))


def test_parse_number_placeholders():
    for token in ("", "NaN", "Off", "OVER", "---", "Null"):
        assert math.isnan(aliases.parse_number(token))
    assert aliases.parse_number("46.23%") == pytest.approx(46.23)
    assert aliases.parse_number(" -0.00 % ") == 0.0


def test_day_files_prefers_fixed_and_orders_by_content(tmp_path):
    src = (ERAS / "era2026.csv").read_text()
    early = src.replace("6/17/2026", "6/16/2026")
    (tmp_path / "data_record_2026-06-17.csv").write_text(src)
    (tmp_path / "data_record_2026-06-16.csv").write_text("garbage header\n")
    (tmp_path / "data_record_2026-06-16-fixed.csv").write_text(early)
    (tmp_path / "data_record_2026-06-15.csv.corrupt").write_text(src)
    files = records.day_files(tmp_path)
    assert [p.name for p in files] == ["data_record_2026-06-16-fixed.csv", "data_record_2026-06-17.csv"]
    table = records.read_cooldown(tmp_path, ["F1"])
    assert len(table) == 6 and np.all(np.diff(table.t) > 0)


def test_timeline_sample_and_hold_never_invents():
    t = np.array([0.0, 10.0, 20.0, 1000.0])
    table = records.SlowTable(t=t, numeric={"F1": np.array([1.0, np.nan, 3.0, 4.0])},
                              text={"nmr_status": np.array(["TE", "", "Polarization", "TE"], dtype=object)})
    tl = Timeline(table, max_hold_s=60)
    assert math.isnan(tl.value_at("F1", -1))          # before the record
    assert tl.value_at("F1", 5) == 1.0
    assert tl.value_at("F1", 15) == 1.0                # NaN row: hold last finite
    assert tl.value_at("F1", 25) == 3.0
    assert math.isnan(tl.value_at("F1", 500))          # gap longer than max_hold
    assert tl.value_at("F1", 1000) == 4.0
    assert math.isnan(tl.value_at("absent", 5))
    assert tl.text_at("nmr_status", 15) == "TE"
    np.testing.assert_allclose(tl.values_at("F1", [5, 25, 500]), [1.0, 3.0, np.nan])
    assert tl.gaps(120) == [(20.0, 1000.0)]
    assert not tl.has("absent") and tl.has("F1")


def test_blast_log_parsing(tmp_path):
    path = tmp_path / "blast_log_2026-06-18.csv"
    path.write_text(
        "Start time\tEnd time\tCentral Freq (MHz)\tFM Deviation (kHz)\tStep Size (dB)\t"
        "Sweep Min (dBm)\tSweep Max (dBm)\tDwell Time (ms)\tStatus\r\n"
        "2026-06-18 15:17:21\t2026-06-18 15:19:58\t33.281254\t\t\t\t\t\tFailed\r\n"
        "2026-06-18 15:21:46\t2026-06-18 15:21:47\t32.281254\t10\t123\t-120\t3\t90\tSuccess\r\n")
    burns = blast_log.read_blast_log(path)
    assert [b.ok for b in burns] == [False, True]
    assert burns[1].duration_s == 1.0 and burns[1].fm_dev_khz == 10
    assert math.isnan(burns[0].dwell_ms)
    assert records.format_epoch(burns[1].start) == "2026-06-18 15:21:46"


# ------------------------------------------------------- opt-in, real data
DATA = os.environ.get("GENESIS_REPLAY_DATA")


@pytest.mark.skipif(not DATA, reason="GENESIS_REPLAY_DATA not set (real past cool-downs)")
def test_real_headers_resolve_core_channels_in_every_cooldown():
    """Headers only -- reads one line per file across every past cool-down."""
    problems = []
    for cd in records.list_cooldowns(Path(DATA)):
        for path in records.day_files(cd):
            hdr, _ = records.read_header(path)
            cols = aliases.resolve(hdr)
            missing = [c for c in ("timestamp", "F1", "M1", "ionivac", "roots_pressure",
                                   "nmr_status", "online_p") if c not in cols]
            if missing:
                problems.append(f"{path.name}: {missing}")
    assert not problems, problems
