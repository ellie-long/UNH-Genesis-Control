"""Offline fault handling for optional Cryogenics instruments."""

import asyncio
import math
from pathlib import Path
import ast

import pytest

from genesis_common.cryo_readonly_health import CryoReadOnlyHealth


class FakePV:
    def __init__(self, value):
        self.value = value
        self.alarmed = False

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeParent:
    def _skip_none_channels(self, channels=None):
        return ["CH"] if channels is None else channels

    def set_alarm(self, name):
        self.pvs[name].alarmed = True

    def remove_alarm(self, name):
        self.pvs[name].alarmed = False

    def _handle_read_error(self, channels=None):
        for name in self.pvs:
            if name.startswith("CH_"):
                self.set_alarm(name)
        self.reconnects += 1

    def _handle_read_success(self, channels=None):
        for name in self.pvs:
            if name.startswith("CH_"):
                self.remove_alarm(name)

    async def do_reads(self):
        if self.t is None:
            self.reconnects += 1
            return False

        if self.failure:
            self._handle_read_error()
            return False

        for name, value in self.next_values.items():
            self.pvs[name].set(value)

        self._handle_read_success()
        return True


class FakeDevice(CryoReadOnlyHealth, FakeParent):
    CRYO_NUMERIC_SUFFIXES = ("_PI", "_VapT")

    def __init__(self):
        self.pvs = {
            "CH_PI": FakePV(12.0),
            "CH_VapT": FakePV(2.0),
            "CH_Raw": FakePV("sample"),
            "CH_Status": FakePV("OK"),
            "COMM": FakePV(1),
        }
        self.t = object()
        self.failure = False
        self.reconnects = 0
        self.next_values = {}


def run(device):
    return asyncio.run(device.do_reads())


def test_connection_failure_invalidates_only_numeric_pvs():
    device = FakeDevice()
    device.failure = True

    assert run(device) is False
    assert math.isnan(device.pvs["CH_PI"].get())
    assert math.isnan(device.pvs["CH_VapT"].get())
    assert device.pvs["CH_PI"].alarmed
    assert device.pvs["CH_VapT"].alarmed
    assert device.pvs["CH_Raw"].get() == "sample"
    assert device.pvs["CH_Status"].get() == "OK"
    assert device.reconnects == 1


def test_disconnected_branch_invalidates_before_retry():
    device = FakeDevice()
    device.t = None

    assert run(device) is False
    assert math.isnan(device.pvs["CH_PI"].get())
    assert math.isnan(device.pvs["CH_VapT"].get())
    assert device.pvs["CH_PI"].alarmed
    assert device.reconnects == 1


def test_invalid_measurement_keeps_numeric_read_alarm():
    device = FakeDevice()
    device.next_values = {
        "CH_PI": float("nan"),
        "CH_VapT": float("nan"),
        "CH_Status": "PARSE_ERR",
    }

    assert run(device) is True
    assert device.pvs["CH_PI"].alarmed
    assert device.pvs["CH_VapT"].alarmed
    assert device.pvs["CH_Status"].get() == "PARSE_ERR"


def test_finite_recovery_clears_numeric_alarms():
    device = FakeDevice()
    device.failure = True
    assert run(device) is False

    device.failure = False
    device.next_values = {
        "CH_PI": 8.5,
        "CH_VapT": 1.8,
        "CH_Status": "OK",
    }

    assert run(device) is True
    assert device.pvs["CH_PI"].get() == 8.5
    assert device.pvs["CH_VapT"].get() == 1.8
    assert not device.pvs["CH_PI"].alarmed
    assert not device.pvs["CH_VapT"].alarmed


@pytest.mark.parametrize("filename,suffixes", [
    ("flowmeter_pi.py", ("_SLM", "_Raw")),
    ("flowmeter_diewu.py", ("_SLM", "_Raw")),
    ("ami1700.py", ("_LI", "_Raw")),
    ("ami135.py", ("_LI", "_Raw")),
    ("manometer_low.py", ("_PI", "_VapT")),
])
def test_only_expected_driver_integration(filename, suffixes):
    root = Path(__file__).resolve().parents[1]
    path = root / "target_ioc/instruments" / filename
    tree = ast.parse(path.read_text())

    classes = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "Device"
    ]
    assert len(classes) == 1

    cls = classes[0]
    assert [
        ast.unparse(base) for base in cls.bases
    ] == ["CryoReadOnlyHealth", "SerialTcpDevice"]

    values = [
        node for node in cls.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "CRYO_NUMERIC_SUFFIXES"
            for target in node.targets
        )
    ]

    assert len(values) == 1
    assert ast.literal_eval(values[0].value) == suffixes
