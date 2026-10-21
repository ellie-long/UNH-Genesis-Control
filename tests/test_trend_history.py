"""logic_devices/trend_history.py: ring buffer, archive back-fill, published arrays.

The archive files are small synthetic CSVs written exactly the way
logic_devices/archiver.py writes them: {safe_pv_name}_{YYYY-MM-DD}.csv with a
'Timestamp,Value' header and naive local timestamps with milliseconds.
"""

import csv
import importlib
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml

from cooldown_sim.testing import softioc_stub

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "configs" / "target_physical" / "trend_history" / "settings.yaml"


@pytest.fixture()
def th():
    with softioc_stub.installed() as stub:
        mod = importlib.import_module("logic_devices.trend_history")
        mod._stub = stub
        yield mod


def write_archive(folder: Path, pv: str, rows):
    """rows: (epoch seconds, value) -- written like the archiver, one file per local day."""
    by_day = {}
    for t, v in rows:
        stamp = datetime.fromtimestamp(t)
        by_day.setdefault(stamp.date(), []).append((stamp, v))
    for day, day_rows in by_day.items():
        path = folder / f"{pv.replace(':', '_').replace('/', '_')}_{day:%Y-%m-%d}.csv"
        new = not path.exists()
        with open(path, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["Timestamp", "Value"])
            for stamp, v in day_rows:
                w.writerow([stamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], v])


#: a fixed local noon, so a 6 h window never straddles midnight unless a test wants it
NOON = datetime(2026, 6, 17, 12, 0, 0).timestamp()


# ------------------------------------------------------------------ ring buffer
def test_ring_buffer_orders_and_wraps(th):
    rb = th.RingBuffer(2, 4)
    assert rb.count == 0 and math.isnan(rb.last_time())
    for i in range(6):
        rb.append(float(i), [i, 10 * i])
    t, v = rb.ordered()
    assert list(t) == [2, 3, 4, 5] and list(v[1]) == [20, 30, 40, 50]
    assert rb.last_time() == 5.0 and rb.count == 4


def test_ring_buffer_drops_samples_after_a_backward_clock_step(th):
    rb = th.RingBuffer(1, 5)
    for i in range(5):
        rb.append(float(i), [i])
    assert rb.drop_after(2.5) == 2
    t, _ = rb.ordered()
    assert list(t) == [0, 1, 2]
    rb.append(2.7, [9])
    assert list(rb.ordered()[0]) == [0, 1, 2, 2.7]


# ---------------------------------------------------------------- archive files
def test_archive_file_name_matches_the_archiver(th, tmp_path):
    import logic_devices.archiver as archiver  # noqa: F401 - same naming rule
    path = th.archive_file(tmp_path, "UNH:GEN:CRYO:MAG:M1_TI", datetime(2026, 6, 17).date())
    assert path.name == "UNH_GEN_CRYO_MAG_M1_TI_2026-06-17.csv"
    assert th.safe_pv_name("A:B/C") == "A_B_C"


def test_read_archive_spans_days_and_survives_bad_rows(th, tmp_path):
    pv = "UNH:GEN:CRYO:FRIDGE:F1_TI"
    midnight = datetime(2026, 6, 18).timestamp()
    write_archive(tmp_path, pv, [(midnight - 60, 4.2), (midnight - 30, "nan"),
                                 (midnight + 30, 4.3), (midnight + 90, "<ca_nothing disconnected>")])
    with open(tmp_path / "UNH_GEN_CRYO_FRIDGE_F1_TI_2026-06-18.csv", "a", newline="") as fh:
        fh.write("garbage\n2026-06-18 00:00:40.000\nnot a time,1.0\n")
    t, v = th.read_archive(tmp_path, pv, midnight - 3600, midnight + 3600)
    assert list(np.round(t - midnight)) == [-60, -30, 30, 90]
    assert v[0] == pytest.approx(4.2) and math.isnan(v[1]) and v[2] == pytest.approx(4.3)
    assert math.isnan(v[3])                                    # the disconnect is a gap
    # outside the requested span
    t, _ = th.read_archive(tmp_path, pv, midnight, midnight + 60)
    assert list(np.round(t - midnight)) == [30]
    # no file at all
    t, v = th.read_archive(tmp_path, "UNH:NOPE", midnight - 60, midnight)
    assert len(t) == 0 and len(v) == 0


