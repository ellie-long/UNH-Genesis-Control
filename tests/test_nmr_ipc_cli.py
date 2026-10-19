"""Protected NMR IPC CLI validation without runtime startup."""

import ast
from pathlib import Path
import subprocess
import sys

import pytest

MODULES = (
    "online_fitter.run",
    "online_fitter.epics_run",
)


def invoke(module, *args):
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize("module", MODULES)
def test_options_visible(module):
    result = invoke(module, "--help")
    assert result.returncode == 0, result.stderr
    assert "--pynmr-ipc-path" in result.stdout
    assert "--pynmr-ipc-allowed-uid" in result.stdout
    assert "--pynmr-ipc-shared-gid" in result.stdout


@pytest.mark.parametrize("module", MODULES)
def test_unprotected_ipc_rejected(module):
    result = invoke(
        module,
        "--pynmr-ipc-path",
        "/tmp/nmr-ipc-test.sock",
    )
    assert result.returncode != 0
    assert "PyNMR IPC requires protected NMR mode" in result.stderr


@pytest.mark.parametrize("module", MODULES)
def test_relative_socket_rejected(module):
    result = invoke(
        module,
        "--nmr-protected",
        "--nmr-source", "pynmr",
        "--pynmr-ipc-path", "relative.sock",
        "--pynmr-ipc-allowed-uid", "1001",
    )
    assert result.returncode != 0
    assert "must be absolute" in result.stderr


@pytest.mark.parametrize("module", MODULES)
def test_missing_uid_rejected(module):
    result = invoke(
        module,
        "--nmr-protected",
        "--nmr-source", "pynmr",
        "--pynmr-ipc-path", "/tmp/nmr-ipc-test.sock",
    )
    assert result.returncode != 0
    assert "requires explicit allowed UIDs" in result.stderr


def test_runtime_receives_ipc_settings():
    tree = ast.parse(
        Path("online_fitter/run.py").read_text()
    )
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "ProtectedNMRRuntime"
    ]
    assert len(calls) == 1
    names = {k.arg for k in calls[0].keywords}
    assert {
        "pynmr_ipc_path",
        "pynmr_ipc_allowed_uids",
        "pynmr_ipc_shared_gid",
    } <= names
