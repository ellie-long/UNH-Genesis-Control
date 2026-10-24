"""Hardware-free regression tests for the optional SIGLENT scope."""

import struct

import numpy as np
import pytest

from microwave_ioc.siglent_sds814x.toolkit_device import (
    Controller,
    SCPIError,
    decode_waveform,
    parse_preamble,
    simulator_preamble,
)


def test_signed_word_voltage_and_time_conversion():
    preamble = simulator_preamble(count=3)
    payload = struct.pack("<3h", -6400, 0, 6400)

    seconds, volts = decode_waveform(
        preamble, payload, timebase=0.001
    )

    np.testing.assert_allclose(volts, [-1.0, 0.0, 1.0])
    np.testing.assert_allclose(
        seconds,
        [-0.005, -0.004999, -0.004998],
        atol=1e-12,
    )


def test_invalid_descriptor_rejected():
    with pytest.raises(ValueError, match="WAVEDESC"):
        parse_preamble(bytes(346))


def test_invalid_word_payload_rejected():
    preamble = simulator_preamble()

    with pytest.raises(ValueError, match="payload"):
        decode_waveform(preamble, b"\x01", 0.001)


def test_invalid_timebase_rejected():
    preamble = simulator_preamble()

    with pytest.raises(ValueError, match="timebase"):
        decode_waveform(preamble, b"\x00\x00", 0)


def test_sequence_mode_rejected_without_waveform_request():
    class FakeTransport:
        def __init__(self):
            self.commands = []

        def query_many(self, commands):
            self.commands.append(tuple(commands))
            return ["ON", "10000", "0.001"]

        def write(self, command):
            raise AssertionError("Unexpected scope write")

        def query(self, command, binary=False):
            raise AssertionError("Unexpected waveform query")

    transport = FakeTransport()

    with pytest.raises(SCPIError, match="sequence"):
        Controller(transport).snapshot()

    assert len(transport.commands) == 1


def test_initialize_preserves_acquisition_controls():
    class FakeTransport:
        def __init__(self):
            self.writes = []

        def write(self, command):
            self.writes.append(command)

        def query(self, command):
            assert command == "*IDN?"
            return "Siglent Technologies,SDS814X HD,TEST,1.0"

    transport = FakeTransport()
    Controller(transport).initialize()

    assert transport.writes == [
        "CHDR OFF",
        ":WAV:SOUR C1;:WAV:WIDT WORD;:WAV:BYT LSB",
    ]