def test_hold_at_carries_values_up_to_max_hold(th):
    rows_t = np.array([0.0, 100.0])
    rows_v = np.array([1.0, 2.0])
    grid = np.array([-10.0, 0.0, 50.0, 99.0, 100.0, 130.0, 200.0])
    out = th.hold_at(rows_t, rows_v, grid, max_hold=60)
    assert math.isnan(out[0])                                  # before the first row
    assert out[1] == 1.0 and out[2] == 1.0
    assert math.isnan(out[3])                                  # 99 s after the last row > 60 s
    assert out[4] == 2.0 and out[5] == 2.0 and math.isnan(out[6])


def test_archive_path_comes_from_the_archiver_settings(th, tmp_path):
    from archive.data_paths import legacy_archive_path
    # the live archiver's own resolution: under the Genesis data root, not the checkout
    live = th.archiver_archive_path(ROOT / "configs/target_physical/archiver/settings.yaml")
    assert live == Path(legacy_archive_path("archive/subsystems/pv_csv")) and live.is_absolute()
    # an absolute path (the simulator's run folder) is kept as it is
    cfg = tmp_path / "archiver.yaml"
    cfg.write_text(yaml.safe_dump({"archiver": {"module": "logic_devices.archiver",
                                                "archive_path": str(tmp_path / "pv")}}))
    assert th.archiver_archive_path(cfg) == tmp_path / "pv"
    assert th.archiver_archive_path(tmp_path / "missing.yaml") is None


# -------------------------------------------------------------------- groups
def _group(th, window=600, interval=10, unit="min", n=2):
    sources = [th.Source(f"S{i}", f"UNH:TEST:S{i}") for i in range(n)]
    return th.Group("G", sources, window, interval, unit)


def test_group_caps_points_by_coarsening_the_interval(th):
    g = th.Group("G", [th.Source("A", "X:A")], window=172800, interval=10)
    assert g.buffer.capacity <= th.MAX_POINTS and g.interval == pytest.approx(172800 / th.MAX_POINTS)
    with pytest.raises(ValueError):
        th.Group("G", [th.Source("A", "X:A")], time_unit="fortnight")


def test_arrays_pad_at_the_start_with_nan_and_keep_time_monotonic(th):
    g = _group(th)
    g.update("S0", 4.0)                     # S1 never connected
    for k in range(3):
        g.sample(NOON + 10 * k)
    rel, cols = g.arrays(NOON + 20)
    assert len(rel) == g.buffer.capacity == 60 and rel.dtype == np.float32
    assert np.all(np.isfinite(rel)) and np.all(np.diff(rel) > 0)    # no NaN or turn-back on x
    assert rel[-1] == 0 and rel[-3] == pytest.approx(-20 / 60)
    assert np.all(np.isnan(cols[0][:-3])) and list(cols[0][-3:]) == [4, 4, 4]
    assert np.all(np.isnan(cols[1]))                               # disconnected: nothing drawn


def test_gaps_and_disconnects_break_the_line(th):
    g = _group(th, n=1)
    g.update("S0", 1.0)
    g.sample(NOON)
    g.sample(NOON + 10)
    g.sample(NOON + 100)                    # the IOC was stalled: a gap
    t, v = g.buffer.ordered()
    assert list(t - NOON) == [0, 10, 20, 100] and math.isnan(v[0][2])
    g.update("S0", type("ca_nothing", (), {"ok": False})())        # disconnect
    g.sample(NOON + 110)
    assert math.isnan(g.buffer.ordered()[1][0][-1])


def test_backward_clock_step_forgets_the_future(th):
    g = _group(th, n=1)
    g.update("S0", 1.0)
    for k in range(5):
        g.sample(NOON + 10 * k)
    g.sample(NOON + 15)                     # the clock went back 25 s
    t, _ = g.buffer.ordered()
    assert list(t - NOON) == [0, 10, 15]
    # and the schedule follows the clock back
    g.next_due = NOON + 1000
    assert g.due(NOON + 20) and not g.due(NOON + 21)


def test_scale_converts_at_ingest(th):
    g = th.Group("P", [th.Source("Pump_Torr", "X:Pump_PI", scale=0.750062)], 600, 10)
    g.update("Pump_Torr", 2.0)
    g.sample(NOON)
    assert g.buffer.ordered()[1][0][-1] == pytest.approx(1.500124)


