"""Offline tests of the actual optional Cryogenics device parsers.

The DeviceConnection AST is loaded from production source, replacing
only its socket-owning parent class with a fake transport.
No hardware connections, EPICS IOCs, or sockets are created.
"""

import ast
import copy
import math
import re
from functools import lru_cache
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT / "target_ioc" / "instruments"

CONSTANTS = {
    "FLOAT_RE", "TERMINATOR",
    "COMMAND_EOL", "REPLY_EOL", "EOL",
}


class FakeTransport:
    def __init__(self, replies, **options):
        self.replies = list(replies)
        self.sent = []
        self.discard_count = 0
        self.read_count = 0
        self.host = "offline-fake"
        self.port = 0

        self.request = options.get("request", "F")
        self.query = options.get(
            "query", "MEASure:HE:LEVel?"
        )
        self.idn_query = options.get(
            "idn_query", "*IDN?"
        )
        self.substring_start = options.get(
            "substring_start", 5
        )

    def discard_input(self):
        self.discard_count += 1

    def send(self, payload):
        if isinstance(payload, str):
            payload = payload.encode("ascii")
        self.sent.append(payload)

    def read_until(self, terminator=None, limit=4096):
        self.read_count += 1

        if not self.replies:
            raise OSError("simulated socket timeout")

        response = self.replies.pop(0)

        if isinstance(response, OSError):
            raise response

        assert isinstance(response, bytes)

        if isinstance(terminator, str):
            terminator = terminator.encode("ascii")

        if terminator not in response:
            raise OSError(
                "simulated incomplete instrument response"
            )

        return response


@lru_cache(maxsize=None)
def parser_class(filename):
    path = DRIVERS / filename
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))

    nodes = []

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        if any(
            isinstance(target, ast.Name)
            and target.id in CONSTANTS
            for target in node.targets
        ):
            nodes.append(copy.deepcopy(node))

    classes = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "DeviceConnection"
    ]

    assert len(classes) == 1
    cls = copy.deepcopy(classes[0])

    assert [ast.unparse(base) for base in cls.bases] == [
        "SerialTcpConnection"
    ]

    # Use the production methods, without the real socket
    # constructor inherited from SerialTcpConnection.
    cls.bases = [
        ast.Name(id="FakeTransport", ctx=ast.Load())
    ]

    nodes.append(cls)

    module = ast.fix_missing_locations(
        ast.Module(body=nodes, type_ignores=[])
    )

    namespace = {
        "FakeTransport": FakeTransport,
        "math": math,
        "re": re,
    }

    exec(
        compile(module, str(path), "exec"),
        namespace,
    )

    return namespace["DeviceConnection"]


def instrument(filename, *replies, **options):
    cls = parser_class(filename)
    if filename == "ami135.py":
        options.setdefault("query", "LEVEL")
    return cls(replies, **options)


def measure(device, filename):
    if filename in (
        "flowmeter_pi.py",
        "flowmeter_diewu.py",
    ):
        return device.read_flow()

    if filename in ("ami1700.py", "ami135.py"):
        return device.read_level()

    if filename == "manometer_low.py":
        return device.read_pressure()

    raise AssertionError(filename)


CASES = [
    (
        "flowmeter_pi.py",
        b"12.5>",
        12.5,
        b"F\r",
    ),
    (
        "flowmeter_diewu.py",
        b"2.75>",
        2.75,
        b"F\r",
    ),
    (
        "ami1700.py",
        b"16.25\n",
        16.25,
        b"MEASure:HE:LEVel?\r",
    ),
    (
        "ami135.py",
        b"42.5\r\n",
        42.5,
        b"LEVEL\r\n",
    ),
    (
        "manometer_low.py",
        b"HEAD:1.25e-3\n",
        0.00125,
        None,
    ),
]


@pytest.mark.parametrize(
    "filename,reply,expected,command", CASES
)
def test_valid_measurement_and_command_framing(
    filename, reply, expected, command
):
    device = instrument(filename, reply)
    result = measure(device, filename)

    assert result[0] == pytest.approx(expected)
    assert result[1] == "OK"
    assert device.discard_count == 1

    if command is None:
        assert device.sent == []
    else:
        assert device.sent == [command]


@pytest.mark.parametrize(
    "filename,reply",
    [
        ("flowmeter_pi.py", b"invalid>"),
        ("flowmeter_diewu.py", b"invalid>"),
        ("ami1700.py", b"invalid\n"),
        ("ami135.py", b"invalid\r\n"),
        ("manometer_low.py", b"HEAD:invalid\n"),
    ],
)
def test_malformed_response_is_nan(filename, reply):
    device = instrument(filename, reply)
    result = measure(device, filename)

    assert math.isnan(result[0])
    assert result[1] == "PARSE_ERR"


@pytest.mark.parametrize(
    "filename,reply",
    [
        ("flowmeter_pi.py", b"1e999>"),
        ("flowmeter_diewu.py", b"1e999>"),
        ("ami1700.py", b"1e999\n"),
        ("ami135.py", b"1e999\r\n"),
        ("manometer_low.py", b"HEAD:1e999\n"),
    ],
)
def test_nonfinite_response_is_rejected(filename, reply):
    device = instrument(filename, reply)
    result = measure(device, filename)

    assert math.isnan(result[0])
    assert result[1] == "NOT_FINITE"


@pytest.mark.parametrize(
    "filename",
    [
        "flowmeter_pi.py",
        "flowmeter_diewu.py",
        "ami1700.py",
        "ami135.py",
        "manometer_low.py",
    ],
)
def test_transport_timeout_propagates(filename):
    device = instrument(
        filename,
        OSError("simulated timeout"),
    )

    with pytest.raises(OSError, match="simulated timeout"):
        measure(device, filename)


@pytest.mark.parametrize(
    "filename,reply,expected_status",
    [
        ("flowmeter_pi.py", b">", "PARSE_ERR"),
        ("flowmeter_diewu.py", b">", "EMPTY_REPLY"),
        ("ami1700.py", b"\n", "EMPTY_REPLY"),
        ("ami135.py", b"\r\n", "EMPTY_REPLY"),
    ],
)
def test_empty_formatted_response(
    filename, reply, expected_status
):
    device = instrument(filename, reply)
    result = measure(device, filename)

    assert math.isnan(result[0])
    assert result[1] == expected_status


def test_pi_server_error_keeps_reason():
    device = instrument(
        "flowmeter_pi.py",
        b"ERROR serial unavailable>",
    )

    value, status = device.read_flow()

    assert math.isnan(value)
    assert status == "serial unavailable"


def test_ami135_echo_is_discarded():
    device = instrument(
        "ami135.py",
        b"LEVEL\r\n",
        b"45.75\r\n",
    )

    value, status = device.read_level()

    assert value == pytest.approx(45.75)
    assert status == "OK"
    assert device.sent == [b"LEVEL\r\n"]
    assert device.read_count == 2


def test_ami1700_identity_query():
    device = instrument(
        "ami1700.py",
        b"AMI,1700,TEST,1.0\n",
    )

    assert device.read_idn() == "AMI,1700,TEST,1.0"
    assert device.sent == [b"*IDN?\r"]


def test_manometer_is_passive_and_rejects_empty_stream():
    device = instrument(
        "manometer_low.py",
        b"\n",
    )

    with pytest.raises(OSError):
        device.read_pressure()

    assert device.sent == []
    assert device.discard_count == 1
