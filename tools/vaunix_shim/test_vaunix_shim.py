"""Protocol tests; no fake Python library is used or provided."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

MODULE = Path(__file__).with_name("vaunix_shim.py")
SPEC = importlib.util.spec_from_file_location("vaunix_shim", MODULE)
shim = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shim)


class RecordingBrick:
    test_mode = True
    model, serial, library_version = "LMS-TEST", 12345, 0x010C
    minimum_frequency_hz, maximum_frequency_hz = 200e6, 20e9
    minimum_power_dbm, maximum_power_dbm = -40.0, 10.0

    def __init__(self):
        self.f, self.p, self.r, self.ref = 10e9, -7.5, 0, 1
        self.sweep = [8e9, 12e9, 1e6, 1000, 1, 0, 0, 0, 0]
    def frequency(self): return self.f
    def power(self): return self.p
    def rf(self): return self.r
    def reference(self): return self.ref
    def pll_locked(self): return 1
    def set_frequency(self, value): self.f = value; return value
    def set_power(self, value): self.p = value; return value
    def set_rf(self, value): self.r = int(value); return self.r
    def set_reference(self, value): self.ref = int(value); return self.ref
    def set_test_mode(self, value): self.test_mode = bool(value); return int(value)
    def allowed_frequency_limits(self):
        return 11.5e9, 142e9 / 12
    def sweep_state(self): return tuple(self.sweep)
    def set_sweep_start(self, value): self.sweep[0] = value; return value
    def set_sweep_end(self, value): self.sweep[1] = value; return value
    def set_sweep_step(self, value): self.sweep[2] = value; return value
    def set_sweep_time(self, value): self.sweep[3] = int(value); return int(value)
    def set_sweep_flag(self, name, value):
        index = {"DIRECTION": 4, "REPEAT": 5, "BIDIRECTIONAL": 6,
                 "EXTERNAL_TRIGGER": 7, "RUN": 8}[name]
        self.sweep[index] = int(value)
        return int(value)
    def save(self): pass


class ProtocolTests(unittest.TestCase):
    def setUp(self): self.protocol = shim.Protocol(RecordingBrick())

    def test_identity_reports_mode(self):
        self.assertEqual(self.protocol.dispatch("*IDN?"),
                         "VAUNIX,LMS-TEST,12345,268,TEST")

    def test_read_and_sets_use_wire_units(self):
        self.assertEqual(self.protocol.dispatch("READ?"),
                         "10000000000 -7.50 0 1 1")
        self.assertEqual(self.protocol.dispatch("FREQ 9400000000"), "OK 9400000000")
        self.assertEqual(self.protocol.dispatch("POW -3.25"), "OK -3.25")
        self.assertEqual(self.protocol.dispatch("RF 1"), "OK 1")

    def test_bad_input_returns_one_error_line(self):
        self.assertEqual(self.protocol.dispatch("RF 2"), "ERR RF requires 0 or 1")
        self.assertEqual(self.protocol.dispatch("NOPE"), "ERR unknown command NOPE")

    def test_sweep_readback_and_controls(self):
        self.assertEqual(self.protocol.dispatch("SWEEP?"),
                         "8000000000 12000000000 1000000 1000 1 0 0 0 0")
        self.assertEqual(self.protocol.dispatch("SWEEP_START 9000000000"), "OK 9000000000")
        self.assertEqual(self.protocol.dispatch("SWEEP_REPEAT 1"), "OK 1")
        self.assertEqual(self.protocol.dispatch("SWEEP_RUN 1"), "OK 1")

    def test_runtime_mode_switch(self):
        self.assertEqual(self.protocol.dispatch("TEST_MODE?"), "1")
        self.assertEqual(self.protocol.dispatch("TEST_MODE 0"), "OK 0")
        self.assertEqual(self.protocol.dispatch("TEST_MODE?"), "0")

    def test_fixed_frequency_limits_protocol(self):
        self.assertEqual(self.protocol.dispatch("LIMITS?"),
                         "11500000000 11833333333 -40.00 10.00")
        self.assertEqual(self.protocol.dispatch("OVERRIDE 1"),
                         "ERR unknown command OVERRIDE")


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        state = {"frequency": 1_160_000_000}
        def set_frequency(_device, raw): state["frequency"] = raw; return 0
        self.brick = shim.LabBrick.__new__(shim.LabBrick)
        self.brick.device_id = 1
        self.brick.allowed_min_hz, self.brick.allowed_max_hz = 11.5e9, 142e9 / 12
        self.brick.minimum_frequency_hz, self.brick.maximum_frequency_hz = 8e9, 12e9
        self.brick.api = SimpleNamespace(
            get_frequency=lambda _device: state["frequency"],
            set_frequency=set_frequency,
            get_rf=lambda _device: 0,
            set_rf=lambda _device, value: self.calls.append(("rf", bool(value))) or 0,
            get_status=lambda _device: 0,
            start_sweep=lambda _device, value: self.calls.append(("sweep", bool(value))) or 0,
            get_sweep_start=lambda _device: 1_150_000_000,
            get_sweep_end=lambda _device: 1_183_333_333,
        )

    def test_fixed_limits_reject_outside_and_allow_inside(self):
        with self.assertRaises(shim.VaunixError):
            self.brick.set_frequency(11e9)
        self.assertEqual(self.brick.set_frequency(11.6e9), 11.6e9)


if __name__ == "__main__": unittest.main()