def test_backfill_from_synthetic_archive(th, tmp_path):
    g = th.Group("G", [th.Source("A", "UNH:T:A"), th.Source("B", "UNH:T:B", scale=2.0)],
                 window=600, interval=10)
    now = NOON
    # A archived every 30 s for the last 5 min, B once 400 s ago (older than max_hold)
    write_archive(tmp_path, "UNH:T:A", [(now - 300 + 30 * k, 4.0 + k) for k in range(10)])
    write_archive(tmp_path, "UNH:T:B", [(now - 400, 7.0)])
    loaded = g.backfill(tmp_path, now, max_hold=120)
    t, v = g.buffer.ordered()
    # starts at B's row (the earliest grid time with any data), ends one interval before now
    assert t[-1] == pytest.approx(now - 10) and t[0] == pytest.approx(now - 400)
    assert loaded == len(t) == 40
    a = dict(zip(np.round(t - now), v[0]))
    assert a[-300] == 4.0 and a[-290] == 4.0 and a[-270] == 5.0 and a[-10] == 13.0
    assert math.isnan(a[-310])                                     # before A's first row
    b = dict(zip(np.round(t - now), v[1]))
    assert b[-400] == 14.0 and b[-280] == 14.0 and math.isnan(b[-270])   # held 120 s, scaled
    assert g.latest["A"] == 13.0 and math.isnan(g.latest["B"])     # B is stale
    # the first live sample continues the grid at 'now'
    g.sample(now)
    rel, cols = g.arrays(now)
    assert rel[-1] == 0 and rel[-2] == pytest.approx(-10 / 60) and cols[0][-1] == 13.0


def test_backfill_with_no_archive_leaves_the_buffer_empty(th, tmp_path):
    g = _group(th)
    assert g.backfill(tmp_path / "nothing", NOON, 900) == 0 and g.buffer.count == 0


# -------------------------------------------------------------------- device
def _device(th, tmp_path, now):
    settings = yaml.safe_load(SETTINGS.read_text())["trend_history"]
    settings = dict(settings, backfill=False, archive_path=str(tmp_path))
    dev = th.Device("UNH:GEN:HIST", settings)
    dev.clock = lambda: now
    return dev


def test_device_publishes_every_configured_array(th, tmp_path):
    now = NOON
    write_archive(tmp_path, "UNH:GEN:CRYO:MAG:M1_TI", [(now - 3600 + 30 * k, 4.0) for k in range(120)])
    write_archive(tmp_path, "UNH:GEN:CRYO:VAC:Pump_PI", [(now - 600, 1.0)])
    write_archive(tmp_path, "UNH:GEN:CRYO:VAC:Pump_Unit", [(now - 7200, "mbar")])
    dev = _device(th, tmp_path, now)
    dev.backfill()
    recs = th._stub.builder.records
    settings = yaml.safe_load(SETTINGS.read_text())
    published = th.published_pvs("UNH:GEN:HIST", settings["trend_history"])
    # every published PV is a record of the device (the stub drops the prefix)
    assert {pv.removeprefix("UNH:GEN:HIST:") for pv in published} == set(recs) - {"MAN:trend_history_time"}
    time_ = recs["MAG:TIME"].value
    m1 = recs["MAG:M1_TI"].value
    assert len(time_) == len(m1) <= th.MAX_POINTS and recs["MAG:TIME"].EGU == "min"
    assert np.all(np.isfinite(time_)) and np.all(np.diff(time_) > 0)
    assert np.nanmax(m1) == 4.0 and np.count_nonzero(np.isfinite(m1)) == 300   # 1 h at 12 s
    assert recs["MAG:T_REF"].value == now
    assert recs["PRESS:Pump_Torr"].value[-1] == pytest.approx(0.750062)       # mbar -> Torr
    assert recs["SHELLS:TIME"].EGU == "h" and len(recs["SHELLS:TIME"].value) == 1440
    assert recs["Hist_Backfilled"].value == dev.backfilled > 0
    assert recs["Hist_Sources"].value == 32
    assert {"LEVEL:Magnet_LI", "LEVEL:Fridge_LI", "FLOW:SepFlow_SLM", "FLOW:LPump_SLM",
            "FLOW:RPump_SLM", "PRESS:Mano_PI", "PRESS:Pump_Torr_NOW", "PRESS:OVC_Torr_NOW"} <= set(recs)


