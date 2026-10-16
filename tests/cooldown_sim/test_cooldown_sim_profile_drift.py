"""Profile drift guard.

A simulated cool-down must run the LIVE configuration.  Generated replay
profiles may differ from the canonical ones only in the allowlisted
loopback-endpoint, replay-port and run-directory keys; safety flags must be
identical; and every IOC in the canonical managers must be either launched or
deliberately excluded, so a new live IOC cannot silently fall out of the
replay.
"""

from pathlib import Path

import pytest
import yaml

from cooldown_sim import ports, profile

ROOT = Path(__file__).resolve().parents[2]


def flatten(node, prefix=""):
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    else:
        out[prefix] = node
    return out


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    return profile.generate(tmp_path_factory.mktemp("run"))


def test_every_replay_ioc_generated(stack):
    stacks = ports.load_stacks()
    legs = profile.with_companion_legs(("cryo", "ssrf", "mw"), stacks)
    assert "history" in legs                                # the trend history rides with cryo
    expected = {name for leg in legs for name, _s, _m in profile.leg_entries(stacks, leg)}
    assert {s.name for s in stack.iocs} == expected


def test_trend_history_reads_the_runs_archiver(stack):
    spec = stack.by_name("trend_history")
    generated = yaml.safe_load((Path(spec.folder) / "settings.yaml").read_text())
    archiver = Path(generated["trend_history"]["archiver_settings"])
    assert archiver == Path(stack.by_name("archiver").folder) / "settings.yaml"
    # the fitter's live CA port maps to the replay fitter
    assert "127.0.0.1:6100" in generated["general"]["epics_addr_list"].split()


def test_only_allowlisted_keys_differ(stack):
    for spec in stack.iocs:
        canonical = yaml.safe_load((ROOT / spec.canonical_folder / "settings.yaml").read_text())
        generated = yaml.safe_load((Path(spec.folder) / "settings.yaml").read_text())
        a, b = flatten(canonical), flatten(generated)
        assert set(a) == set(b), spec.name                    # no key added or removed
        allowed = {"general.epics_addr_list"}
        allowed |= {f"{spec.ioc}.{k}" for k in profile.ENDPOINT_KEYS}
        allowed |= {f"{spec.ioc}.{k}" for k in profile.OUTPUT_PATHS}
        allowed |= {f"{spec.ioc}.{k}" for k in profile.RUN_INPUTS}
        differing = {k for k in a if a[k] != b[k]}
        assert differing <= allowed, (spec.name, differing - allowed)


def test_safety_flags_identical(stack):
    for spec in stack.iocs:
        canonical = yaml.safe_load((ROOT / spec.canonical_folder / "settings.yaml").read_text())[spec.ioc]
        generated = yaml.safe_load((Path(spec.folder) / "settings.yaml").read_text())[spec.ioc]
        for key in profile.SAFETY_KEYS:
            assert canonical.get(key) == generated.get(key), (spec.name, key)


def test_rewritten_values_are_loopback_replay_or_run_dir(stack):
    replay = ports.replay_ports()
    for r in stack.rewrites:
        if r.key.endswith((".ip",)):
            assert r.new == "127.0.0.1", r
        elif r.key.endswith(".port"):
            assert r.new in replay, r
        elif r.key.endswith(".resource"):
            assert r.new.startswith("TCPIP0::127.0.0.1::") and int(r.new.split("::")[2]) in replay, r
        elif r.key == "general.epics_addr_list":
            for token in r.new.split():
                host, port = token.split(":")
                assert host == "127.0.0.1" and int(port) in replay, r
        else:
            assert Path(r.new).is_relative_to(stack.run_dir), r


def test_generated_profiles_are_loopback_only(stack):
    for spec in stack.iocs:
        generated = yaml.safe_load((Path(spec.folder) / "settings.yaml").read_text())
        assert profile.non_loopback_endpoints(generated) == [], spec.name


def test_loopback_scan_catches_a_lan_address():
    assert profile.non_loopback_endpoints({"x": {"ip": "192.168.1.12"}})
    assert profile.non_loopback_endpoints({"x": {"resource": "TCPIP0::192.168.1.24::INSTR"}})


def test_canonical_managers_fully_accounted_for():
    stacks = ports.load_stacks()
    managers = {}
    for leg in ("cryo", "ssrf", "mw"):
        for name, _spec, manager in profile.leg_entries(stacks, leg):
            managers.setdefault(manager, set()).add(name)
    for manager, excluded in stacks.get("excluded", {}).items():
        managers.setdefault(manager, set()).update(excluded)
    for manager, accounted in managers.items():
        entries = {k for k in yaml.safe_load((ROOT / manager).read_text()) if k != "general"}
        assert entries <= accounted, (manager, entries - accounted)
    # the canonical cryo and sim managers are covered completely
    for manager in ("configs/target_physical/ioc_manager/settings.yaml",
                    "configs/commissioning/ssrf_sim_active/ioc_manager/settings.yaml",
                    "configs/commissioning/microwave_sim/ioc_manager/settings.yaml",
                    "configs/commissioning/microwave_physical/ioc_manager/settings.yaml"):
        assert manager in managers, manager


def test_output_paths_never_point_at_live_archive(stack):
    for spec in stack.iocs:
        generated = flatten(yaml.safe_load((Path(spec.folder) / "settings.yaml").read_text()))
        for key, value in generated.items():
            if key.split(".")[-1] in profile.OUTPUT_PATHS:
                assert Path(value).is_relative_to(stack.run_dir), (spec.name, key, value)


def test_scenario_overrides_are_limited(tmp_path):
    with pytest.raises(ValueError):
        profile.generate(tmp_path, ("cryo",), overrides={"ls224_fridge": {"write_enabled": True}})
    limits = tmp_path / "limits.yaml"
    limits.write_text("{}")
    out = profile.generate(tmp_path, ("cryo",), overrides={
        "target_status": {"state_table_validated": True},
        "safety_interlock": {"limits": str(limits)}})
    assert {r.key for r in out.overrides} == {"target_status.state_table_validated",
                                              "safety_interlock.limits"}
    ts = yaml.safe_load(Path(out.by_name("target_status").folder, "settings.yaml").read_text())
    assert ts["target_status"]["state_table_validated"] is True
    assert ts["target_status"]["apply_enabled"] is False         # safety flag untouched


def test_unmapped_addr_list_port_is_an_error():
    with pytest.raises(ValueError):
        profile.map_addr_list("127.0.0.1:5999", {5111: 6111})
    with pytest.raises(ValueError):
        profile.map_addr_list("192.168.1.5:5111", {5111: 6111})
    assert profile.map_addr_list("127.0.0.1:5111", {5111: 6111}) == "127.0.0.1:6111"


def test_port_offset_applies_everywhere(tmp_path):
    out = profile.generate(tmp_path, ("cryo",), offset=5)
    fridge = out.by_name("ls224_fridge")
    assert fridge.ca == 6121 and fridge.pva == 6221
    settings = yaml.safe_load(Path(fridge.folder, "settings.yaml").read_text())
    assert settings["ls224_fridge"]["port"] == 16017
