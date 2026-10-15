"""Run the REAL Vaunix shim (tools/vaunix_shim/vaunix_shim.py) on a simulated LabBrick.

Only the vendor shared library is replaced: ``SimLMSLibrary`` implements the
``fnLMS_*`` calls the shim binds through ctypes, in the vendor's own units
(frequency in 10 Hz steps, power in quarter-dBm).  Everything above it is
the unchanged production shim -- ``LabBrick`` (unit conversion, limits,
safe-off enforcement), ``Protocol`` (the line protocol the Vaunix IOC
speaks), ``ControlLease`` (exclusive ownership, watchdog safe-off) and
``Server`` -- bound to 127.0.0.1 only.

The simulated device reports HARDWARE mode (not vendor test mode), exactly as
every cooldown_sim instrument emulator presents itself as the instrument: the
physical supervisor rightly refuses RF ON for a test-mode source, and a
rehearsal must be able to pump.  Its model name is ``LMS-SIM-COOLDOWN`` so no
log can mistake it for hardware, and it only ever runs loopback-isolated
behind cooldown_sim's guard.

    python -m cooldown_sim.emulators.vaunix_brick --port 16030
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import threading

from .. import REPO_ROOT
from ..ports import LOOPBACK

SHIM_PATH = REPO_ROOT / "tools" / "vaunix_shim" / "vaunix_shim.py"
MODEL = b"LMS-SIM-COOLDOWN"
SERIAL = 990001
FREQ_UNIT_HZ = 10
POWER_UNIT_DBM = 0.25


def load_shim():
    spec = importlib.util.spec_from_file_location("vaunix_shim_real", SHIM_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SimLMSLibrary:
    """Python stand-in for liblmshid.so (the fnLMS_* API the shim binds)."""

    def __init__(self, min_hz=10.0e9, max_hz=12.0e9, min_dbm=-40.0, max_dbm=10.0):
        self._lock = threading.Lock()
        self.min_raw, self.max_raw = int(min_hz / FREQ_UNIT_HZ), int(max_hz / FREQ_UNIT_HZ)
        self.min_pwr, self.max_pwr = int(min_dbm / POWER_UNIT_DBM), int(max_dbm / POWER_UNIT_DBM)
        self.freq = int(11.535e9 / FREQ_UNIT_HZ)          # 138.42 GHz after the x12 multiplier
        self.power = int(10.0 / POWER_UNIT_DBM)
        self.rf = 0
        self.internal_ref = 1
        self.sweep = {"start": int(11.5e9 / FREQ_UNIT_HZ), "end": int(11.8e9 / FREQ_UNIT_HZ),
                      "step": int(1e6 / FREQ_UNIT_HZ), "time": 1000, "repeat": 0, "bidir": 0,
                      "ext": 0, "up": 1, "active": 0}
        self.calls: list[str] = []

    # identity / enumeration
    def initialize(self): pass
    def set_test_mode(self, enabled): self.test_mode = bool(enabled)
    def get_version(self): return 0x010C
    def get_num_devices(self): return 1

    def get_dev_info(self, ids):
        ids[0] = 1
        return 1

    def get_model_name(self, device, buffer):
        buffer.value = MODEL
        return len(MODEL)

    def get_serial(self, device): return SERIAL
    def open_device(self, device): return 0
    def close_device(self, device): return 0

    # frequency / power / RF
    def get_frequency(self, d): return self.freq
    def get_min_frequency(self, d): return self.min_raw
    def get_max_frequency(self, d): return self.max_raw

    def set_frequency(self, d, raw):
        with self._lock:
            self.calls.append(f"freq {raw}")
            self.freq = int(raw)
        return 0

    def get_power(self, d): return self.power
    def get_min_power(self, d): return self.min_pwr
    def get_max_power(self, d): return self.max_pwr

    def set_power(self, d, quarter_dbm):
        with self._lock:
            self.calls.append(f"power {quarter_dbm}")
            self.power = int(quarter_dbm)
        return 0

    def get_rf(self, d): return self.rf

    def set_rf(self, d, on):
        with self._lock:
            self.calls.append(f"rf {int(bool(on))}")
            self.rf = int(bool(on))
        return 0

    def get_reference(self, d): return self.internal_ref

    def set_reference(self, d, internal):
        self.internal_ref = int(bool(internal))
        return 0

    def get_pll_lock(self, d): return 1
    def save_settings(self, d): return 0

    # sweep
    def get_sweep_start(self, d): return self.sweep["start"]
    def set_sweep_start(self, d, v): self.sweep["start"] = int(v); return 0
    def get_sweep_end(self, d): return self.sweep["end"]
    def set_sweep_end(self, d, v): self.sweep["end"] = int(v); return 0
    def get_sweep_step(self, d): return self.sweep["step"]
    def set_sweep_step(self, d, v): self.sweep["step"] = int(v); return 0
    def get_sweep_time(self, d): return self.sweep["time"]
    def set_sweep_time(self, d, v): self.sweep["time"] = int(v); return 0
    def get_sweep_repeat(self, d): return self.sweep["repeat"]
    def set_sweep_repeat(self, d, v): self.sweep["repeat"] = int(bool(v)); return 0
    def get_sweep_bidirectional(self, d): return self.sweep["bidir"]
    def set_sweep_bidirectional(self, d, v): self.sweep["bidir"] = int(bool(v)); return 0
    def get_external_trigger(self, d): return self.sweep["ext"]
    def set_external_trigger(self, d, v): self.sweep["ext"] = int(bool(v)); return 0
    def set_sweep_direction(self, d, v): self.sweep["up"] = int(bool(v)); return 0

    def start_sweep(self, d, run):
        self.sweep["active"] = int(bool(run))
        return 0

    def get_status(self, d):
        return (0x04 if self.sweep["active"] else 0) | (0x08 if self.sweep["up"] else 0)


async def serve(port: int, lease_timeout: float = 6.0, library: SimLMSLibrary | None = None,
                ready: threading.Event | None = None, stop: asyncio.Event | None = None):
    shim = load_shim()
    brick = shim.LabBrick(library or SimLMSLibrary(), test_mode=False,
                          minimum_hz=11.5e9, maximum_hz=142e9 / 12)
    brick.open()
    try:
        server = shim.Server(shim.Protocol(brick), lease_timeout)
        listener = await asyncio.start_server(server.client, LOOPBACK, port)
        watchdog = asyncio.create_task(server.watchdog())
        if ready is not None:
            ready.port = listener.sockets[0].getsockname()[1]
            ready.set()
        try:
            async with listener:
                if stop is None:
                    await listener.serve_forever()
                else:
                    await stop.wait()
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
            async with server.lock:
                await asyncio.to_thread(server.lease.revoke)
    finally:
        brick.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--lease-timeout", type=float, default=6.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(serve(args.port, args.lease_timeout))


if __name__ == "__main__":
    main()