# ---------------------------------------------------------------- units
def _unit_group(th):
    src = th.Source("Pump_Torr", "X:Pump_PI", egu="Torr", unit_pv="X:Pump_Unit", to="Torr")
    return th.Group("P", [src], 600, 10)


def test_unit_factor_converts_to_torr(th):
    assert th.unit_factor("mbar", "Torr") == pytest.approx(0.750062)
    assert th.unit_factor(" Torr ", "Torr") == 1.0
    assert th.unit_factor("Pa", "Torr") == pytest.approx(0.00750062)
    assert math.isnan(th.unit_factor("furlong", "Torr")) and math.isnan(th.unit_factor(None, "Torr"))


def test_live_unit_follows_the_unit_pv(th):
    g = _unit_group(th)
    g.update("Pump_Torr", 1000.0)
    assert math.isnan(g.latest["Pump_Torr"])          # unit not known yet: not shown
    g.set_unit("X:Pump_Unit", "mbar")
    assert g.latest["Pump_Torr"] == pytest.approx(750.062)
    g.set_unit("X:Pump_Unit", "Torr")                 # the controller switched to Torr
    g.update("Pump_Torr", 760.0)
    assert g.latest["Pump_Torr"] == 760.0
    g.set_unit("X:Pump_Unit", "Torr")
    g.set_unit("X:Other_Unit", "mbar")                # another gauge's unit: no effect
    assert g.latest["Pump_Torr"] == 760.0


def test_backfill_uses_the_unit_in_force_at_each_sample(th, tmp_path):
    g = _unit_group(th)
    now = NOON
    write_archive(tmp_path, "X:Pump_PI", [(now - 300 + 30 * k, 1000.0) for k in range(10)])
    write_archive(tmp_path, "X:Pump_Unit", [(now - 3000, "mbar"), (now - 150, "Torr")])
    g.backfill(tmp_path, now, max_hold=120)
    t, v = g.buffer.ordered()
    by_t = dict(zip(np.round(t - now), v[0]))
    assert by_t[-200] == pytest.approx(750.062) and by_t[-100] == 1000.0
    assert g.latest["Pump_Torr"] == 1000.0           # seeded in the last archived unit


def test_backfill_without_a_unit_archive_shows_nothing(th, tmp_path):
    g = _unit_group(th)
    write_archive(tmp_path, "X:Pump_PI", [(NOON - 60, 5.0)])
    g.backfill(tmp_path, NOON, max_hold=120)
    assert np.all(np.isnan(g.buffer.ordered()[1]))
    assert math.isnan(g.latest["Pump_Torr"])


def test_device_publishes_the_converted_reading(th, tmp_path):
    import asyncio
    dev = _device(th, tmp_path, NOON)
    dev._started = True
    press = next(g for g in dev.groups if g.name == "PRESS")
    src = next(s for s in press.sources if s.key == "Pump_Torr")
    dev._callback(press, src)(1013.25)
    dev._unit_callback("UNH:GEN:CRYO:VAC:Pump_Unit")("mbar")
    assert asyncio.run(dev.do_reads())
    recs = th._stub.builder.records
    assert recs["PRESS:Pump_Torr_NOW"].value == pytest.approx(760.0, abs=0.01)      # 1 atm
    assert recs["PRESS:Pump_Torr_NOW"].EGU == "Torr"


def test_device_samples_monitor_updates(th, tmp_path):
    import asyncio
    now = [NOON]
    dev = _device(th, tmp_path, NOON)
    dev.clock = lambda: now[0]
    dev._started = True                                   # no Channel Access in a unit test
    cb = dev._callback(dev.groups[0], dev.groups[0].sources[0])
    cb(4.25)
    assert asyncio.run(dev.do_reads())
    recs = th._stub.builder.records
    assert recs["MAG:M1_TI"].value[-1] == pytest.approx(4.25) and recs["Hist_Connected"].value == 1
    now[0] += 5                                           # not due yet: arrays unchanged
    assert asyncio.run(dev.do_reads()) and recs["MAG:T_REF"].value == NOON
    now[0] += 7
    cb(type("ca_nothing", (), {"ok": False})())
    asyncio.run(dev.do_reads())
    assert math.isnan(recs["MAG:M1_TI"].value[-1]) and recs["Hist_Connected"].value == 0
    assert recs["MAG:TIME"].value[-1] == 0 and recs["MAG:TIME"].value[-2] == pytest.approx(-0.2)
    assert recs["Hist_Status"].value == 1
