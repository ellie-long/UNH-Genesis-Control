"""History UCA timeline (logbook) and the thermal fit's step selection."""

import datetime as dt
import math

import numpy as np
import pytest

from cooldown_sim.data import records
from cooldown_sim.data.records import SlowTable
from cooldown_sim.data.timeline import Timeline
from cooldown_sim.model import fit_params as fp
from cooldown_sim.model import uca_history as uh

DAY = dt.datetime(2023, 3, 1, tzinfo=records.lab_tz()).timestamp()      # outside every logged cool-down


def at(hhmm, day=DAY):
    h, m = map(int, hhmm.split(":"))
    return day + 3600.0 * h + 60.0 * m


def record(rf_periods, t0="08:00", t1="20:00", extra=None, dt_s=20.0):
    """Synthetic record: RF on in the given (start, end) periods, 140 GHz, -4 dB."""
    t = np.arange(at(t0), at(t1), dt_s)
    rf = np.zeros(len(t))
    for a, b in rf_periods:
        rf[(t >= at(a)) & (t < at(b))] = 1.0
    num = {"mw_rf_status": rf, "mw_power_db": np.full(len(t), -4.0), "mw_freq_ghz": np.full(len(t), 140.0)}
    num.update((extra or (lambda t: {}))(t))
    return Timeline(SlowTable(t=t, numeric=num), max_hold_s=60)


def logs(*entries, source="logbook", span=("07:00", "21:00")):
    es = tuple(uh.Entry(at(t), v, b) for t, v, b in entries)
    return {"test": uh.CooldownLog("test", at(span[0]), at(span[1]), source, es)}


def history(tl, lg):
    h = uh.UcaHistory(tl, lg)
    tl._uca_history = h                       # what for_timeline() would cache
    return h


# ----------------------------------------------------------- timeline lookup
def test_rf_on_starts_at_5_v_until_the_logbook_lowers_it_and_rf_off_resets():
    tl = record([("09:00", "12:00"), ("13:00", "15:00")])
    h = history(tl, logs(("09:30", 0.0, "stated"), ("10:00", 2.5, "stated"), ("11:00", 0.0, "inferred"),
                         ("13:20", 0.0, "stated")))
    assert h.setting(at("08:30")).source == "rf_off" and h.uca_V(at("08:30")) == 5.0
    assert h.setting(at("09:10")) == uh.Setting(5.0, "procedure", at("09:00"))
    assert h.uca_V(at("09:45")) == 0.0 and h.setting(at("09:45")).source == "stated"
    assert h.uca_V(at("10:30")) == 2.5
    assert h.setting(at("11:30")).source == "inferred"
    assert h.setting(at("12:30")).source == "rf_off"
    s = h.setting(at("13:10"))                # RF OFF reset: back at 5 V, not the 11:00 setting
    assert s.uca_V == 5.0 and s.source == "procedure"
    assert h.uca_V(at("13:30")) == 0.0
    # the history drive follows: zero at 5 V with RF on, full drive at 0 V, partial at 2.5 V
    assert fp.hist_drive(tl, at("09:10")) == 0.0
    assert fp.hist_drive(tl, at("09:45")) == pytest.approx(1.0)
    assert fp.hist_drive(tl, at("10:30")) == pytest.approx(fp.uca_fraction(2.5, 140.0))


def test_unlogged_rf_on_period_reaches_full_power_after_the_delay():
    tl = record([("09:00", "10:00"), ("11:00", "12:00")])
    h = history(tl, logs(("09:30", 0.0, "stated")))
    t_full = at("11:00") + uh.UNLOGGED_DELAY_S
    assert h.setting(t_full - 30) == uh.Setting(5.0, "procedure", at("11:00"))
    assert h.setting(t_full + 30) == uh.Setting(uh.UNLOGGED_UCA_V, "fallback", t_full)
    assert fp.hist_drive(tl, t_full + 30) == pytest.approx(1.0)


def test_a_record_outside_every_logged_cooldown_uses_the_fallback():
    tl = record([("09:00", "12:00")])
    h = history(tl, logs(span=("21:00", "22:00")))
    assert h.logs == []
    assert h.uca_V(at("09:02")) == 5.0 and h.uca_V(at("09:10")) == 0.0


