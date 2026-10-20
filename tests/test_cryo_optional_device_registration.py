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


def test_commissioned_cryo_lifecycle_membership():
    script = (ROOT / "scripts/cryo_physical.sh").read_text()

    original = {
        "siglent_coldboard",
        "mks902b",
        "tpg262",
        "ionivac",
        "ionivac_pump",
        "ls455",
        "ls224_fridge",
        "ls224_magnet",
        "safety_interlock",
        "target_status",
        "save_restore",
        "archiver",
        "alarm_summary",
    }

    commissioned = set(OPTIONAL)

    assert commissioned == {
        "flowmeter_pi",
        "flowmeter_left",
        "flowmeter_sep",
        "lhe_fridge",
        "lhe_magnet",
        "manometer_low",
    }

    expected = original | commissioned

    for array in ("NAMES", "START_ORDER", "STOP_ORDER"):
        match = re.search(
            rf"^{array}=\(\n(.*?)^\)",
            script,
            re.DOTALL | re.MULTILINE,
        )
        assert match is not None, array

        entries = match.group(1).split()

        assert len(entries) == 19, array
        assert len(entries) == len(set(entries)), array
        assert set(entries) == expected, array

        # All registered Cryo devices are lifecycle members.
        assert commissioned <= set(entries), array

    # Startup and shutdown retain their intended ordering.
    start = re.search(
        r"^START_ORDER=\(\n(.*?)^\)",
        script,
        re.DOTALL | re.MULTILINE,
    ).group(1).split()

    stop = re.search(
        r"^STOP_ORDER=\(\n(.*?)^\)",
        script,
        re.DOTALL | re.MULTILINE,
    ).group(1).split()

    assert start.index("flowmeter_left") < start.index("safety_interlock")
    assert start.index("flowmeter_sep") < start.index("safety_interlock")
    assert stop.index("flowmeter_left") < stop.index("ls224_magnet")
    assert stop.index("flowmeter_sep") < stop.index("ls224_magnet")

    # Static preflight still validates all registered devices.
    assert "physical = expected[:8] + optional" in script
    assert "expected = expected + optional" in script
