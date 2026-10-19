"""Offline regression tests for NMR source-change acknowledgment."""

from logic_devices.nmr_source_ack import validate_source_change_ack


def ack(**overrides):
    values = dict(
        requested="labview",
        previous_source="pynmr",
        previous_epoch=12,
        observed_source="labview",
        observed_epoch=13,
    )
    values.update(overrides)
    return validate_source_change_ack(**values)


def test_confirmed_transition():
    assert ack()


def test_reverse_transition():
    assert ack(
        requested="pynmr",
        previous_source="labview",
        observed_source="pynmr",
    )


def test_wrong_selected_source():
    assert not ack(observed_source="pynmr")


def test_epoch_not_advanced():
    assert not ack(observed_epoch=12)


def test_stale_epoch():
    assert not ack(observed_epoch=11)


def test_missing_epoch():
    assert not ack(observed_epoch=None)


def test_invalid_previous_epoch():
    assert not ack(previous_epoch=-1)


def test_boolean_epoch_rejected():
    assert not ack(observed_epoch=True)


def test_unknown_source_rejected():
    assert not ack(requested="invalid")


def test_same_source_not_a_transition():
    assert not ack(previous_source="labview")
