"""Conversions and the channel map.

The LS224 inverse is checked against the *driver's own* curves for every
calibrated slot, so the emulator and the unchanged driver agree.  Since
Utsav's ccce63e every curve comes from ``calibration/Thermo_Calibrations.ods``:
the classic F1-F11 / M1-M5 rows through ``genesis_common.thermometry_ods`` and
the driver's ``resistance_to_kelvin``, the target-stick Cernoxes (T1 = 2-T1 on
C4, T3 = 2-T3 on C2) through ``genesis_common.cernox_ccx``.
"""

import functools
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from cooldown_sim import channel_map, conversions
from cooldown_sim.data import records
from cooldown_sim.data.timeline import Timeline
from cooldown_sim.testing import softioc_stub

ROOT = Path(__file__).resolve().parents[2]
LS224_IOCS = ("ls224_fridge", "ls224_magnet")


def _settings(ioc):
    return yaml.safe_load((ROOT / "configs/target_physical" / ioc / "settings.yaml").read_text())[ioc]


def _ccx_forward(entry, ohms):
    from genesis_common.cernox_ccx import ccx_kelvin
    return ccx_kelvin(entry[1], ohms)


def _calibration_entries():
    """(ioc, slot, entry, forward) for every configured LS224 channel, loaded
    exactly the way the driver loads them (entry = (type, curve, name))."""
    from genesis_common.cernox_ccx import load_ccx_bands
    from genesis_common.thermometry_ods import load_classic_ods
    with softioc_stub.installed():
        from target_ioc.instruments import ls224
    out = []
    for ioc in LS224_IOCS:
        s = _settings(ioc)
        slots = dict(zip(s["channels"], s["slots"]))
        ccx = s.get("ccx_sensors") or {}
        assert s["calibration"].endswith(".ods"), ioc
        table = load_classic_ods(ROOT / s["calibration"],
                                 {name: slot for name, slot in slots.items() if name not in ccx})
        for slot, entry in table.items():
            out.append((ioc, slot, entry, ls224.resistance_to_kelvin))
        if ccx:
            bands = load_ccx_bands(ROOT / s["ccx_calibration"])
            for name, source in ccx.items():
                out.append((ioc, slots[name], ("CCX", bands[source], name), _ccx_forward))
    return out


@pytest.fixture(scope="module")
def entries():
    return _calibration_entries()


def test_every_calibrated_slot_inverts_through_the_driver_curve(entries):
    assert len(entries) == 18           # F1-F11 + M1-M5 + T1, T3 (Cernox, stick 2)
    names = {e[2][2]: (e[0], e[1]) for e in entries}
    assert names["T1"] == ("ls224_magnet", "C4") and names["T3"] == ("ls224_magnet", "C2")
    for _ioc, _slot, entry, forward in entries:
        inv = conversions.InverseCalibration(functools.partial(forward, entry))
        lo, hi = inv.t_range
        assert lo < 0.5 and hi > 290, (entry[2], inv.t_range)
        for kelvin in np.geomspace(0.5, hi * 0.999, 60):
            ohms, clamped = inv.ohms(kelvin)
            assert not clamped
            wire = float(f"{ohms:.6E}")                 # what the emulator sends
            assert forward(entry, wire) == pytest.approx(kelvin, rel=1e-5), (entry[2], kelvin)


def test_host_inverse_is_the_driver_curve_for_every_slot(entries):
    """cooldown_sim.emulators.host builds the same inverse, keyed by slot."""
    from cooldown_sim.emulators import host
    by_ioc = {ioc: host._ls224_inverse(_settings(ioc)) for ioc in LS224_IOCS}
    for ioc, slot, entry, forward in entries:
        inv = by_ioc[ioc][slot]
        for kelvin in (0.8, 4.2, 77.0, 250.0):
            assert forward(entry, inv.ohms(kelvin)[0]) == pytest.approx(kelvin, rel=1e-6), entry[2]
    assert {ioc: len(v) for ioc, v in by_ioc.items()} == {"ls224_fridge": 11, "ls224_magnet": 7}


def test_cernox_target_stick_live_room_temperature_reading(entries):
    """Utsav's operator check: the live ~40 ohm room-temperature reading on
    C4 is ~297 K through 2-T1 (the earlier dev-sim curve on that input gave
    328 K)."""
    t1 = next(e for e in entries if e[2][2] == "T1")
    assert t1[3](t1[2], 40.049) == pytest.approx(297.0, abs=3.0)


