"""Genesis master archive (+ pyNMR eventfiles) -> LabVIEW data_record copies.

Synthetic recordings only: a tiny genesis_master.csv in Utsav's column layout
and a pyNMR eventfile with a few sweeps.
"""

import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import pytest

from cooldown_sim.data import genesis_to_labview as g2l
from cooldown_sim.data import records
from online_fitter.vendored import cooldown_io

ERA2026 = Path(__file__).with_name("fixtures") / "eras" / "era2026.csv"
EDT = dt.timezone(dt.timedelta(hours=-4))
P = "UNH:GEN:"


def _master(path: Path, rows):
    """rows: list of (local datetime, {pv: (value, quality)})."""
    pvs = sorted({pv for _, r in rows for pv in r})
    header = ["timestamp_utc", "timestamp_local"]
    for pv in pvs:
        header += [f"{pv}|{s}" for s in ("value", "quality", "age_s", "received_utc", "source_utc")]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for t, r in rows:
            u = t.astimezone(dt.timezone.utc)
            line = [u.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", t.isoformat(timespec="milliseconds")]
            for pv in pvs:
                v, q = r.get(pv, ("", "missing"))
                line += [v, q, "", "", ""]
            w.writerow(line)


def _event(stop: dt.datetime, n=8, label="None", pol=0.01, base=1.0, cc=None):
    freqs = [32.5 + 0.05 * k for k in range(n)]
    return {"stop_time": str(stop.astimezone(dt.timezone.utc)), "stop_stamp": stop.timestamp(),
            "phase": [base + 0.001 * k for k in range(n)], "freq_list": freqs, "label": label,
            "pol": pol, "chassis_temp": 41.5, "diode_vout": 0.25, "phase_vout": 3.0,
            "diode": [0.0] * n, "fitsub": [0.1] * n, "cc": cc}


def _rows(out: Path, day: str):
    text = (out / f"data_record_{day}.csv").read_bytes().decode("latin-1")
    assert "\r\n" in text
    return [line.split("\t") for line in text.split("\r\n") if line]


def _col(title):
    return g2l.HEADER.index(title)


@pytest.fixture
def recording(tmp_path):
    t0 = dt.datetime(2026, 11, 3, 23, 59, 57, tzinfo=EDT)      # crosses lab midnight
    rows = []
    for k in range(6):
        t = t0 + dt.timedelta(seconds=k)
        r = {P + "CRYO:FRIDGE:F1_TI": (f"{1.0 + k:.3f}", "valid"),
             P + "CRYO:MAG:T1_TI": ("2.5", "stale"),
             P + "CRYO:VAC:Pump_PI": ("2.0", "valid"),
             P + "CRYO:VAC:Pump_Unit": ("mbar", "stale"),
             P + "CRYO:CELL:Cell_PI": ("20.0", "valid"),
             P + "CRYO:ION:Ion_PI": ("", "disconnected"),
             P + "SSRF:FIT:CALIBRATED": ("1" if k >= 4 else "0", "valid"),
             P + "SSRF:FIT:P_AREA": ("12.5" if k >= 4 else "", "valid")}
        if k >= 3:                                   # Manometer-Lo in range from row 3
            r[P + "CRYO:VAC:LOW:Mano_PI"] = ("0.5", "valid")
            r[P + "CRYO:VAC:LOW:Mano_VapT"] = ("", "valid")
            r[P + "CRYO:VAC:LOW:Mano_VapPhase"] = ("below scale", "valid")
        rows.append((t, r))
    root = tmp_path / "local_data" / "archive" / "master" / "S1"
    _master(root / "2026-11-04" / "genesis_master.csv", rows)
    (root / "session.json").write_text(json.dumps({"git_sha": "abc123", "git_dirty": False}))
    pynmr = tmp_path / "pynmr_data"
    pynmr.mkdir()
    events = [_event(t0 + dt.timedelta(seconds=0.5), base=1.0),
              _event(t0 + dt.timedelta(seconds=3.2), base=2.0, label="TE"),
              _event(t0 + dt.timedelta(seconds=3.6), base=3.0, label="TE"),   # same row as previous
              _event(t0 - dt.timedelta(hours=2), base=9.0)]                   # long before any row
    events.sort(key=lambda e: e["stop_stamp"])
    (pynmr / "2026-11-03_21-00-00__2026-11-04_00-10-00.txt").write_text(
        "".join(json.dumps(e) + "\n" for e in events))
    return tmp_path, t0


def _tree_digest(path: Path):
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def test_header_is_the_2026_06_layout():
    hdr, delim = records.read_header(ERA2026)
    assert delim == "\t"
    assert list(g2l.HEADER) == hdr[:len(g2l.HEADER)]
    assert len(g2l.HEADER) == 93 and g2l.HEADER[-1] == "NMR"


def test_convert_layout_values_and_sweeps(recording):
    tmp, t0 = recording
    before = _tree_digest(tmp / "local_data") + _tree_digest(tmp / "pynmr_data")
    out = tmp / "converted"
    rep = g2l.convert([tmp / "local_data"], out, [tmp / "pynmr_data"], log=lambda *_: None)
    assert _tree_digest(tmp / "local_data") + _tree_digest(tmp / "pynmr_data") == before

    assert sorted(rep.days) == ["2026-11-03", "2026-11-04"]          # split at lab midnight
    a, b = _rows(out, "2026-11-03"), _rows(out, "2026-11-04")
    assert a[0] == list(g2l.HEADER) and b[0] == list(g2l.HEADER)
    data = a[1:] + b[1:]
    assert len(data) == 6
    assert data[0][0] == "11/3/2026 11:59:57 PM" and data[3][0] == "11/4/2026 12:00:00 AM"
    assert float(data[3][1]) == 0.0

    f1 = [float(r[_col("F1 (K)")]) for r in data]
    assert f1 == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert data[0][_col("T1 (K)")] == "2.5"                           # stale is used
    assert data[0][_col("Ionivac (Torr)")] == "NaN"                   # disconnected
    assert math.isclose(float(data[0][_col("Roots Pressure (Torr)")]), 2.0 * 0.750061683)
    assert data[0][_col("Central Mag Field (T)")] == g2l.NOT_IN_GENESIS
    assert data[0][_col("EPR Function")] == g2l.NOT_IN_GENESIS

    # vapour pressure: Hi until Manometer-Lo reads, then Lo
    assert data[0][_col("Vapor Pressure Range")] == "Hi: He-II superfluid"   # 20 Torr < lambda
    assert 1.9 < float(data[0][_col("Vapor Pressure Temp (K)")]) < 2.0
    assert data[4][_col("Vapor Pressure Range")] == "Lo: below scale"
    assert data[4][_col("Vapor Pressure Temp (K)")] == "NaN"

    # sweeps: row 1 gets the first, row 4 the later of the two that ended in
    # its second; the old sweep is two hours before any row
    nmr = [int(r[_col("NMR Number")]) for r in data]
    assert nmr == [0, 8, 0, 0, 8, 0]
    assert float(data[4][_col("NMR")]) == 3.0
    assert data[4][_col("NMR Status")] == "TE"
    assert rep.counts["sweeps_superseded_same_row"] == 1
    assert rep.counts["sweeps_without_genesis_row"] == 1
    assert data[0][_col("NMR Status")] == "NaN"                       # no sweep yet
    assert data[2][_col("VME Board Temp (C)")] == "41.5"              # held from the last sweep
    # Area/Polarization: NaN before any sweep, then the area sum(V*df) of
    # 0.1 V x 8 bins x 0.05 MHz, then P with a % sign once calibrated
    ap = [r[_col("Area/Polarization")] for r in data]
    assert ap[0] == "NaN"
    assert all(math.isclose(float(v), 0.1 * 8 * 0.05) for v in ap[1:4])
    assert ap[4:] == ["12.5000%", "12.5000%"]
    assert (out / g2l.REPORT_NAME).is_file()


def test_old_nmr_reader_reads_the_sweeps(recording):
    tmp, _ = recording
    out = tmp / "converted"
    g2l.convert([tmp / "local_data"], out, [tmp / "pynmr_data"], log=lambda *_: None)
    cd = cooldown_io.read_cooldown(str(out / "data_record_2026-11-04.csv"), _crop=False)
    assert cd.sweeps.shape == (1, 8)
    axis = cd.freq_axis(0)
    assert math.isclose(axis[0], 32.5) and math.isclose(axis[-1], 32.5 + 0.05 * 7)


def test_without_the_fitter_pynmr_cc_counts_as_calibrated(tmp_path):
    t0 = dt.datetime(2026, 11, 5, 12, 0, 0, tzinfo=EDT)
    rows = [(t0 + dt.timedelta(seconds=k), {P + "CRYO:FRIDGE:F1_TI": ("1.0", "valid")}) for k in range(3)]
    _master(tmp_path / "g" / "S" / "2026-11-05" / "genesis_master.csv", rows)
    (tmp_path / "n").mkdir()
    (tmp_path / "n" / "2026-11-05_15-59-00__2026-11-05_16-01-00.txt").write_text(
        json.dumps(_event(t0 + dt.timedelta(seconds=0.5), pol=0.2345, cc=0.9)) + "\n")
    g2l.convert([tmp_path / "g"], tmp_path / "out", [tmp_path / "n"], log=lambda *_: None)
    data = _rows(tmp_path / "out", "2026-11-05")[1:]
    assert [r[_col("Area/Polarization")] for r in data] == ["NaN", "23.4500%", "23.4500%"]
    cd = cooldown_io.read_cooldown(str(tmp_path / "out" / "data_record_2026-11-05.csv"), _crop=False)
    assert math.isclose(cd.online_p[0], 23.45)


def test_area_is_voltage_times_frequency_step():
    assert math.isclose(g2l.area_v_df([1.0, 2.0, 3.0], [10.0, 10.1, 10.3]),
                        1.0 * 0.1 + 2.0 * 0.15 + 3.0 * 0.2)
    assert math.isnan(g2l.area_v_df([1.0], [1.0]))


def test_strict_drops_stale(recording):
    tmp, _ = recording
    out = tmp / "strict"
    g2l.convert([tmp / "local_data"], out, strict=True, log=lambda *_: None)
    data = _rows(out, "2026-11-03")[1:]
    assert data[0][_col("T1 (K)")] == "NaN"
    assert data[0][_col("Roots Pressure (Torr)")] == "NaN"            # unit is stale -> unknown


def test_never_overwrites_or_writes_into_inputs(recording):
    tmp, _ = recording
    out = tmp / "converted"
    g2l.convert([tmp / "local_data"], out, log=lambda *_: None)
    first = _tree_digest(out)
    with pytest.raises(g2l.Refused):
        g2l.convert([tmp / "local_data"], out, log=lambda *_: None)
    assert _tree_digest(out) == first
    with pytest.raises(g2l.Refused):
        g2l.convert([tmp / "local_data"], tmp / "local_data" / "copy", log=lambda *_: None)
    assert not (tmp / "local_data" / "copy").exists()


def test_report_lists_every_column_source():
    assert all(g2l.source_text(src) for _, src in g2l.COLUMNS)
