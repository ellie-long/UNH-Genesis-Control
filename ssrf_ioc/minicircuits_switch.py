"""
Genesis Mini-Circuits RF switch controller.

Low-level support for the dual-SPDT Mini-Circuits switches used
to route the NMR and ssRF signal paths.

Documented switch protocol:

    GET /MN?       -> model
    GET /SN?       -> serial number
    GET /SWPORT?   -> 2-bit switch-state mask

    GET /SETP=0    -> both internal switches to Port 1
    GET /SETP=3    -> both internal switches to Port 2

Mask interpretation:

    0 -> both internal switches on Port 1
    3 -> both internal switches on Port 2
    1 -> mixed state
    2 -> mixed state

A command is never considered successful from the SETP request
alone. The resulting state must be verified with SWPORT?.
"""

from __future__ import annotations

import time

from genesis_common.http_base import (
    HttpConnection,
    HttpDevice,
)


PORT1_MASK = 0
PORT2_MASK = 3

VALID_READBACK_MASKS = {
    0,
    1,
    2,
    3,
}

VALID_COMMAND_MASKS = {
    PORT1_MASK,
    PORT2_MASK,
}


class MiniCircuitsSwitchConnection(
    HttpConnection
):
    """HTTP protocol for one Mini-Circuits dual-SPDT switch."""

    def model(self) -> str:
        return self.get("/MN?")

    def serial(self) -> str:
        return self.get("/SN?")

    @staticmethod
    def _parse_port_mask(
        raw,
    ) -> int:
        try:
            mask = int(
                str(raw).strip()
            )

        except ValueError as exc:
            raise OSError(
                "invalid SWPORT response: "
                f"{raw!r}"
            ) from exc

        if mask not in VALID_READBACK_MASKS:
            raise OSError(
                "invalid SWPORT mask: "
                f"{mask}"
            )

        return mask

    def read_port_mask(self) -> int:
        """Return the authoritative 2-bit SWPORT state."""

        return self._parse_port_mask(
            self.get("/SWPORT?")
        )

    def set_port_mask(
        self,
        mask,
        *,
        verify_timeout_s=5.0,
        verify_interval_s=0.01,
    ) -> int:
        """Command both SPDTs and verify the resulting SWPORT mask."""

        mask = int(mask)

        if mask not in VALID_COMMAND_MASKS:
            raise ValueError(
                "combined switch command "
                "must be mask 0 (Port 1) "
                "or 3 (Port 2)"
            )

        verify_timeout_s = float(
            verify_timeout_s
        )

        verify_interval_s = float(
            verify_interval_s
        )

        if verify_timeout_s <= 0:
            raise ValueError(
                "verify_timeout_s must be > 0"
            )

        if verify_interval_s < 0:
            raise ValueError(
                "verify_interval_s must be >= 0"
            )

        # The SETP response itself is deliberately not trusted.
        # SWPORT? is the authoritative confirmation.
        self.get(
            f"/SETP={mask}"
        )

        deadline = (
            time.monotonic()
            + verify_timeout_s
        )

        last_mask = None

        while True:
            last_mask = (
                self.read_port_mask()
            )

            if last_mask == mask:
                return last_mask

            if (
                time.monotonic()
                >= deadline
            ):
                raise OSError(
                    "Mini-Circuits switch "
                    "verification failed: "
                    f"commanded mask {mask}, "
                    f"read back {last_mask}"
                )

            if verify_interval_s > 0:
                time.sleep(
                    verify_interval_s
                )

    def set_port1(
        self,
        **kwargs,
    ) -> int:
        """Route both internal switches to Port 1."""

        return self.set_port_mask(
            PORT1_MASK,
            **kwargs,
        )

    def set_port2(
        self,
        **kwargs,
    ) -> int:
        """Route both internal switches to Port 2."""

        return self.set_port_mask(
            PORT2_MASK,
            **kwargs,
        )

    @staticmethod
    def state_name(
        mask,
    ) -> str:
        """Return a human-readable combined-switch state."""

        mask = int(mask)

        if mask == PORT1_MASK:
            return "PORT1"

        if mask == PORT2_MASK:
            return "PORT2"

        if mask in (1, 2):
            return "MIXED"

        return "UNKNOWN"


class Device(HttpDevice):
    """Future EPICS-facing Mini-Circuits switch device."""

    connection_class = None


class DeviceConnection(
    MiniCircuitsSwitchConnection
):
    """Genesis connection class for Mini-Circuits switches."""

    pass


Device.connection_class = (
    DeviceConnection
)
