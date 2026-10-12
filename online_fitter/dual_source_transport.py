"""Two separate, protected loopback NMR fitter input listeners.

LabVIEW: localhost:8777
PyNMR:   localhost:8779

The fit publisher remains separately owned on localhost:8778.

Both listeners use one common protected sweep processor and one
common fitter state. No listener in this manager is unprotected.

Port identity is configured by the receiving service. Access to a
local TCP port does not itself authenticate a remote acquisition
device; approved source-specific forwarding is required separately.

No EPICS or hardware operations occur in this module.
"""

from . import server


from .nmr_tcp_connection_tracker import (
    TCPConnectionTracker,
)


class DualSourceTransport:
    def __init__(
        self,
        state,
        processor,
        *,
        labview_port=8777,
        pynmr_port=8779,
        verbose=False,
        connection_observer=None,
    ):
        if not callable(processor):
            raise TypeError(
                "protected sweep processor is required"
            )

        for port in (labview_port, pynmr_port):
            if type(port) is not int or not 0 <= port <= 65535:
                raise ValueError("invalid local TCP port")

        if (
            labview_port == pynmr_port
            and labview_port != 0
        ):
            raise ValueError(
                "LabVIEW and PyNMR ports must differ"
            )

        self.state = state
        self.processor = processor
        self.verbose = verbose
        if (
            connection_observer is not None
            and not callable(connection_observer)
        ):
            raise TypeError("Invalid connection observer")
        self.connection_observer = connection_observer

        self._requested_ports = {
            "labview": labview_port,
            "pynmr": pynmr_port,
        }

        self._listeners = {}
        self._threads = {}

    @property
    def ports(self):
        return {
            source: listener.server_address[1]
            for source, listener in self._listeners.items()
        }

    def start(self):
        if self._listeners:
            raise RuntimeError(
                "dual NMR transport is already running"
            )

        opened = {}
        running = {}
        tracking = {}

        try:
            for source in ("labview", "pynmr"):
                opened[source] = server.make_server(
                    self.state,
                    host="127.0.0.1",
                    port=self._requested_ports[source],
                    verbose=self.verbose,
                    trusted_source=source,
                    sweep_processor=self.processor,
                )

                if self.connection_observer is not None:
                    monitor = TCPConnectionTracker(
                        source,
                        self.connection_observer,
                    )
                    opened[source].RequestHandlerClass = (
                        monitor.wrap(
                            opened[source].RequestHandlerClass
                        )
                    )
                    tracking[source] = monitor

            ports = [
                listener.server_address[1]
                for listener in opened.values()
            ]

            if len(set(ports)) != 2:
                raise RuntimeError(
                    "dual NMR listener ports collided"
                )

            for source, listener in opened.items():
                running[source] = server.serve_in_thread(
                    listener
                )

        except BaseException:
            for source in running:
                opened[source].shutdown()

            for listener in opened.values():
                listener.server_close()

            for thread in running.values():
                thread.join(timeout=3)

            raise

        self._connection_trackers = tracking
        self._listeners = opened
        self._threads = running
        return self

    def close(self):
        tracking = getattr(
            self, "_connection_trackers", {}
        )
        self._connection_trackers = {}

        for monitor in tracking.values():
            monitor.close()

        listeners = self._listeners
        threads = self._threads

        self._listeners = {}
        self._threads = {}

        for listener in listeners.values():
            listener.shutdown()

        for listener in listeners.values():
            listener.server_close()

        for thread in threads.values():
            thread.join(timeout=3)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
