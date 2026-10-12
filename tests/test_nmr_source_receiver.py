"""Loopback-only synthetic receiver integration tests."""

import json
import socket
from types import SimpleNamespace

from online_fitter import server
from online_fitter.source_receiver import SourceAwareProcessor


def observation(source="pynmr", ready=True):
    return {
        "selected_source": source,
        "nmr_mask": 3 if source == "pynmr" else 0,
        "nmr_comm": 1,
        "route_age_s": 0.1,
        "data_age_s": 0.2,
        "stream_connected": True,
        "post_switch_ready": ready,
    }


def test_rejects_without_switch_barrier():
    calls = []
    processor = SourceAwareProcessor(
        lambda: observation(ready=False),
        fit=lambda state, msg: calls.append(msg),
    )
    reply = processor(None, {"source": "pynmr"}, "pynmr")
    assert reply["ok"] is False
    assert calls == []


def test_rejects_wrong_trusted_channel():
    calls = []
    processor = SourceAwareProcessor(
        lambda: observation(),
        fit=lambda state, msg: calls.append(msg),
    )
    reply = processor(None, {"source": "pynmr"}, "labview")
    assert reply["ok"] is False
    assert calls == []


def test_legacy_labview_channel_accepts_untagged_data():
    calls = []
    processor = SourceAwareProcessor(
        lambda: observation(source="labview"),
        fit=lambda state, msg: (
            calls.append(msg) or {"ok": True}
        ),
    )
    reply = processor(None, {"type": "sweep"}, "labview")
    assert reply["ok"] is True
    assert len(calls) == 1


def test_live_loopback_receiver_rejects_then_accepts():
    calls = []
    processor = SourceAwareProcessor(
        lambda: observation(),
        fit=lambda state, msg: (
            calls.append(msg) or {"ok": True}
        ),
    )

    listener = server.make_server(
        SimpleNamespace(n_received=0),
        host="127.0.0.1",
        port=0,
        trusted_source="pynmr",
        sweep_processor=processor,
    )

    thread = server.serve_in_thread(listener)

    try:
        with socket.create_connection(
            listener.server_address, timeout=3
        ) as sock:
            sock.settimeout(3)
            with sock.makefile("rwb") as stream:
                for payload, accepted in (
                    ({"type": "sweep", "source": "labview"}, False),
                    ({"type": "sweep", "source": "pynmr"}, True),
                ):
                    stream.write(
                        (json.dumps(payload) + "\n").encode()
                    )
                    stream.flush()

                    reply = json.loads(stream.readline())
                    assert reply["ok"] is accepted

        assert len(calls) == 1
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)


def test_partial_protected_configuration_rejected():
    import pytest

    with pytest.raises(ValueError):
        server.make_server(
            SimpleNamespace(),
            port=0,
            trusted_source="pynmr",
        )

    with pytest.raises(ValueError):
        server.make_server(
            SimpleNamespace(),
            port=0,
            sweep_processor=lambda *args: None,
        )
