"""Conversions and the channel map.

The LS224 inverse is checked against the *driver's own* resistance_to_kelvin
for every calibrated slot, so the emulator and the unchanged driver agree.
"""

import functools
import math
from pathlib import Path

import numpy as np
import pytest

from cooldown_sim import channel_map, conversions
from cooldown_sim.data import records
from cooldown_sim.data.timeline import Timeline
from cooldown_sim.testing import softioc_stub

ROOT = Path(__file__).resolve().parents[2]
CALS = ["calibration/fridge_thermometry.csv", "calibration/magnet_thermometry.csv"]


def _calibration_entries():
    with softioc_stub.installed():
        from target_ioc.instruments import ls224
        out = []
        for cal in CALS:
            for slot, entry in ls224.load_calibration(ROOT / cal).items():
                out.append((cal, slot, entry, ls224.resistance_to_kelvin))
        return out


@pytest.fixture(scope="module")
def entries():
    return _calibration_entries()


def test_every_calibrated_slot_inverts_through_the_driver_curve(entries):
    assert len(entries) == 18           # F1-F11 + M1-M5 + T1, T3 (Cernox, stick 2)
    for cal, slot, entry, forward in entries:
        inv = conversions.InverseCalibration(functools.partial(forward, entry))
        lo, hi = inv.t_range
        assert lo < 0.5 and hi > 290, (entry[2], inv.t_range)
        for kelvin in np.geomspace(0.5, hi * 0.999, 60):
            ohms, clamped = inv.ohms(kelvin)
            assert not clamped
            wire = float(f"{ohms:.6E}")                 # what the emulator sends
            assert forward(entry, wire) == pytest.approx(kelvin, rel=1e-5), (entry[2], kelvin)


def test_cernox_curves_are_continuous_and_monotonic(entries):
    """Lake Shore's piecewise Chebyshev ranges meet within a few mK and the
    curve falls steadily with R over the whole fitted span."""
    ccx = [e for e in entries if e[2][0] == "CCX"]
    assert sorted(e[2][2] for e in ccx) == ["T1", "T3"]
    for _cal, _slot, entry, forward in ccx:
        curve = entry[1]
        assert len(curve["ranges"]) == 4 and len(curve["bounds"]) == 3
        assert all(zu > zl for zu, zl, _a in curve["ranges"])
        for b in curve["bounds"]:
            assert forward(entry, b * 1.000001) == pytest.approx(forward(entry, b * 0.999999), abs=0.01)
        r = np.geomspace(45.0, 30000.0, 4000)
        t = np.array([forward(entry, x) for x in r])
        assert np.all(np.isfinite(t)) and np.all(np.diff(t) < 0), entry[2]
        assert 0.9 < forward(entry, float(conversions.InverseCalibration(
            functools.partial(forward, entry)).ohms(1.0)[0])) < 1.1


def test_cernox_transposed_range_limits_are_corrected(tmp_path):
    """A row with ZU < ZL in one range (a transcription slip) reads as if the
    pair were the right way round."""
    with softioc_stub.installed():
        from target_ioc.instruments import ls224
    path = ROOT / "calibration/magnet_thermometry.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    head = lines[0].split(",")
    i2 = head.index("ZU2")
    row = next(line for line in lines if line.startswith("T1,")).split(",")
    swapped = list(row)
    swapped[i2], swapped[i2 + 1] = row[i2 + 1], row[i2]
    bad = tmp_path / "cal.csv"
    bad.write_text("\n".join([lines[0], ",".join(swapped)]) + "\n", encoding="utf-8")
    good = ls224.load_calibration(path)["C2"]
    fixed = ls224.load_calibration(bad)["C2"]
    for ohms in (2000.0, 1002.0, 700.0, 300.0, 100.0):
        assert ls224.resistance_to_kelvin(fixed, ohms) == pytest.approx(
            ls224.resistance_to_kelvin(good, ohms), rel=1e-12)


def test_inverse_is_on_the_cold_ntc_branch(entries):
    for _cal, _slot, entry, forward in entries:
        inv = conversions.InverseCalibration(functools.partial(forward, entry))
        assert inv.ohms(1.0)[0] > inv.ohms(4.0)[0] > inv.ohms(100.0)[0], entry[2]


def test_out_of_branch_temperature_is_clamped_and_flagged(entries):
    _cal, _slot, entry, forward = next(e for e in entries if e[2][2] == "M4")   # max ~294 K
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
    import yaml
    cmap = channel_map.load()
    for inst in ("ls224_fridge", "ls224_magnet", "mks902b", "tpg262", "ionivac", "ls455",
                 "siglent_coldboard"):
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
