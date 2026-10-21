"""Genesis Cryo Raspberry Pi bridge lifecycle regression tests."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PI = SCRIPTS / "pi_cryo_bridges"


def load_flow_bridge():
    path = PI / "flowmeter_pi_headless.py"
    spec = importlib.util.spec_from_file_location(
        "genesis_cryo_flow_bridge_test", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stale_flow_invalidated_on_disconnect():
    bridge = load_flow_bridge()
    latest = bridge.LatestReading()

    latest.update(-0.26, -0.383, "Flow: -0.26")
    assert latest.tcp_reply().startswith("-0.260000>")

    latest.set_error("USB disconnected")
    reply = latest.tcp_reply()

    assert reply.startswith("ERROR ")
    assert "USB disconnected" in reply
    assert "-0.260000" not in reply


def test_missing_usb_does_not_terminate_serial_reader():
    bridge = load_flow_bridge()
    latest = bridge.LatestReading()

    class StopAfterOneRetry:
        cycles = 0

        def is_set(self):
            return self.cycles >= 1

        def wait(self, seconds):
            self.cycles += 1

    with patch.object(
        bridge, "Flowmeter",
        side_effect=OSError("USB unavailable"),
    ):
        bridge.serial_reader(
            "/dev/nonexistent-genesis-device",
            19200,
            "F",
            28 / 19,
            latest,
            1.0,
            StopAfterOneRetry(),
        )

    assert latest.tcp_reply().startswith("ERROR ")


def test_pi_controller_manages_only_genesis_bridges():
    controller = (PI / "ssh_control.sh").read_text()

    assert 'case "${SSH_ORIGINAL_COMMAND:-}" in' in controller

    for command in ("start)", "status)", "stop)"):
        assert command in controller

    assert "genesis-cryo-flow-right" in controller
    assert "genesis-cryo-manometer-low" in controller
    assert "5010" in controller
    assert "5030" in controller
    assert "genesis_cryo_bridges/env/bin/python" not in controller or \
        'PYTHON="$ROOT/env/bin/python"' in controller

    assert '[[ ! -e "$FLOW_DEVICE" ]]' not in controller
    assert "cooldown/bin/python" not in controller


def test_ubuntu_controller_uses_noninteractive_restricted_ssh():
    controller = (PI / "ubuntu_control.sh").read_text()

    assert "genesis_cryo_pi_ed25519" in controller
    assert "BatchMode=yes" in controller
    assert "IdentitiesOnly=yes" in controller
    assert "unhpoltarg@192.168.1.24" in controller


def test_cryo_start_always_checks_pi_bridges():
    script = (SCRIPTS / "cryo_physical.sh").read_text()

    start = script.split("start_stack()\n{", 1)[1]
    start = start.split("\n}\n", 1)[0]

    assert "pi_bridge_control start" in start
    assert start.index("pi_bridge_control start") < \
        start.index('state="$(stack_state)"')

    assert 'return "$bridge_failed"' in start
    assert 'for name in "${START_ORDER[@]}"' in start


def test_cryo_stop_includes_pi_bridges():
    script = (SCRIPTS / "cryo_physical.sh").read_text()

    stop = script.split("stop_stack()\n{", 1)[1]
    stop = stop.split("\n}\n", 1)[0]

    assert "pi_bridge_control stop" in stop
    assert stop.index("pi_bridge_control stop") > \
        stop.index('for name in "${STOP_ORDER[@]}"')


def test_cryo_status_reports_pi_bridges_and_measurements():
    script = (SCRIPTS / "cryo_physical.sh").read_text()

    status = script.split("show_status()\n{", 1)[1]
    status = status.split("\n}\n", 1)[0]

    assert "pi_bridge_control status" in status
    assert "RASPBERRY PI CRYO BRIDGES" in status

    for pv in (
        "UNH:GEN:CRYO:FLOW:RIGHT:COMM",
        "UNH:GEN:CRYO:FLOW:RIGHT:RPump_Raw",
        "UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM",
        "UNH:GEN:CRYO:FLOW:RIGHT:RPump_Status",
        "UNH:GEN:CRYO:VAC:LOW:COMM",
        "UNH:GEN:CRYO:VAC:LOW:Mano_PI",
        "UNH:GEN:CRYO:VAC:LOW:Mano_Status",
    ):
        assert pv in status
