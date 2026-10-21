"""Static regression checks for optional Cryo archiving."""

from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

DEVICES = (
    "flowmeter_pi",
    "flowmeter_left",
    "flowmeter_sep",
    "lhe_fridge",
    "lhe_magnet",
    "manometer_low",
)


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def test_optional_archive_inventory():
    manager = load(
        "configs/target_physical/ioc_manager/settings.yaml"
    )
    archive = load(
        "configs/target_physical/archiver/settings.yaml"
    )

    pvs = archive["archiver"]["explicit_pvs"]

    assert len(pvs) == len(set(pvs)) == 168
    assert archive["archiver"]["autostart"] is False
    assert archive["archiver"]["module"] == "logic_devices.archiver"

    new_pvs = []
    for name in DEVICES:
        entry = manager[name]
        profile = load(
            f"{entry['settings_folder']}/settings.yaml"
        )

        prefix = profile["general"]["prefix"]
        device = profile[name]

        assert entry["autostart"] is False
        assert device["write_enabled"] is False

        new_pvs.extend(
            f"{prefix}:{record}"
            for record in device["records"]
        )
        new_pvs.append(entry["heartbeat_pv"])

    assert len(new_pvs) == 28
    assert pvs[-28:] == new_pvs
    assert len(pvs[:-28]) == 140

    assert not any(
        pv.startswith("UNH:GEN:CRYO:ARCH:")
        for pv in pvs
    )


def test_optional_archive_discovery():
    manager = load(
        "configs/target_physical/ioc_manager/settings.yaml"
    )
    archive = load(
        "configs/target_physical/archiver/settings.yaml"
    )

    addresses = archive["general"]["epics_addr_list"].split()

    expected = [
        f"127.0.0.1:{port}"
        for port in range(5111, 5122)
    ] + [
        f"127.0.0.1:{manager[name]['ca_server_port']}"
        for name in DEVICES
    ]

    expected += ["127.0.0.1:5129"]
    assert addresses == expected
    assert len(addresses) == len(set(addresses)) == 18
    assert "127.0.0.1:5122" not in addresses
