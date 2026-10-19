"""Offline T1 C2 calibration acceptance; never connects to hardware."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import yaml

from genesis_common.cernox_ccx import load_ccx_bands, ccx_kelvin

ROOT = Path(__file__).resolve().parents[1]
CONF = ROOT / "configs/target_physical/ls224_magnet/settings.yaml"
CSV = ROOT / "calibration/ccx_cernox_multiband.csv"


def config():
    return yaml.safe_load(CONF.read_text())["ls224_magnet"]


def test_configuration_identity_and_provisional_status():
    d = config()
    assert d["ip"] == "192.168.1.13"
    slots = dict(zip(d["channels"], d["slots"]))
    assert slots["T1"] == "C2" and slots["T3"] == "C4"
    assert d["ccx_sensors"] == {"T1": "1-T1"}
    assert set(d["ccx_required"]) == {"T1", "T3"}
    assert d["ccx_provisional"]["T1"] == "X141509"
    assert d["ccx_kelvin_bounds"]["T1"] == [0.30, 325.0]
    assert d["write_enabled"] is False


def test_certificate_reference_points_and_rejection():
    curves = load_ccx_bands(CSV)
    assert len(curves["1-T1"]) == 4
    # From manufacturer certificate X141509, Report 1078107, p. 3.
    pairs = [(21352.5, .309970), (533.058, 4.20),
             (315.807, 10.1531), (201.25, 24.3227),
             (87.3029, 100.057), (40.1308, 300.047)]
    for r, expected in pairs:
        assert abs(ccx_kelvin(curves["1-T1"], r, kelvin_bounds=(.3, 325.))-expected) < .08
    for r in (-100, 0, float("nan"), 1e12, 1):
        assert math.isnan(ccx_kelvin(curves["1-T1"], r, kelvin_bounds=(.3,325.)))
    assert math.isnan(ccx_kelvin([], 100))


def test_corrupted_or_incomplete_file_rejected(tmp_path):
    invalid = tmp_path/"ccx.csv"
    invalid.write_text("Source_Row,ZL_log10_ohm,ZU_log10_ohm,c0\n1-T1,2,3,4\n")
    try:
        load_ccx_bands(invalid)
        assert False, "Missing coefficients unexpectedly accepted"
    except ValueError:
        pass


def test_read_all_provisional_c2_and_unassigned_c4():
    # Compile production method without importing softioc or opening a TCP socket.
    source = ROOT/"target_ioc/instruments/ls224.py"
    tree = ast.parse(source.read_text())
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Device")
    method = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "read_all")
    script = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {"decode_status": lambda v: "invalid reading" if int(v) else "",
                 "ccx_kelvin": ccx_kelvin, "math": math,
                 "resistance_to_kelvin": lambda *a: float("nan")}
    exec(compile(script, str(source), "exec"), namespace)
    d = config()
    bands = load_ccx_bands(CSV)
    class Transport:
        def sensor_reading(self, slot): return {"C2": 533.058, "C4": 10.0}[slot]
        def reading_status(self, slot): return 0
        def kelvin(self, slot): raise AssertionError("Uncalibrated CCX must never call KRDG?")
    fake = SimpleNamespace(settings=d, t=Transport(), _ccx=bands,
                           _cal={}, _slot_of=lambda ch, index: dict(zip(d["channels"], d["slots"]))[ch])
    t1 = namespace["read_all"](fake, "T1", 6)
    t3 = namespace["read_all"](fake, "T3", 7)
    assert abs(t1["T1_TI"]-4.2) < .01
    assert t1["T1_RI"] == 533.058
    assert t1["T1_Status"] == "OK PROVISIONAL X141509"
    assert math.isnan(t3["T3_TI"])
    assert "UNASSIGNED" in t3["T3_Status"]
    fake._ccx = {}
    t1_bad = namespace["read_all"](fake,"T1",6)
    assert math.isnan(t1_bad["T1_TI"])
    assert "CALIBRATION MISSING" in t1_bad["T1_Status"]
    fake._ccx = bands
    class Flagged(Transport):
        def reading_status(self, slot): return 1
    fake.t = Flagged()
    flagged = namespace["read_all"](fake,"T1",6)
    assert math.isnan(flagged["T1_TI"])
    assert "invalid reading" in flagged["T1_Status"]


def test_gui_channel_readbacks_present():
    for name in ("genesis_cryo.bob", "genesis_cryo_magnet.bob"):
        root = ET.parse(ROOT/"gui"/name).getroot()
        pvs = [w.findtext("pv_name") or "" for w in root.iter("widget")]
        for ch in ("T1", "T3"):
            for suffix in ("TI", "Status"):
                assert any(p.endswith(f"{ch}_{suffix}") for p in pvs)
    assert "T1 provisional / T3 N/A" in (ROOT/"gui/genesis_cryo.bob").read_text()
