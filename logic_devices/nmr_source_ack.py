"""Fail-closed acknowledgment checks for NMR software-source changes.

No EPICS writes, hardware operations, or sequencer control.
"""


SOURCES = frozenset(("labview", "pynmr"))


def validate_source_change_ack(
    *,
    requested,
    previous_source,
    previous_epoch,
    observed_source,
    observed_epoch,
):
    """Require a verified new software selection and acquisition epoch."""

    if requested not in SOURCES:
        return False

    if previous_source not in SOURCES:
        return False

    if previous_source == requested:
        return False

    if type(previous_epoch) is not int or previous_epoch < 0:
        return False

    if observed_source != requested:
        return False

    if type(observed_epoch) is not int:
        return False

    return observed_epoch > previous_epoch