def test_an_off_entry_implies_the_power_was_on_before_it():
    tl = record([("09:00", "12:00")])
    h = history(tl, logs(("10:00", 5.0, "inferred"), ("11:00", 0.0, "inferred")))
    assert h.setting(at("09:30")).source == "fallback" and h.uca_V(at("09:30")) == 0.0
    assert h.uca_V(at("10:30")) == 5.0 and h.uca_V(at("11:30")) == 0.0


def test_entry_a_minute_before_rf_on_counts_and_open_start_takes_the_last_entry():
    tl = record([("09:01", "12:00")])
    h = history(tl, logs(("09:00", 0.0, "stated")))           # written the minute RF came on
    assert h.uca_V(at("09:02")) == 0.0
    tl2 = record([("08:00", "12:00")], t0="10:00")             # RF already on when the record starts
    h2 = history(tl2, logs(("09:00", 2.0, "stated")))
    assert h2.runs[0][2] is True and h2.uca_V(at("10:30")) == 2.0


def test_record_era_uses_the_recorded_uca_column():
    def uca(t):
        return {"uca_v": np.where(t < at("10:00"), 5.0, 0.0)}
    tl = record([("09:00", "12:00")], extra=uca)
    h = history(tl, logs(("09:30", 3.0, "stated"), source="record"))
    assert h.record_uca
    s = h.setting(at("09:45"))
    assert (s.uca_V, s.source) == (5.0, "record")             # the column, not the 3 V entry
    assert h.uca_V(at("10:30")) == 0.0
    (c,) = h.changes()
    assert c.basis == "record" and c.exact and c.t == at("10:00") and (c.v_before, c.v_after) == (5.0, 0.0)
    h_log = history(record([("09:00", "12:00")], extra=uca), logs(("09:30", 3.0, "stated")))
    assert h_log.uca_V(at("10:30")) == 3.0                    # logbook era: the column is ignored


def test_logbook_file_is_well_formed_and_matches_the_2024_12_17_calibration():
    lg = uh.load()
    assert {"2024-12-Cooldown", "2025-12-Cooldown", "2026-06-Cooldown", "2025-07-Cooldown"} <= set(lg)
    for c in lg.values():
        assert c.source in ("logbook", "record") and c.start < c.end
        ts = [e.t for e in c.entries]
        assert ts == sorted(ts)
        for e in c.entries:
            assert c.start <= e.t <= c.end, (c.name, records.format_epoch(e.t))
            assert 0.0 <= e.uca_V <= 5.0 and e.basis in uh.BASES
            assert len(e.note) <= 70                          # short neutral notes, no quotes
    assert lg["2025-07-Cooldown"].entries == ()
    # the task's example and the 62 mW Roots-flow calibration: 5 V / 0 V toggles, RF on
    day = dt.datetime(2024, 12, 17, tzinfo=records.lab_tz()).timestamp()
    toggles = [(e.uca_V, records.format_epoch(e.t)[11:16]) for e in lg["2024-12-Cooldown"].entries
               if at("23:30", day) <= e.t <= at("23:59", day)]
    assert toggles == [(5.0, "23:33"), (0.0, "23:38"), (5.0, "23:44"), (0.0, "23:50"), (5.0, "23:53"),
                       (0.0, "23:58")]
    t = np.arange(at("16:00", day), at("23:59", day) + 3000, 20.0)
    tl = Timeline(SlowTable(t=t, numeric={"mw_rf_status": (t >= at("16:34", day)).astype(float),
                                          "mw_power_db": np.full(len(t), -4.0),
                                          "mw_freq_ghz": np.full(len(t), 139.2666)}), max_hold_s=60)
    on, off = fp.hist_drive(tl, at("23:40", day)), fp.hist_drive(tl, at("23:46", day))
    assert off == 0.0 and on == pytest.approx(fp.freq_factor(139.2666)) and on > 0.97


# ------------------------------------------------------------- fit steps
def cup_record(jumps, rf=(("09:00", "15:00"),)):
    """T1 rising 0.15 K (T3 0.05 K) per unit drive at each real switching time."""
    def cup(t):
        level = np.zeros(len(t))
        for tj, dv in jumps:
            level += np.where(t >= tj, dv, 0.0)
        return {"T1": 1.1 + 0.15 * level, "T3": 1.1 + 0.05 * level, "vp_temp": np.full(len(t), 1.05)}
    return record(list(rf), extra=cup)


