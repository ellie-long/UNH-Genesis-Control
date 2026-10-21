"""Dedicated asyncio lifecycle for the opt-in NMR source-control IPC.

The protected fitter's event IPC uses a separate server and must not be
repurposed for control. No EPICS or hardware commands are issued here.
"""

import asyncio
import threading


class NMRSourceIPCWorker:
    """Run an asyncio Unix-socket endpoint in one owned background thread."""

    def __init__(self, endpoint, *, startup_timeout_s=5.0, stop_timeout_s=5.0):
        if not callable(getattr(endpoint, "start", None)) or not callable(
            getattr(endpoint, "close", None)
        ):
            raise TypeError("IPC endpoint requires async start/close methods")
        self.endpoint = endpoint
        self.startup_timeout_s = float(startup_timeout_s)
        self.stop_timeout_s = float(stop_timeout_s)
        if not 0 < self.startup_timeout_s <= 30 or not 0 < self.stop_timeout_s <= 30:
            raise ValueError("Invalid IPC lifecycle timeout")
        self._thread = None
        self._loop = None
        self._startup_error = None
        self._cleanup_error = None
        self._ready = threading.Event()
        self._lock = threading.RLock()

    def _run(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.endpoint.start())
            self._ready.set()
            loop.run_forever()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            try:
                loop.run_until_complete(self.endpoint.close())
            except BaseException as exc:
                self._cleanup_error = exc
            finally:
                loop.close()
                asyncio.set_event_loop(None)

    def start(self):
        """Return only after the endpoint is listening; fail closed on errors."""
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("NMR source IPC worker already started")
            self._ready.clear()
            self._startup_error = None
            self._cleanup_error = None
            self._loop = None
            self._thread = threading.Thread(
                target=self._run, name="genesis-nmr-source-control", daemon=True
            )
            self._thread.start()
            if not self._ready.wait(self.startup_timeout_s):
                # Never treat a slow/unknown startup as operational.
                self.close()
                raise TimeoutError("NMR source IPC startup unconfirmed")
            if self._startup_error is not None:
                exc = self._startup_error
                self._thread.join(self.stop_timeout_s)
                self._thread = None
                raise RuntimeError("NMR source IPC startup failed") from exc
        return self

    def close(self):
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
            thread.join(self.stop_timeout_s)
            if thread.is_alive():
                raise RuntimeError("NMR source IPC thread did not stop")
            self._thread = None
            if self._cleanup_error is not None:
                raise RuntimeError("NMR source IPC cleanup failed") from self._cleanup_error
