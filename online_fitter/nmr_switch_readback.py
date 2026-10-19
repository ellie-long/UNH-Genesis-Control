"""Read-only EPICS switch readback adapter for NMR source state.

Reads COMM and MASK_RBV, never PORT_CMD.
No source changes or hardware writes.
"""

import math
import numbers
import time


DEFAULT_PREFIX = "UNH:GEN:SSRF:NMR_SW"


class NMRReadbackPoller:

    def __init__(
        self, state, pv_factory, *,
        prefix=DEFAULT_PREFIX,
        timeout_s=0.4,
        max_cycle_s=2.0,
        clock=time.monotonic,
    ):
        if not callable(pv_factory):
            raise TypeError("PV factory required")

        if (
            type(timeout_s) not in (int, float)
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
            or type(max_cycle_s) not in (int, float)
            or not math.isfinite(max_cycle_s)
            or max_cycle_s <= 0
        ):
            raise ValueError("Invalid readback timeout")

        self.state = state
        self.clock = clock
        self.timeout_s = timeout_s
        self.max_cycle_s = max_cycle_s
        self.last_error = "Not yet polled"

        self.comm = pv_factory(
            prefix + ":COMM", auto_monitor=False
        )
        self.mask = pv_factory(
            prefix + ":MASK_RBV", auto_monitor=False
        )

    def _sample(self):
        values = []

        for pv in (self.comm, self.mask):
            if pv.connected is not True:
                raise RuntimeError("EPICS PV disconnected")

            value = pv.get(
                timeout=self.timeout_s,
                use_monitor=False,
            )

            if (
                not isinstance(value, numbers.Integral)
                or isinstance(value, bool)
            ):
                raise ValueError("Invalid EPICS integer readback")

            values.append(int(value))

        return tuple(values)

    def poll_once(self):
        """Read twice; invalidate state if unavailable or inconsistent."""

        start = self.clock()

        try:
            first = self._sample()
            second = self._sample()
            elapsed = self.clock() - start

            if (
                not math.isfinite(elapsed)
                or elapsed < 0
                or elapsed > self.max_cycle_s
            ):
                raise RuntimeError("Readback cycle exceeded timeout")

            if first != second:
                raise RuntimeError("Inconsistent switch readbacks")

        except Exception as exc:
            self.state.update_route(None, None)
            self.last_error = str(exc)
            return False

        comm, mask = second

        self.state.update_route(comm, mask)

        if comm != 1 or mask not in (0, 3):
            self.last_error = "Invalid communication or switch mask"
            return False

        self.last_error = ""
        return True


def make_pyepics_poller(state, **kwargs):
    """Create a real read-only PyEpics adapter when explicitly invoked."""
    from epics import PV
    return NMRReadbackPoller(state, PV, **kwargs)
