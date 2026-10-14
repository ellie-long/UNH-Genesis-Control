"""Replay ports never collide with live ones, and the guard refuses near a live stack."""

import socket

import pytest

from cooldown_sim import guard, ports


def test_live_ports_scan_finds_known_live_ports():
    live = ports.live_ports()
    # cryo device IOCs, ssRF sim manager, microwave, fitter, gateway serial ports
    for port in (5110, 5116, 5122, 5216, 5064, 5075, 5068, 5100, 5101, 5004, 7777, 8777, 8081):
        assert port in live, port


def test_replay_ports_disjoint_from_live_ports():
    overlap = ports.replay_ports() & ports.live_ports()
    assert not overlap, f"replay ports collide with live ports: {sorted(overlap)}"


def test_replay_ports_unique():
    assert ports.duplicate_replay_ports() == {}


def test_replay_ports_in_private_ranges():
    for ep in ports.replay_endpoints():
        assert 6100 <= ep.port <= 6299 or 16000 <= ep.port <= 16999, ep


def test_port_offset_shifts_everything():
    base = ports.replay_ports()
    assert ports.replay_ports(offset=7) == {p + 7 for p in base}


def test_listening_ports_detects_a_listener():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        assert port in ports.listening_ports({port})


def test_unbindable_ports_reports_a_taken_port():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        assert port in ports.unbindable_ports({port})


# --------------------------------------------------------------------- guard
QUIET = dict(sessions=[], processes=[], live={5116}, probe=lambda c: set())


def test_guard_passes_on_quiet_dev_host():
    assert guard.check(hostname="dev-laptop", **QUIET) == []


@pytest.mark.parametrize("host", ["sliferlab-ubuntu", "SLIFERLAB-UBUNTU"])
def test_guard_refuses_production_host(host):
    found = guard.check(hostname=host, **QUIET)
    assert [r.check for r in found] == ["host"]


def test_guard_refuses_live_screen_sessions():
    found = guard.check(hostname="dev", **{**QUIET, "sessions": ["genesis-cryo-ls224_fridge", "other"]})
    assert [r.check for r in found] == ["screen"]
    assert "genesis-cryo-ls224_fridge" in found[0].detail


def test_guard_refuses_foreign_genesis_process(tmp_path):
    procs = [(41, "python -u master_ioc.py -s configs/target_physical/ls224_fridge -i ls224_fridge"),
             (42, "python notebook.py")]
    found = guard.check(tmp_path, hostname="dev", **{**QUIET, "processes": procs})
    assert [r.check for r in found] == ["process"]
    assert "pid 41" in found[0].detail and "pid 42" not in found[0].detail


def test_guard_ignores_this_runs_own_processes(tmp_path):
    run = tmp_path / "logs" / "cooldown_sim" / "r1"
    procs = [(7, f"python master_ioc.py -s {run}/profiles/cryo/ls224_fridge -i ls224_fridge")]
    assert guard.check(run, hostname="dev", **{**QUIET, "processes": procs}) == []


def test_guard_refuses_another_sim_run(tmp_path):
    other = tmp_path / "logs" / "cooldown_sim" / "other"
    procs = [(7, f"python master_ioc.py -s {other}/profiles/cryo/ls224_fridge -i ls224_fridge")]
    found = guard.check(tmp_path / "logs" / "cooldown_sim" / "mine", hostname="dev",
                        **{**QUIET, "processes": procs})
    assert [r.check for r in found] == ["process"]


def test_guard_refuses_listening_live_port():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        found = guard.check(hostname="dev", sessions=[], processes=[], live={port})
        assert [r.check for r in found] == ["port"]
        assert str(port) in found[0].detail
