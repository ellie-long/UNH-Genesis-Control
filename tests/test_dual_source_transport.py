"""Synthetic TCP validation of the protected NMR listeners."""

import json
import socket
from types import SimpleNamespace

import pytest

from online_fitter.dual_source_transport import (
    DualSourceTransport,
)
from online_fitter.source_receiver import (
    SourceAwareProcessor,
)


def send(port, payload):
    with socket.create_connection(
        ("127.0.0.1", port), timeout=3
    ) as sock:
        sock.settimeout(3)

        with sock.makefile("rwb") as stream:
            stream.write(
                (json.dumps(payload) + "\n").encode()
            )
            stream.flush()

            return json.loads(
                stream.readline()
            )


def observation(source):
    return {
        "selected_source": source,
        "nmr_mask": 0 if source == "labview" else 3,
        "nmr_comm": 1,
        "route_age_s": 0.1,
        "data_age_s": 0.2,
        "stream_connected": True,
        "post_switch_ready": True,
    }


def test_two_distinct_protected_tcp_listeners():
    selected = ["labview"]
    fitted = []

    processor = SourceAwareProcessor(
        lambda: observation(selected[0]),
        fit=lambda state, msg: (
            fitted.append(msg)
            or {"ok": True}
        ),
    )

    manager = DualSourceTransport(
        SimpleNamespace(n_received=0),
        processor,
        labview_port=0,
        pynmr_port=0,
    )

    with manager:
        ports = manager.ports

        assert set(ports) == {"labview", "pynmr"}
        assert ports["labview"] != ports["pynmr"]

        assert all(
            listener.sweep_processor is processor
            for listener in manager._listeners.values()
        )

        assert (
            manager._listeners["labview"].trusted_source
            == "labview"
        )
        assert (
            manager._listeners["pynmr"].trusted_source
            == "pynmr"
        )

        # Legacy LabVIEW packets may omit source metadata.
        assert send(
            ports["labview"],
            {"type": "sweep"},
        )["ok"]

        # The other channel cannot reach the fitter.
        assert not send(
            ports["pynmr"],
            {"type": "sweep", "source": "pynmr"},
        )["ok"]

        selected[0] = "pynmr"

        assert send(
            ports["pynmr"],
            {"type": "sweep", "source": "pynmr"},
        )["ok"]

        assert not send(
            ports["labview"],
            {"type": "sweep"},
        )["ok"]

        assert len(fitted) == 2

    assert manager.ports == {}


def test_unprotected_processor_is_rejected():
    with pytest.raises(TypeError):
        DualSourceTransport(
            SimpleNamespace(),
            None,
        )


def test_duplicate_ports_are_rejected():
    with pytest.raises(ValueError):
        DualSourceTransport(
            SimpleNamespace(),
            lambda *args: None,
            labview_port=8777,
            pynmr_port=8777,
        )


@pytest.mark.parametrize(
    "port", [-1, 65536, None, True]
)
def test_invalid_ports_are_rejected(port):
    with pytest.raises(ValueError):
        DualSourceTransport(
            SimpleNamespace(),
            lambda *args: None,
            labview_port=port,
        )


def test_start_twice_is_rejected():
    processor = SourceAwareProcessor(
        lambda: observation("labview"),
        fit=lambda state, msg: {"ok": True},
    )

    manager = DualSourceTransport(
        SimpleNamespace(n_received=0),
        processor,
        labview_port=0,
        pynmr_port=0,
    )

    with manager:
        with pytest.raises(RuntimeError):
            manager.start()


def test_close_without_start_is_safe():
    manager = DualSourceTransport(
        SimpleNamespace(),
        lambda *args: {"ok": False},
        labview_port=0,
        pynmr_port=0,
    )

    manager.close()
    assert manager.ports == {}


def test_port_conflict_closes_partial_startup():
    processor = SourceAwareProcessor(
        lambda: observation("labview"),
        fit=lambda state, msg: {"ok": True},
    )

    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)

        blocked_port = blocker.getsockname()[1]

        manager = DualSourceTransport(
            SimpleNamespace(n_received=0),
            processor,
            labview_port=0,
            pynmr_port=blocked_port,
        )

        with pytest.raises(OSError):
            manager.start()

        assert manager.ports == {}

        manager.close()


def test_pynmr_only_opens_exactly_one_receiver():
    selected = ["pynmr"]
    processor = SourceAwareProcessor(
        lambda: observation(selected[0]),
        fit=lambda state, msg: {"ok": True},
    )
    manager = DualSourceTransport(
        SimpleNamespace(n_received=0), processor,
        labview_port=0, pynmr_port=0, pynmr_only=True,
    )
    with manager:
        assert set(manager.ports) == {"pynmr"}
        assert set(manager._listeners) == {"pynmr"}
        assert manager._listeners["pynmr"].trusted_source == "pynmr"
    assert manager.ports == {}
