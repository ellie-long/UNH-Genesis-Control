"""The REAL Vaunix shim (tools/vaunix_shim) running on cooldown_sim's simulated
vendor library: protocol, control lease and safe-off behave as in production."""

import asyncio
import socket
import threading

import pytest

from cooldown_sim.emulators import vaunix_brick as vb


@pytest.fixture
def shim():
    lib = vb.SimLMSLibrary()
    ready = threading.Event()
    loop = asyncio.new_event_loop()
    holder = {}

    def runner():
        asyncio.set_event_loop(loop)
        holder["stop"] = asyncio.Event()
        loop.run_until_complete(vb.serve(0, lease_timeout=1.0, library=lib, ready=ready,
                                         stop=holder["stop"]))

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    assert ready.wait(10)
    yield ready.port, lib
    loop.call_soon_threadsafe(holder["stop"].set)
    th.join(5)


def client(port):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    f = s.makefile("rwb")

    def ask(cmd):
        f.write((cmd + "\n").encode())
        f.flush()
        return f.readline().decode().strip()
    return s, ask


def test_identity_is_simulated_hardware_mode(shim):
    port, _lib = shim
    s, ask = client(port)
    assert ask("*IDN?") == "VAUNIX,LMS-SIM-COOLDOWN,990001,268,HARDWARE"
    assert ask("LIMITS?") == "11500000000 11833333333 -40.00 10.00"
    s.close()


def test_mutations_need_the_lease_and_release_forces_rf_off(shim):
    port, lib = shim
    s, ask = client(port)
    assert ask("RF 1").startswith("ERR")                     # no lease, no RF
    assert ask("CONTROL ACQUIRE").startswith("OK")
    assert ask("FREQ 11535000000") == "OK 11535000000"
    assert ask("RF 1") == "OK 1" and lib.rf == 1
    assert ask("CONTROL RELEASE").startswith("OK")
    assert lib.rf == 0                                       # production safe-off
    s.close()


def test_lease_expiry_forces_rf_off(shim):
    port, lib = shim
    s, ask = client(port)
    ask("CONTROL ACQUIRE")
    ask("RF 1")
    assert lib.rf == 1
    import time
    time.sleep(2.0)                                          # lease timeout 1 s, no heartbeat
    assert lib.rf == 0
    s.close()


def test_frequency_limits_enforced_by_the_real_shim(shim):
    port, _lib = shim
    s, ask = client(port)
    ask("CONTROL ACQUIRE")
    assert ask("FREQ 12500000000").startswith("ERR")         # above 142 GHz / 12
    s.close()


def test_shim_binds_loopback_only():
    assert vb.LOOPBACK == "127.0.0.1"
