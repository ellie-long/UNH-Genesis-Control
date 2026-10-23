"""A value-holding stand-in for ``softioc`` so real drivers run in unit tests.

The driver-conformance tests construct the *real* Genesis driver
(``target_ioc.instruments.ls224.Device`` etc.), point it at a cooldown_sim
emulator on an ephemeral loopback port, call ``do_reads()`` and assert the PV
values -- exercising the full wire protocol, calibration and status handling
without starting an IOC or touching the EPICS network.

``installed()`` swaps this stub into ``sys.modules`` and evicts any cached
Genesis driver modules for the duration, then restores everything.  That
matters because ``tests/test_spd1000x.py`` replaces ``sys.modules['softioc']``
globally at import time; tests here must not depend on, or disturb, that.
"""

from __future__ import annotations

import contextlib
import sys
import types


class FakeRecord:
    """Minimal softioc record: holds value, severity, alarm and fields."""

    def __init__(self, kind: str, name: str, initial_value=None, on_update=None,
                 on_update_name=None, **fields):
        self.kind = kind
        self.name = name
        self.value = initial_value if initial_value is not None else (
            "" if kind in ("stringIn", "stringOut") else 0)
        self.on_update = on_update
        self.on_update_name = on_update_name
        self.severity = 0
        self.alarm = 0
        self.history: list = []
        for key, value in fields.items():
            setattr(self, key, value)

    def set(self, value, process: bool = True, severity=None, alarm=None):
        self.value = value
        self.history.append(value)
        if severity is not None:
            self.severity = severity
        if alarm is not None:
            self.alarm = alarm

    def get(self):
        return self.value

    def set_alarm(self, severity=0, alarm=0, timestamp=None):
        self.severity = severity
        self.alarm = alarm

    def __repr__(self) -> str:
        return f"<FakeRecord {self.kind} {self.name}={self.value!r} sev={self.severity}>"


_RECORD_KINDS = ("aIn", "aOut", "boolIn", "boolOut", "longIn", "longOut", "mbbIn",
                 "mbbOut", "stringIn", "stringOut", "WaveformIn", "WaveformOut",
                 "Waveform", "longStringIn", "longStringOut", "int64In", "int64Out", "Action")


def build_stub() -> types.ModuleType:
    softioc = types.ModuleType("softioc")
    builder = types.ModuleType("softioc.builder")
    records: dict[str, FakeRecord] = {}

    def factory(kind):
        def make(name, *args, **kwargs):
            if args:
                kwargs.setdefault("initial_value", args[0])
            rec = FakeRecord(kind, name, **kwargs)
            records[name] = rec
            return rec
        return make

    for kind in _RECORD_KINDS:
        setattr(builder, kind, factory(kind))
    builder.records = records
    builder.SetDeviceName = lambda name: None
    builder.LoadDatabase = lambda: None
    builder.GetRecordNames = lambda: types.SimpleNamespace(prefix=[""])

    alarm = types.ModuleType("softioc.alarm")
    for i, name in enumerate(("NO_ALARM", "MINOR_ALARM", "MAJOR_ALARM", "INVALID_ALARM")):
        setattr(alarm, name, i)
    for i, name in enumerate(("READ_ALARM", "WRITE_ALARM", "HIHI_ALARM", "HIGH_ALARM",
                              "LOLO_ALARM", "LOW_ALARM", "STATE_ALARM", "COS_ALARM",
                              "COMM_ALARM", "TIMEOUT_ALARM", "HWLIMIT_ALARM", "CALC_ALARM",
                              "SCAN_ALARM", "LINK_ALARM", "SOFT_ALARM", "BAD_SUB_ALARM",
                              "UDF_ALARM", "DISABLE_ALARM", "SIMM_ALARM", "READ_ACCESS_ALARM",
                              "WRITE_ACCESS_ALARM"), start=1):
        setattr(alarm, name, i)

    inner = types.ModuleType("softioc.softioc")
    inner.iocInit = lambda *a, **k: None
    inner.interactive_ioc = lambda *a, **k: None
    dispatcher = types.ModuleType("softioc.asyncio_dispatcher")
    dispatcher.AsyncioDispatcher = lambda *a, **k: (lambda fn: None)

    softioc.builder = builder
    softioc.alarm = alarm
    softioc.softioc = inner
    softioc.asyncio_dispatcher = dispatcher
    softioc.__fake__ = True
    return softioc


#: Modules that bind ``softioc`` at import time and must be re-imported
#: against the stub.
_EVICT_PREFIXES = ("genesis_common", "target_ioc", "logic_devices", "microwave_ioc",
                   "ssrf_ioc", "tools.pv_map", "magnet_ioc")


@contextlib.contextmanager
def installed():
    """Install the stub; yields the stub module.  Restores sys.modules after."""
    stub = build_stub()
    saved = {k: v for k, v in sys.modules.items()
             if k == "softioc" or k.startswith("softioc.") or k.startswith(_EVICT_PREFIXES)}
    for key in saved:
        del sys.modules[key]
    sys.modules["softioc"] = stub
    for sub in ("builder", "alarm", "softioc", "asyncio_dispatcher"):
        sys.modules[f"softioc.{sub}"] = getattr(stub, sub)
    try:
        yield stub
    finally:
        for key in [k for k in sys.modules
                    if k == "softioc" or k.startswith("softioc.") or k.startswith(_EVICT_PREFIXES)]:
            del sys.modules[key]
        sys.modules.update(saved)
