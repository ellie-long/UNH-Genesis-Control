"""Mac-safe offline tests; Linux socket acceptance is tested on Ubuntu."""

import pytest
from online_fitter.nmr_source_control_ipc import apply_source_request


class Runtime:
    def __init__(self):
        self.source = "pynmr"
        self.epoch = 12
        self.calls = []

    def snapshot(self):
        return {"selected_source": self.source, "route_epoch": self.epoch}

    def select_source(self, source):
        self.calls.append(source)
        self.source = source
        self.epoch += 1


def invoke(runtime=None, **overrides):
    runtime = runtime if runtime is not None else Runtime()
    arguments = dict(
        peer_uid=1001, peer_pid=1234,
        allowed_uid=1001, allowed_pid=1234,
        request={"source": "labview"},
        snapshot=runtime.snapshot, select_source=runtime.select_source,
    )
    arguments.update(overrides)
    return runtime, lambda: apply_source_request(**arguments)


def test_authenticated_source_change_returns_epoch():
    runtime, action = invoke()
    assert action() == {"selected_source": "labview", "route_epoch": 13}
    assert runtime.calls == ["labview"]


@pytest.mark.parametrize("bad_peer", [{"peer_uid": 1002}, {"peer_pid": 1235},
                                      {"peer_pid": 0}, {"peer_uid": True}])
def test_wrong_peer_rejected_before_mutation(bad_peer):
    runtime, action = invoke(**bad_peer)
    with pytest.raises(PermissionError):
        action()
    assert runtime.calls == []


@pytest.mark.parametrize("payload", [{"source": "invalid"}, {"source": "pynmr"},
                                     {"source": "labview", "force": True},
                                     {"source": 0}, "labview", {}])
def test_invalid_or_noop_request_never_changes_source(payload):
    runtime, action = invoke(request=payload)
    with pytest.raises(ValueError):
        action()
    assert runtime.calls == []


def test_unknown_initial_epoch_rejected():
    runtime = Runtime()
    runtime.epoch = -1
    runtime, action = invoke(runtime)
    with pytest.raises(RuntimeError, match="unverified"):
        action()
    assert runtime.calls == []


def test_incorrect_acknowledgment_is_rejected():
    runtime = Runtime()
    def broken_select(source):
        runtime.calls.append(source)
    runtime, action = invoke(runtime, select_source=broken_select)
    with pytest.raises(RuntimeError, match="unverified"):
        action()


def test_epoch_must_advance():
    runtime = Runtime()
    def broken_select(source):
        runtime.source = source
    runtime, action = invoke(runtime, select_source=broken_select)
    with pytest.raises(RuntimeError, match="unverified"):
        action()
