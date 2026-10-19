"""Offline helium-conversion and manometer-parser regression tests.

These tests do not import EPICS drivers or connect to laboratory hardware.
"""

import ast
import math
import re
from pathlib import Path

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
