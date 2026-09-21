"""
Common Genesis extension of James Maxwell's BaseDevice.

James Maxwell's epics-device-lib remains an upstream dependency and is not
modified here.

Genesis additions:
- reliable connection cleanup
- reconnect backoff
- standard COMM / ERROR PVs
- safe confirmed-value writeback with process=False
- one common write-permission check

Reconnect/writeback concepts adapted from Farooq's UNH DAQ IOC work.
"""

from __future__ import annotations

import logging
import time

from softioc import alarm, builder

from devices.base_device import BaseDevice


log = logging.getLogger(__name__)

RECONNECT_MIN_S = 2.0
RECONNECT_MAX_S = 60.0


class GenesisBaseDevice(BaseDevice):
    """Common base class for Genesis-specific device IOCs."""

    def __init__(self, device_name, settings):
        # These must exist before BaseDevice initializes the child.
        self._reconnect_fails = 0
        self._reconnect_due = 0.0

        super().__init__(device_name, settings)

        # Standard Genesis communication-health PVs.
        self.pvs["COMM"] = builder.boolIn(
            "COMM",
            ZNAM="Disconnected",
            ONAM="Connected",
        )
        self.pvs["ERROR"] = builder.stringIn("ERROR")

        self._set_comm(False)

    # ------------------------------------------------------------------
    # Connection state
    # ------------------------------------------------------------------

    def _set_comm(self, connected: bool, message: str = ""):
        self.pvs["COMM"].set(1 if connected else 0)
        self.pvs["ERROR"].set(str(message)[:39])

    def _close_connection(self):
        connection = getattr(self, "t", None)

        if connection is not None:
            close = getattr(connection, "close", None)

            if callable(close):
                try:
                    close()
                except Exception:
                    log.exception(
                        "%s: error while closing device connection",
                        self.device_name,
                    )

        self.t = None

    def connect(self):
        """Open the device connection without modifying hardware state."""

        self._close_connection()

        try:
            self.t = self._create_connection()
            self._post_connect()

        except Exception as exc:
            self._close_connection()
            self._set_comm(False, str(exc))

            log.error(
                "%s: connection to %s failed: %s",
                self.device_name,
                self.settings.get("ip", "?"),
                exc,
            )
            return False

        self._reconnect_fails = 0
        self._reconnect_due = 0.0
        self._set_comm(True)

        return True

    def reconnect(self):
        """Reconnect using exponential backoff.

        The IOC remains alive while an instrument is unavailable.
        """

        self._close_connection()

        now = time.monotonic()

        if now < self._reconnect_due:
            return False

        log.warning(
            "%s: attempting reconnect to %s",
            self.device_name,
            self.settings.get("ip", "?"),
        )

        if self.connect():
            return True

        self._reconnect_fails += 1

        wait = min(
            RECONNECT_MIN_S * (2 ** (self._reconnect_fails - 1)),
            RECONNECT_MAX_S,
        )

        self._reconnect_due = time.monotonic() + wait

        log.warning(
            "%s: reconnect failed; next attempt in %.1f s",
            self.device_name,
            wait,
        )

        return False

    # ------------------------------------------------------------------
    # Safe PV synchronization
    # ------------------------------------------------------------------

    def store(self, pv_name, value):
        """Store confirmed hardware state without reprocessing an OUT record."""

        pv_object = self.pvs.get(pv_name)

        if pv_object is None:
            return

        try:
            pv_object.set(value, process=False)

        except TypeError:
            # Input records do not need process=False.
            pv_object.set(value)

    # ------------------------------------------------------------------
    # Write authorization
    # ------------------------------------------------------------------

    def can_write(self, pv_name: str | None = None) -> bool:
        """Common Genesis gate for hardware writes.

        Later we can extend this method with runtime WRITE_ARMED /
        Expert Mode without changing every individual device driver.
        """

        if not bool(self.settings.get("write_enabled", False)):
            self.pvs["ERROR"].set(
                "Read-only mode; command not sent"
            )

            if pv_name in self.pvs:
                self.pvs[pv_name].set_alarm(
                    severity=alarm.MINOR_ALARM,
                    alarm=alarm.WRITE_ALARM,
                )

            return False

        if self.t is None:
            self._set_comm(
                False,
                "Disconnected; command not sent",
            )

            if pv_name in self.pvs:
                self.pvs[pv_name].set_alarm(
                    severity=alarm.MAJOR_ALARM,
                    alarm=alarm.WRITE_ALARM,
                )

            return False

        return True