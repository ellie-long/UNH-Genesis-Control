"""Loopback instrument emulators speaking each real wire protocol.

The unchanged Genesis drivers connect to these exactly as they would to a
serial gateway or LXI instrument -- only ``ip``/``port`` in the generated
profile differ -- so every byte of parsing, calibration, status decoding,
alarm and reconnect logic in the live drivers is exercised.

Protocol constants are imported from the drivers themselves wherever they
exist, so a driver protocol change breaks a conformance test instead of
letting the emulator silently drift.
"""

from .base import FAULT_MODES, FaultController, LoopbackServer  # noqa: F401
