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

import logging
import time

from softioc import alarm, builder

from genesis_common.http_base import (
    HttpConnection,
    HttpDevice,
)


log = logging.getLogger(__name__)


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
    """EPICS-facing Genesis Mini-Circuits RF switch."""

    # Assigned after DeviceConnection is defined.
    connection_class = None

    READBACK_PVS = (
        "MASK_RBV",
        "STATE_RBV",
    )

    def _create_pvs(self):
        self._last_mask = None

        # ----------------------------------------------------------
        # Device identity
        # ----------------------------------------------------------

        self.pvs["MODEL"] = builder.stringIn(
            "MODEL"
        )

        self.pvs["SERIAL"] = builder.stringIn(
            "SERIAL"
        )

        # ----------------------------------------------------------
        # Combined dual-SPDT route
        #
        # PORT_CMD:
        #   0 -> both internal switches to Port 1
        #   1 -> both internal switches to Port 2
        #
        # MASK_RBV is authoritative:
        #   0 -> Port 1 / Port 1
        #   1 -> mixed
        #   2 -> mixed
        #   3 -> Port 2 / Port 2
        # ----------------------------------------------------------

        self.pvs["PORT_CMD"] = builder.boolOut(
            "PORT_CMD",
            ZNAM="Port 1",
            ONAM="Port 2",
            on_update_name=self.do_sets,
        )

        self.pvs["MASK_RBV"] = builder.longIn(
            "MASK_RBV",
            LOPR=0,
            HOPR=3,
        )

        self.pvs["STATE_RBV"] = builder.stringIn(
            "STATE_RBV"
        )

    def _sync_command_from_mask(
        self,
        mask,
    ):
        """Synchronize command widget only for coherent states."""

        mask = int(mask)

        if mask == PORT1_MASK:
            self.store(
                "PORT_CMD",
                0,
            )

        elif mask == PORT2_MASK:
            self.store(
                "PORT_CMD",
                1,
            )

        # Masks 1 and 2 are deliberately not mapped onto PORT_CMD.
        # The command PV cannot represent a mixed physical state.

    def _apply_mask(
        self,
        mask,
    ):
        """Apply confirmed SWPORT state to EPICS readbacks."""

        mask = int(mask)

        self._last_mask = mask

        self.pvs[
            "MASK_RBV"
        ].set(mask)

        self.pvs[
            "STATE_RBV"
        ].set(
            MiniCircuitsSwitchConnection.state_name(
                mask
            )
        )

        self._sync_command_from_mask(
            mask
        )

    def _set_readback_alarm(
        self,
        fault,
    ):
        severity = (
            alarm.MAJOR_ALARM
            if fault
            else alarm.NO_ALARM
        )

        condition = (
            alarm.READ_ALARM
            if fault
            else alarm.NO_ALARM
        )

        for name in self.READBACK_PVS:
            self.pvs[name].set_alarm(
                severity=severity,
                alarm=condition,
            )

    def _set_state_fault(
        self,
        mask,
    ):
        """Expose mixed internal-SPDT states as a safety-relevant fault."""

        mask = int(mask)

        mixed = mask in (
            1,
            2,
        )

        severity = (
            alarm.MAJOR_ALARM
            if mixed
            else alarm.NO_ALARM
        )

        condition = (
            alarm.STATE_ALARM
            if mixed
            else alarm.NO_ALARM
        )

        for name in self.READBACK_PVS:
            self.pvs[name].set_alarm(
                severity=severity,
                alarm=condition,
            )

        if mixed:
            self.pvs["ERROR"].set(
                f"Mixed switch state: mask {mask}"
            )

    def _post_connect(self):
        """Read identity and route without modifying hardware state."""

        if self.t is None:
            return

        model = self.t.model()
        serial = self.t.serial()
        mask = self.t.read_port_mask()

        if not model.strip():
            raise OSError(
                "empty Mini-Circuits model response"
            )

        if not serial.strip():
            raise OSError(
                "empty Mini-Circuits serial response"
            )

        self.pvs["MODEL"].set(
            model[:39]
        )

        self.pvs["SERIAL"].set(
            serial[:39]
        )

        self._apply_mask(
            mask
        )

    def read_all(
        self,
        channel,
        index,
    ):
        """Perform one authoritative SWPORT polling cycle."""

        mask = self.t.read_port_mask()

        return {
            "mask": mask,
        }

    async def do_reads(self):
        """Poll switch while keeping IOC alive on disconnect."""

        if self.t is None:
            if not self.reconnect():
                return False

        if self.t is None:
            return False

        try:
            readings = self.read_all(
                None,
                0,
            )

            mask = int(
                readings["mask"]
            )

            self._apply_mask(
                mask
            )

            self._set_readback_alarm(
                False
            )

            self._set_comm(
                True
            )

            self._set_state_fault(
                mask
            )

            self.pvs[
                "LAST_OK"
            ].set(
                time.time()
            )

            return True

        except Exception as exc:
            message = str(exc)

            log.warning(
                "%s: switch read failed: %s",
                self.device_name,
                message,
            )

            self._set_comm(
                False,
                message,
            )

            self._set_readback_alarm(
                True
            )

            self._close_connection()

            return False

    def do_sets(
        self,
        new_value,
        pv,
    ):
        """Handle verified Port 1 / Port 2 commands."""

        pv_name = pv.removeprefix(
            self.device_name + ":"
        )

        if pv_name != "PORT_CMD":
            return

        if not self.can_write(
            pv_name
        ):
            if self._last_mask is not None:
                self._sync_command_from_mask(
                    self._last_mask
                )

            return

        target_mask = (
            PORT2_MASK
            if bool(new_value)
            else PORT1_MASK
        )

        try:
            confirmed = (
                self.t.set_port_mask(
                    target_mask,
                    verify_timeout_s=float(
                        self.settings.get(
                            "verify_timeout_s",
                            5.0,
                        )
                    ),
                    verify_interval_s=float(
                        self.settings.get(
                            "verify_interval_s",
                            0.01,
                        )
                    ),
                )
            )

            self._apply_mask(
                confirmed
            )

            self._set_readback_alarm(
                False
            )

            self._set_state_fault(
                confirmed
            )

            self.pvs[
                "ERROR"
            ].set("")

            self.pvs[
                "PORT_CMD"
            ].set_alarm(
                severity=alarm.NO_ALARM,
                alarm=alarm.NO_ALARM,
            )

            self.record_event(
                "Switch set: "
                + MiniCircuitsSwitchConnection.state_name(
                    confirmed
                )
            )

        except Exception as exc:
            message = str(exc)

            log.warning(
                "%s: switch command failed: %s",
                self.device_name,
                message,
            )

            self.pvs[
                "ERROR"
            ].set(
                message[:39]
            )

            self.pvs[
                "PORT_CMD"
            ].set_alarm(
                severity=alarm.MAJOR_ALARM,
                alarm=alarm.WRITE_ALARM,
            )

            if self._last_mask is not None:
                self._sync_command_from_mask(
                    self._last_mask
                )


class DeviceConnection(
    MiniCircuitsSwitchConnection
):
    """Genesis connection class for Mini-Circuits switches."""

    pass


Device.connection_class = (
    DeviceConnection
)
