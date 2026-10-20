"""Optional Cryo IOC registration; no runtime or hardware access."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]

OPTIONAL = {
    "flowmeter_pi": (5123, 5223, "UNH:GEN:CRYO:FLOW:RIGHT"),
    "flowmeter_left": (5124, 5224, "UNH:GEN:CRYO:FLOW:LEFT"),
    "flowmeter_sep": (5125, 5225, "UNH:GEN:CRYO:FLOW:SEP"),
    "lhe_fridge": (5126, 5226, "UNH:GEN:CRYO:FRIDGE:LHE"),
    "lhe_magnet": (5127, 5227, "UNH:GEN:CRYO:MAG:LHE"),
    "manometer_low": (5128, 5228, "UNH:GEN:CRYO:VAC:LOW"),
}


def test_optional_ioc_registration():
    manager = yaml.safe_load((
        ROOT / "configs/target_physical/ioc_manager/settings.yaml"
    ).read_text())

    assert len(manager) == 20
    for name, (ca, pva, prefix) in OPTIONAL.items():
        entry = manager[name]
        assert entry["autostart"] is False
        assert entry["ca_server_port"] == ca
        assert entry["pva_server_port"] == pva
        assert entry["heartbeat_pv"] == f"{prefix}:MAN:{name}_time"

        settings = yaml.safe_load((
            ROOT / entry["settings_folder"] / "settings.yaml"
        ).read_text())

        assert settings["general"]["prefix"] == prefix
        assert settings[name]["write_enabled"] is False
        assert settings[name]["autostart"] is False

    ca = [v["ca_server_port"] for k, v in manager.items()
          if k != "general"]
    pva = [v["pva_server_port"] for k, v in manager.items()
           if k != "general"]
    assert len(ca) == len(set(ca)) == 19
    assert len(pva) == len(set(pva)) == 19


def test_discovery_includes_all_registered_iocs():
    manager = yaml.safe_load((
        ROOT / "configs/target_physical/ioc_manager/settings.yaml"
    ).read_text())

    addresses = manager["general"]["epics_addr_list"].split()
    expected = [f"127.0.0.1:{port}" for port in range(5111, 5129)] + ["127.0.0.1:5129"]
    assert addresses == expected

    text = (
        ROOT / "configs/phoebus/target_physical.ini"
    ).read_text()

    key = "org.phoebus.pv.ca/addr_list="
    lines = [line for line in text.splitlines()
             if line.startswith(key)]
    assert len(lines) == 1
    assert lines[0][len(key):].split() == expected


def test_optional_devices_excluded_from_default_lifecycle():
    script = (ROOT / "scripts/cryo_physical.sh").read_text()

    for array in ("NAMES", "START_ORDER", "STOP_ORDER"):
        match = re.search(
            rf"^{array}=\(\n(.*?)^\)",
            script,
            re.DOTALL | re.MULTILINE,
        )
        assert match is not None, array

        names = set(match.group(1).split())
        assert len(names) == 13, array
        assert not names.intersection(OPTIONAL), array

    # Static preflight must nevertheless cover optional devices.
    assert "physical = expected[:8] + optional" in script
    assert "expected = expected + optional" in script
