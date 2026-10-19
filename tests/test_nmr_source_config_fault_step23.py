"""Fail-closed NMR source-selection configuration regression."""

import ast
import asyncio
from pathlib import Path


SOURCE = Path("logic_devices/ssrf_sequencer.py")


def load_source_runner():
    tree = ast.parse(SOURCE.read_text())

    device = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "Device"
    )

    method = next(
        node for node in device.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_nmr_source_request"
    )

    module = ast.fix_missing_locations(
        ast.Module(body=[method], type_ignores=[])
    )

    # Extracted methods do not inherit their original module globals.
    # Supply asyncio so the real cancellation handler can be evaluated.
    namespace = {"asyncio": asyncio}
    exec(compile(module, str(SOURCE), "exec"), namespace)

    return namespace["_run_nmr_source_request"]


def test_invalid_config_reports_fault_without_dispatch():
    class FakeSequencer:
        def __init__(self):
            self.status = None

        def _validated_nmr_source_configuration(self):
            raise ValueError("Trusted fitter PID missing")

        def _set_nmr_source_status(self, state, reason):
            self.status = (state, reason)

    device = FakeSequencer()

    asyncio.run(load_source_runner()(device, "pynmr"))

    assert device.status is not None
    assert device.status[0] == "FAULT"
    assert "Trusted fitter PID missing" in device.status[1]


def test_configuration_validated_inside_exception_handler():
    tree = ast.parse(SOURCE.read_text())

    device = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "Device"
    )

    runner = next(
        node for node in device.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_nmr_source_request"
    )

    matches = [
        node for node in ast.walk(runner)
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr
                == "_validated_nmr_source_configuration"
            for statement in node.body
            for child in ast.walk(statement)
        )
    ]

    assert len(matches) == 1
    assert any(
        isinstance(handler.type, ast.Name)
        and handler.type.id == "Exception"
        for handler in matches[0].handlers
    )
