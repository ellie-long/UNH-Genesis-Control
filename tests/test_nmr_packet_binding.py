"""Trusted packet-to-acquisition matching regressions."""

from online_fitter.packet_binding import (
    PacketBoundAdmission,
)
from online_fitter.source_receiver import (
    SourceAwareProcessor,
)
from online_fitter.source_state import NMRSourceState


def setup(source="pynmr"):
    clock = [100.0]
    state = NMRSourceState(clock=lambda: clock[0])

    state.select(source)
    state.update_stream(source, True)

    clock[0] = 101.0
    state.update_route(
        1, 0
    )

    binding = PacketBoundAdmission(state)
    fitted = []

    processor = SourceAwareProcessor(
        state.snapshot,
        packet_observer=binding.observe_packet,
        fit=lambda _, msg: (
            fitted.append(msg) or {"ok": True}
        ),
    )

    return clock, state, binding, processor, fitted


def packet(source="pynmr"):
    message = {
        "type": "sweep",
        "npts": 3,
        "raw": [1.0, 2.0, 3.0],
        "baseline_sub": [0.1, 0.2, 0.3],
        "centfreq_mhz": 32.7,
        "span_mhz": 0.4,
    }

    if source == "pynmr":
        message["source"] = "pynmr"

    return message


def attest(clock, binding, source, message):
    clock[0] = 102.0
    return binding.attest_finished_event(
        source, message, 101.5, 102.0
    )


def test_no_attestation_fails_closed():
    _, _, _, processor, fitted = setup()

    reply = processor(
        None, packet(), "pynmr"
    )

    assert not reply["ok"]
    assert fitted == []


def test_exact_pynmr_packet_fits_once():
    clock, _, binding, processor, fitted = setup()
    message = packet()

    assert attest(clock, binding, "pynmr", message)

    assert processor(None, message, "pynmr")["ok"]
    assert not processor(None, message, "pynmr")["ok"]

    assert len(fitted) == 1


def test_wrong_packet_cannot_consume_ticket():
    clock, state, binding, processor, fitted = setup()

    message = packet()
    wrong = packet()
    wrong["raw"] = [9.0, 9.0, 9.0]

    assert attest(clock, binding, "pynmr", message)

    assert not processor(None, wrong, "pynmr")["ok"]

    assert state.snapshot()["post_switch_ready"]
    assert processor(None, message, "pynmr")["ok"]

    assert len(fitted) == 1


def test_wrong_transport_cannot_consume_ticket():
    clock, _, binding, processor, fitted = setup()
    message = packet()

    assert attest(clock, binding, "pynmr", message)

    assert not processor(
        None, message, "labview"
    )["ok"]

    assert processor(None, message, "pynmr")["ok"]
    assert len(fitted) == 1


def test_route_transition_revokes_packet():
    clock, state, binding, processor, fitted = setup()
    message = packet()

    assert attest(clock, binding, "pynmr", message)

    clock[0] = 102.5
    state.update_route(1, 3)

    clock[0] = 103.0
    state.update_route(1, 0)

    assert not processor(
        None, message, "pynmr"
    )["ok"]

    assert fitted == []


def test_source_change_revokes_packet():
    clock, state, binding, processor, fitted = setup()
    message = packet()

    assert attest(clock, binding, "pynmr", message)

    state.select("labview")
    state.select("pynmr")

    assert not processor(
        None, message, "pynmr"
    )["ok"]

    assert fitted == []


def test_legacy_untagged_packet_can_be_bound():
    clock, _, binding, processor, fitted = setup(
        source="labview"
    )
    message = packet(source="labview")

    assert attest(clock, binding, "labview", message)

    assert processor(None, message, "labview")["ok"]
    assert len(fitted) == 1


def test_untrusted_source_tag_cannot_mint_attestation():
    clock, _, binding, processor, fitted = setup()
    message = packet(source="labview")

    assert not attest(
        clock, binding, "pynmr", message
    )

    assert not processor(
        None, message, "pynmr"
    )["ok"]

    assert fitted == []


def test_nonfinite_packet_rejected():
    clock, _, binding, processor, fitted = setup()
    message = packet()
    message["raw"][0] = float("nan")

    assert not attest(clock, binding, "pynmr", message)

    assert not processor(
        None, message, "pynmr"
    )["ok"]

    assert fitted == []


def test_old_acquisition_cannot_be_attested():
    clock, _, binding, processor, fitted = setup()
    clock[0] = 102.0

    message = packet()

    assert not binding.attest_finished_event(
        "pynmr", message, 100.0, 102.0
    )

    assert not processor(
        None, message, "pynmr"
    )["ok"]

    assert fitted == []