def test_cernox_curves_are_continuous_and_monotonic(entries):
    """The four overlapping Chebyshev bands agree where ccx_kelvin switches
    between them (a few mK) and the curve falls steadily with R over the whole
    fitted span; outside the span there is no number."""
    ccx = [e for e in entries if e[2][0] == "CCX"]
    assert sorted(e[2][2] for e in ccx) == ["T1", "T3"]
    for _ioc, _slot, entry, forward in ccx:
        bands = entry[1]
        assert len(bands) == 4 and all(zl < zu for zl, zu, _a in bands)
        z_lo = min(b[0] for b in bands)
        z_hi = max(b[1] for b in bands)
        z = np.linspace(z_lo + 1e-9, z_hi - 1e-9, 20000)
        t = np.array([forward(entry, 10.0 ** x) for x in z])
        assert np.all(np.isfinite(t)) and np.all(np.diff(t) < 0), entry[2]
        steps = np.abs(np.diff(t)) / t[1:]
        assert steps.max() < 2e-3, (entry[2], steps.max())   # no band-switch jumps
        assert math.isnan(forward(entry, 10.0 ** (z_lo - 0.01)))
        assert math.isnan(forward(entry, 10.0 ** (z_hi + 0.01)))
        assert 0.9 < forward(entry, float(conversions.InverseCalibration(
            functools.partial(forward, entry)).ohms(1.0)[0])) < 1.1


