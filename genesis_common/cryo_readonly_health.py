"""Readback invalidation for the new optional Cryogenics IOCs only.

No hardware connections or write operations are implemented here.
The existing Genesis communication, alarm and reconnect machinery
remains authoritative.
"""

import math


class CryoReadOnlyHealth:
    CRYO_NUMERIC_SUFFIXES = ()

    def _cryo_numeric_pvs(self, channels=None):
        for channel in self._skip_none_channels(channels):
            for suffix in self.CRYO_NUMERIC_SUFFIXES:
                name = channel + suffix
                if name in self.pvs:
                    yield name

    def _cryo_invalidate(self, channels=None):
        for name in self._cryo_numeric_pvs(channels):
            self.pvs[name].set(float("nan"))

    async def do_reads(self):
        # ScpiDevice's disconnected branch does not invoke its
        # read-error handler. Invalidate and alarm before delegating
        # to the existing reconnect/backoff implementation.
        if self.t is None:
            self._cryo_invalidate()
            for name in self._cryo_numeric_pvs():
                self.set_alarm(name)

        return await super().do_reads()

    def _handle_read_error(self, channels=None):
        # Invalidate before the inherited handler sets READ alarms.
        self._cryo_invalidate(channels)
        return super()._handle_read_error(channels)

    def _handle_read_success(self, channels=None):
        # Successful TCP polling can still return an invalid
        # measurement. Preserve the existing COMM semantics but
        # retain a READ alarm on any nonfinite numerical readback.
        result = super()._handle_read_success(channels)

        for name in self._cryo_numeric_pvs(channels):
            try:
                value = float(self.pvs[name].get())
                valid = math.isfinite(value)
            except (TypeError, ValueError):
                valid = False

            if not valid:
                self.set_alarm(name)

        return result