def test_steps_are_placed_on_the_cup_response_and_sized():
    real = at("10:00") - 150.0                               # logged 2.5 min after the action
    tl = cup_record([(real, 1.0), (at("12:00") + 30.0, -1.0)])
    history(tl, logs(("10:00", 0.0, "stated"), ("12:00", 5.0, "stated")))
    s1, s2 = fp.uca_steps(tl)
    assert abs(s1.t - real) <= 20.0 and abs(s2.t - (at("12:00") + 30.0)) <= 20.0
    ff = fp.freq_factor(140.0)
    assert s1.dd == pytest.approx(ff) and s2.dd == pytest.approx(-ff)
    for s in (s1, s2):
        assert fp.step_size(tl, "T1", s) / s.dd == pytest.approx(0.15 / ff, rel=0.02)
        assert fp.step_size(tl, "T3", s) / s.dd == pytest.approx(0.05 / ff, rel=0.02)
        assert fp.step_size(tl, "vp_temp", s) == pytest.approx(0.0, abs=1e-9)
        assert s.pre[1] < s.t < s.post[0]


def test_step_selection_rules():
    tl = cup_record([(at("09:40"), 1.0), (at("11:00"), -1.0), (at("11:03"), 1.0),
                     (at("12:30"), -0.1), (at("13:30"), -0.9)])
    history(tl, logs(("09:40", 0.0, "inferred"),            # inferred: shapes history, never fitted
                     ("11:00", 5.0, "stated"),              # 3 min from the next change: not isolated
                     ("11:03", 0.0, "stated"),
                     ("12:30", 1.5, "stated"),              # partial step below MIN_DRIVE_STEP
                     ("13:30", 5.0, "stated")))             # isolated full step: the only one fitted
    (s,) = fp.uca_steps(tl)
    assert abs(s.t - at("13:30")) <= 20.0 and s.label.startswith("stated 1.50->5.00")
    assert s.dd == pytest.approx(-fp.uca_fraction(1.5, 140.0) * fp.freq_factor(140.0))


def test_steps_need_a_known_setting_before_them():
    # the first entry is "off": before it the unlogged fallback (full power) is
    # ASSUMED, so that step is not fitted; the procedure's 5 V at RF ON is known
    tl = cup_record([(at("09:05"), 1.0), (at("11:00"), -1.0), (at("13:00"), 1.0)])
    history(tl, logs(("11:00", 5.0, "stated"), ("13:00", 0.0, "stated")))
    (s,) = fp.uca_steps(tl)
    assert abs(s.t - at("13:00")) <= 20.0
    tl2 = cup_record([(at("10:00"), 1.0)])
    history(tl2, logs(("10:00", 0.0, "stated")))
    (s2,) = fp.uca_steps(tl2)                                 # before: 5 V since RF ON (procedure)
    assert s2.dd > 0.9


def test_rf_method_is_kept_for_comparison():
    tl = cup_record([], rf=(("09:00", "15:00"),))
    assert [s.label for s in fp.rf_steps(tl)] == ["RF flip", "RF flip"]
    with pytest.raises(ValueError):
        fp.fit(None, [], method="bogus")


def test_cup_response_follows_the_fitted_cup_thermometer():
    fitted = {"thermal": {"tau_s": 6.0, "tau_n": 8, "channels": {
        "T1": {"gain_K_per_drive": 0.124, "n": 20, "iqr": [0.05, 0.15], "status": "fitted"},
        "T3": {"gain_K_per_drive": 0.05, "n": 18, "iqr": [0.01, 0.12], "status": "fitted"}}}}
    cup = fp.cup_params(fitted)
    assert cup["gain_K_per_W"] == pytest.approx(0.124 / 0.062, abs=0.01)
    assert cup["tau_s"] == fp.CUP_TAU_FLOOR_S and cup["basis"].startswith("PARTLY FITTED")
    fitted["thermal"]["channels"]["T1"]["status"] = "unfitted: inconsistent sign"
    assert fp.cup_params(fitted)["gain_K_per_W"] == pytest.approx(0.05 / 0.062, abs=0.01)
    fitted["thermal"]["channels"]["T3"]["status"] = "unfitted: too few steps"
    assert fp.cup_params(fitted)["basis"].startswith("ASSUMED")
    assert not math.isnan(fp.build_params({**fitted, "provenance": {}})["assumed"]["cup"]["tau_s"])