def test_cernox_transposed_or_gapped_bands_fail_closed(tmp_path):
    """A transcription slip in a CCX calibration (ZU < ZL in one band, or a
    gap between bands) is rejected by Utsav's loader -- the driver then
    publishes NaN for T1/T3 ('CCX CALIBRATION MISSING'), never a guessed
    temperature.  (The pre-ccce63e driver silently swapped a transposed pair.)"""
    from genesis_common.cernox_ccx import load_ccx_bands
    src = ROOT / "calibration/ccx_cernox_multiband.csv"
    lines = src.read_text(encoding="utf-8-sig").splitlines()
    head = lines[0].split(",")
    zl, zu = head.index("ZL_log10_ohm"), head.index("ZU_log10_ohm")
    rows = [ln.split(",") for ln in lines[1:] if ln.startswith("2-T1,")]
    assert len(rows) == 4
    good = tmp_path / "good.csv"
    good.write_text("\n".join([lines[0]] + [",".join(r) for r in rows]) + "\n", encoding="utf-8")
    assert len(load_ccx_bands(good)["2-T1"]) == 4
    swapped = [list(r) for r in rows]
    swapped[1][zl], swapped[1][zu] = rows[1][zu], rows[1][zl]
    bad = tmp_path / "transposed.csv"
    bad.write_text("\n".join([lines[0]] + [",".join(r) for r in swapped]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_ccx_bands(bad)
    gapped = [list(r) for r in rows]
    gapped[2][zu] = str(float(rows[1][zl]) - 0.01)        # band 3 no longer reaches band 2
    gap = tmp_path / "gapped.csv"
    gap.write_text("\n".join([lines[0]] + [",".join(r) for r in gapped]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_ccx_bands(gap)


def test_inverse_is_on_the_cold_ntc_branch(entries):
    for _ioc, _slot, entry, forward in entries:
        inv = conversions.InverseCalibration(functools.partial(forward, entry))
        assert inv.ohms(1.0)[0] > inv.ohms(4.0)[0] > inv.ohms(100.0)[0], entry[2]


def test_out_of_branch_temperature_is_clamped_and_flagged(entries):
    _ioc, _slot, entry, forward = next(e for e in entries if e[2][2] == "M4")   # max ~294 K
    inv = conversions.InverseCalibration(functools.partial(forward, entry))
    ohms, clamped = inv.ohms(305.0)
    assert clamped and forward(entry, ohms) == pytest.approx(inv.t_range[1], rel=1e-4)
    assert math.isnan(inv.ohms(float("nan"))[0])


def test_pressure_and_ionivac_conversions():
    assert conversions.torr_to_mbar(760.0) == pytest.approx(1013.25, rel=1e-6)
    assert conversions.mbar_to_torr(conversions.torr_to_mbar(3.3)) == pytest.approx(3.3)
    for p in (1e-9, 3.2e-7, 5e-3, 760.0):
        count = conversions.torr_to_ionivac_count(p)
        back = conversions.ionivac_count_to_torr(count)
        assert abs(back / p - 1) <= conversions.IONIVAC_REL_QUANTUM * 1.0001
    assert conversions.torr_to_ionivac_count(0.0) is None
    assert conversions.torr_to_ionivac_count(float("nan")) is None


def test_channel_map_is_structurally_valid():
    cmap = channel_map.load()
    assert channel_map.validate(cmap) == []
    assert set(cmap.instruments["ls224_fridge"]) == {f"F{i}" for i in range(1, 12)}
    # every guess carries a rationale for the reviewer
    for spec in cmap.all_specs():
        if spec.confidence == "guess":
            assert len(spec.note) > 40, spec.name


def test_channel_map_covers_every_cryo_profile_channel():
    cmap = channel_map.load()
    for inst in ("ls224_fridge", "ls224_magnet", "mks902b", "tpg262", "ionivac", "ionivac_pump",
                 "ls455", "siglent_coldboard"):
        settings = yaml.safe_load((ROOT / "configs/target_physical" / inst / "settings.yaml").read_text())
        chans = [c for c in settings[inst]["channels"] if "None" not in str(c)]
        assert set(chans) == set(cmap.instruments[inst]), inst


def test_equipment_inventory_lists_every_genesis_ioc():
    """Every IOC the live IOC managers launch is in the equipment inventory
    (hardware) or the software-IOC list, so the review doc stays complete."""
    import yaml
    cmap = channel_map.load()
    listed = {e["genesis"] for e in cmap.equipment} | {s["genesis"] for s in cmap.software_iocs}
    for manager in ("configs/target_physical/ioc_manager/settings.yaml",
                    "configs/commissioning/microwave_physical/ioc_manager/settings.yaml",
                    "configs/commissioning/ssrf_sim_active/ioc_manager/settings.yaml"):
        iocs = yaml.safe_load((ROOT / manager).read_text())
        missing = sorted(k for k in iocs if k != "general" and k not in listed)
        assert not missing, f"{manager}: {missing} not in channel_map.yaml equipment/software_iocs"
    # every channel-map instrument is a listed piece of equipment
    hardware = {e["genesis"] for e in cmap.equipment}
    assert set(cmap.instruments) <= hardware
    for e in cmap.equipment:
        if e["confidence"] == "guess":
            assert e.get("note"), e["genesis"]


def test_select_and_validity_rules():
    t = np.array([0.0, 10.0, 20.0, 30.0])
    table = records.SlowTable(
        t=t,
        numeric={"manometer_hi": np.array([9.0, 9.1, np.nan, 9.3]),
                 "manometer_lo": np.array([0.5, 0.6, 0.7, 0.8]),
                 "mw_power_db": np.array([10.0, 1.07e8, -4.0, np.nan])},
        text={"vp_range": np.array(["Hi", "Lo", "Lo", "OVER"], dtype=object)})
    tl = Timeline(table)
    cmap = channel_map.load()
    # the 902B is Manometer-Hi whatever range the LabVIEW display was on (Ells, 2026-10-15)
    cell = cmap.channel("mks902b", "Cell")
    # (one missing row holds the previous reading: the timeline is sample-and-hold)
    assert [cell.recorded(tl, x) for x in (0, 10, 20, 30)] == [9.0, 9.1, 9.1, 9.3]
    # the generic row-by-row select still works
    pick = channel_map._spec("x", "y", {"select": {"by": "vp_range", "Hi": "manometer_hi",
                                                   "Lo": "manometer_lo", "default": "manometer_hi"}},
                             "none", "invalid")
    assert [pick.recorded(tl, x) for x in (0, 10, 20, 30)] == [9.0, 0.6, 0.7, 9.3]
    power = cmap.history["mw_power"]
    assert power.recorded(tl, 0) == 10.0
    assert math.isnan(power.recorded(tl, 10))          # 1e8 garbage -> absent, not clipped
    assert cmap.channel("siglent_coldboard", "Coldboard").absent == "output_off"


def test_report_lists_every_channel_and_flags_guesses():
    cmap = channel_map.load()
    text = channel_map.report_markdown(cmap, {"2026-06-Cooldown": {"F1": 0.64}})
    for spec in cmap.all_specs():
        assert f"`{spec.instrument}.{spec.name}`" in text
    assert "**guess**" in text and "| 2026-06-Cooldown |" in text


def _mapping_tables(text):
    """Everything except the coverage section (that one needs the real data)."""
    import re
    return re.sub(r"## Column coverage.*?(?=^## )", "", text, flags=re.S | re.M).rstrip()


def test_committed_report_is_current():
    """docs/COOLDOWN_SIM_CHANNEL_MAP.md must be regenerated when the YAML changes."""
    doc = (ROOT / "docs" / "COOLDOWN_SIM_CHANNEL_MAP.md").read_text(encoding="utf-8")
    fresh = channel_map.report_markdown(channel_map.load())
    assert _mapping_tables(doc) == _mapping_tables(fresh), \
        "regenerate: python -m cooldown_sim channel-map-report --coverage"
