"""Offline helium-conversion and manometer-parser regression tests.

These tests do not import EPICS drivers or connect to laboratory hardware.
"""

import ast
import math
import re
from pathlib import Path

import pytest

from genesis_common import its90_helium as he


ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "calibration/vapor_pressure_he4.csv"
DRIVER = ROOT / "target_ioc/instruments/manometer_low.py"


def test_helium_calibrated_bands():
    original = list(he.BANDS)

    try:
        bands = he.load_bands(str(CSV))
        assert len(bands) >= 2

        he.use_bands(bands)

        for band in bands:
            low = band["p_min"]
            high = band["p_max"]

            assert math.isfinite(low)
            assert math.isfinite(high)
            assert 0 < low < high

            pressure = math.sqrt(low * high)

            temp_pa, status_pa = he.pascals_to_kelvin(pressure)
            temp_torr, status_torr = he.torr_to_kelvin(
                pressure / he.TORR_TO_PA
            )

            assert math.isfinite(temp_pa)
            assert math.isfinite(temp_torr)
            assert math.isclose(
                temp_pa, temp_torr,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            assert status_pa == status_torr

    finally:
        he.use_bands(original)


def test_helium_invalid_and_unsupported_pressures():
    original = list(he.BANDS)

    try:
        bands = he.load_bands(str(CSV))
        he.use_bands(bands)

        low = min(b["p_min"] for b in bands)
        high = max(b["p_max"] for b in bands)

        for pressure in (
            0.0,
            -1.0,
            float("nan"),
            low / 10.0,
            high * 10.0,
        ):
            temp, reason = he.pascals_to_kelvin(pressure)
            assert math.isnan(temp), (pressure, temp)
            assert isinstance(reason, str)
            assert reason, pressure

    finally:
        he.use_bands(original)


def get_parser():
    """Extract the real parser without importing the EPICS IOC."""
    tree = ast.parse(DRIVER.read_text())

    connection = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "DeviceConnection"
    )

    method = next(
        node for node in connection.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "read_pressure"
    )

    pattern_assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "FLOAT_RE"
            for target in node.targets
        )
    )

    pattern = ast.literal_eval(
        pattern_assignment.value.args[0]
    )

    namespace = {
        "math": math,
        "FLOAT_RE": re.compile(pattern),
        "TERMINATOR": b"\n",
    }

    module = ast.fix_missing_locations(
        ast.Module(body=[method], type_ignores=[])
    )

    exec(compile(module, str(DRIVER), "exec"), namespace)
    return namespace["read_pressure"]


class FakeConnection:
    host = "simulation"
    substring_start = 4

    def __init__(self, reply):
        self.reply = reply
        self.discards = 0
        self.terminators = []

    def discard_input(self):
        self.discards += 1

    def read_until(self, terminator):
        self.terminators.append(terminator)
        return self.reply


def test_simulated_pressure_responses():
    parse = get_parser()

    examples = (
        (b"HEAD1.250e-3\n", "OK", 0.00125),
        (b"HEAD7.60E+1\n", "OK", 76.0),
        (b"HEADgarbage\n", "PARSE_ERR", None),
        (b"HEADnan\n", "PARSE_ERR", None),
    )

    for reply, expected_status, expected_value in examples:
        connection = FakeConnection(reply)
        pressure, status, raw = parse(connection)

        assert status == expected_status
        assert raw == reply.decode("ascii").strip()
        assert connection.discards == 1
        assert connection.terminators == [b"\n"]

        if expected_value is None:
            assert math.isnan(pressure)
        else:
            assert math.isclose(
                pressure, expected_value,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )


def test_missing_manometer_reply_raises_oserror():
    parse = get_parser()
    connection = FakeConnection(b"")

    try:
        parse(connection)
    except OSError:
        pass
    else:
        raise AssertionError("Missing reply did not raise OSError")

    assert connection.discards == 1
    assert connection.terminators == [b"\n"]


# ---------------------------------------------- below 1.25 K: Donnelly branch
def test_low_branch_is_elena_longs_donnelly_formula():
    """Below ITS-90's 1.25 K floor the module uses Elena Long's branch from
    reliquefierAnalysisDAQ.py: T = 2 L0 / (5 R W(z)) with her x10 on P.  Its
    closed-form inverse is ln(10 P) = i0 + 2.5 ln T - L0 / (R T)."""
    original = list(he.BANDS)
    try:
        he.use_bands(he.load_bands(str(CSV)))
        for kelvin in (0.5, 0.7, 0.9, 1.0, 1.1, 1.2, 1.249):
            pascals = 10 ** -1 * kelvin ** 2.5 * math.exp(
                he.DONNELLY_I0 - he.DONNELLY_L0 / (he.DONNELLY_R * kelvin))
            temp, why = he.pascals_to_kelvin(pascals)
            assert why == "", (kelvin, why)
            assert temp == pytest.approx(kelvin, abs=1e-9)
        # the live reading on 2026-10-24: 0.572 Torr -> 1.1907 K (her script,
        # with 133.3 Pa/Torr instead of 133.322, gives 1.1906 K)
        assert he.torr_to_kelvin(0.572)[0] == pytest.approx(1.19067, abs=1e-5)
    finally:
        he.use_bands(original)


def test_low_branch_joins_its90_at_1p25_K():
    original = list(he.BANDS)
    try:
        he.use_bands(he.load_bands(str(CSV)))
        below = he.pascals_to_kelvin(he.P_MIN_PA * (1 - 1e-9))[0]
        at = he.pascals_to_kelvin(he.P_MIN_PA)[0]
        assert below == pytest.approx(1.25, abs=2e-5)
        assert at == pytest.approx(1.25, abs=2e-5)
        assert abs(below - at) < 2e-5
        # monotonic across the join and through the whole low branch
        ps = [he.P_LOW_PA * (he.P_MIN_PA * 1.5 / he.P_LOW_PA) ** (i / 400) for i in range(401)]
        ts = [he.pascals_to_kelvin(p)[0] for p in ps]
        assert all(b > a for a, b in zip(ts, ts[1:]))
        assert he.phase_of(0.5) == "He-II superfluid"
        assert he.phase_of(1e-6) == "below scale"
        assert he.kelvin_to_torr(0.9) == pytest.approx(
            he._donnelly_pascals(0.9) / he.TORR_TO_PA, rel=1e-6)
        assert math.isnan(he.kelvin_to_torr(0.4))
    finally:
        he.use_bands(original)


def test_lambert_w():
    for z in (1e-6, 0.1, 1.0, math.e, 10.0, 29.3, 1e3, 1e8):
        w = he._lambert_w(z)
        assert w * math.exp(w) == pytest.approx(z, rel=1e-12)
