"""Mac-safe pure-unit tests; Linux socket transport must be validated on Ubuntu."""

import json
import pytest
from logic_devices.nmr_source_ipc_client import decode_source_reply


def reply(obj):
    return (json.dumps(obj) + "\n").encode()


def good():
    return {"ok": True, "selected_source": "pynmr", "route_epoch": 21}


def test_accepts_valid_acknowledgment():
    assert decode_source_reply(reply(good()), requested="pynmr") == {
        "selected_source": "pynmr", "route_epoch": 21,
    }


@pytest.mark.parametrize("bad", [
    {"ok": True, "selected_source": "labview", "route_epoch": 21},
    {"ok": True, "selected_source": "pynmr", "route_epoch": -1},
    {"ok": True, "selected_source": "pynmr", "route_epoch": True},
    {"ok": True, "selected_source": "pynmr", "route_epoch": "21"},
    {"ok": True, "selected_source": "pynmr", "route_epoch": 21, "extra": 1},
    {"ok": False, "error": "rejected"},
    {"ok": 1, "selected_source": "pynmr", "route_epoch": 21},
])
def test_rejects_invalid_acknowledgment(bad):
    with pytest.raises(RuntimeError):
        decode_source_reply(reply(bad), requested="pynmr")


@pytest.mark.parametrize("bad", [b"", b"{}", b"not-json\n", b"x" * 513,
                                     b"\xff\n"])
def test_rejects_bad_framing(bad):
    with pytest.raises(RuntimeError):
        decode_source_reply(bad, requested="pynmr")


def test_rejects_invalid_requested_source():
    with pytest.raises(ValueError):
        decode_source_reply(reply(good()), requested="other")
