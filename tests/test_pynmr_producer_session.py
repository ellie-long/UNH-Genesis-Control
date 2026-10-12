"""Tests for commissioning-only producer authorization."""

import os
import pytest

from online_fitter.pynmr_producer_session import (
    PinnedPyNMRProducer,
)


def test_default_disabled(monkeypatch):
    monkeypatch.delenv(
        "GENESIS_PYNMR_PRODUCER_PID", raising=False
    )
    monkeypatch.delenv(
        "GENESIS_PYNMR_PRODUCER_START_TICKS", raising=False
    )
    assert PinnedPyNMRProducer.from_environment() is None


@pytest.mark.parametrize("missing", [
    "GENESIS_PYNMR_PRODUCER_PID",
    "GENESIS_PYNMR_PRODUCER_START_TICKS",
])
def test_incomplete_configuration_rejected(monkeypatch, missing):
    monkeypatch.setenv(
        "GENESIS_PYNMR_PRODUCER_PID", "12345"
    )
    monkeypatch.setenv(
        "GENESIS_PYNMR_PRODUCER_START_TICKS", "67890"
    )
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError):
        PinnedPyNMRProducer.from_environment()


def test_stale_pid_rejected():
    session = PinnedPyNMRProducer(999999999, 1)
    assert not session.matches_process()


def test_peer_mismatch_rejected(monkeypatch):
    session = PinnedPyNMRProducer(os.getpid(), 1)
    monkeypatch.setattr(
        session, "matches_process", lambda: True
    )

    assert session.peer_allowed(
        os.getpid(), os.getuid(), os.getgid()
    )
    assert not session.peer_allowed(
        os.getpid() + 1, os.getuid(), os.getgid()
    )
    assert not session.peer_allowed(
        os.getpid(), os.getuid() + 1, os.getgid()
    )
