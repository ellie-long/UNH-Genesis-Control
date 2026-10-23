"""The simulated cool-down's magnet leg: the IPS120 IOC rides with the cryo leg."""

from pathlib import Path

import yaml

from cooldown_sim import ports, profile, pvlist
from cooldown_sim.emulators import host
from cooldown_sim.emulators.ips120 import IPS120


def test_magnet_leg_is_generated_with_cryo(tmp_path):
    st = profile.generate(tmp_path, ("cryo",), offset=700)
    spec = st.by_name("ips120")
    assert spec.leg == "magnet" and spec.emulator["kind"] == "ips120"
    cfg = yaml.safe_load((Path(spec.folder) / "settings.yaml").read_text(encoding="utf-8"))
    block = cfg["ips120"]
    assert block["ip"] == "127.0.0.1" and block["port"] == 16120 + 700
    assert block["write_enabled"] is False                         # never touched
    assert Path(block["magnet_event_dir"]).is_relative_to(tmp_path)
    # the trend history reaches the replay magnet IOC, not the live one
    th = yaml.safe_load((Path(st.by_name("trend_history").folder) / "settings.yaml").read_text())
    assert f"127.0.0.1:{6136 + 700}" in th["general"]["epics_addr_list"]
    assert "5131" not in th["general"]["epics_addr_list"]
    mag = [p for p in pvlist.for_stack(st, legs={"cryo"}) if p.startswith("UNH:GEN:MAG:")]
    assert "UNH:GEN:MAG:FIELD" in mag and "UNH:GEN:MAG:MAN:ips120_time" in mag
    assert not ports.duplicate_replay_ports(offset=700)


class _Channel:
    def __init__(self, value):
        self.value = value

    def recorded(self, timeline, t):
        return self.value


class _Map:
    def __init__(self, value):
        self.history = {"magnet_i": _Channel(value)}


def test_emulator_starts_at_the_record_magnet_current():
    assert host.magnet_initial(_Map(48.6664), object(), 0.0) == {"initial_current_a": 48.666}
    assert host.magnet_initial(_Map(float("nan")), object(), 0.0) == {}
    assert host.magnet_initial(_Map(80.0), object(), 0.0) == {"initial_current_a": 65.0}
    assert host.magnet_initial(_Map(5.0), None, 0.0) == {}

    class Spec:
        emulator = {"kind": "ips120"}
    emu = host.build(Spec(), {"channels": ["PS"]}, None, {"initial_current_a": 30.0})
    assert isinstance(emu, IPS120) and emu.output == emu.magnet == 30.0
