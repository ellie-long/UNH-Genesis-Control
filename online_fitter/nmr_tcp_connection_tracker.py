"""TCP connection lifecycle observer for protected NMR streams.

Connection state is only a transport observation.
It does not authenticate the producer, attest acquisition,
arm event admission, or authorize physical switching.
"""

from threading import RLock


class TCPConnectionTracker:
    def __init__(self, source, observer):
        if source not in ("labview", "pynmr"):
            raise ValueError("Invalid NMR source")
        if not callable(observer):
            raise TypeError("Observer must be callable")

        self.source = source
        self.observer = observer
        self._lock = RLock()
        self._active = 0
        self._closed = False

    def connected(self):
        with self._lock:
            if self._closed:
                return False

            if self._active == 0:
                try:
                    self.observer(self.source, True)
                except Exception:
                    return False

            self._active += 1
            return True

    def disconnected(self):
        with self._lock:
            if self._active == 0:
                return

            self._active -= 1

            if self._active == 0 and not self._closed:
                self.observer(self.source, False)

    def close(self):
        with self._lock:
            if self._closed:
                return

            self._closed = True
            self._active = 0
            self.observer(self.source, False)

    def wrap(self, base_handler):
        tracker = self

        class TrackedHandler(base_handler):
            def setup(self):
                super().setup()

                self._genesis_registered = (
                    tracker.connected()
                )

                if not self._genesis_registered:
                    super().finish()
                    raise ConnectionAbortedError(
                        "NMR connection state unavailable"
                    )

            def finish(self):
                try:
                    return super().finish()
                finally:
                    if getattr(
                        self, "_genesis_registered", False
                    ):
                        self._genesis_registered = False
                        tracker.disconnected()

        return TrackedHandler
