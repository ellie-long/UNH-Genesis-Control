"""Static integration contract for the Genesis target/Cryo port.

These tests intentionally avoid hardware communication.  They verify that the
ported Farooq target-device capability fits the standalone Genesis repository,
that physical profiles default safe/read-only, and that subsystem-owned RF
drivers have not been duplicated.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "target_ioc" / "instruments"
PROFILE = ROOT / "configs" / "target_physical"


PORTED_DRIVERS = {
    "alicat_flow.py",
    "ami136.py",
    "bga244.py",
    "cm4g_magnet.py",
    "cs4_magnet.py",
    "dat8017.py",
    "dat8017_level.py",
    "dat8018.py",
    "dat8024.py",
    "dat8130.py",
    "dp832.py",
    "eip_counter.py",
    "elva1_powmeter.py",
    "feedforward.py",
    "hfc300.py",
    "ioc_load.py",
    "ionivac.py",
    "lm500.py",
    "ls218.py",
    "ls224.py",
    "ls336.py",
    "ls455.py",
    "mks902b.py",
    "mks937b.py",
    "pid.py",
    "pid_controller.py",
    "si9700.py",
    "spd1000x.py",
    "tpg_26x.py",
    "tpg262.py",
    "zaber_motor.py",
}

SUBSYSTEM_OWNED = {
    "smc100a.py",
    "minicircuits_switch.py",
    "vaunix_lms.py",
}

PHYSICAL = {
    "siglent_coldboard": {
        "prefix": "UNH:GEN:CRYO:COLDBOARD",
        "module": "target_ioc.instruments.spd1000x",
        "ca": 5111,
        "pva": 5211,
    },
    "mks902b": {
        "prefix": "UNH:GEN:CRYO:CELL",
        "module": "target_ioc.instruments.mks902b",
        "ca": 5112,
        "pva": 5212,
    },
    "tpg262": {
        "prefix": "UNH:GEN:CRYO:VAC",
        "module": "target_ioc.instruments.tpg262",
        "ca": 5113,
        "pva": 5213,
    },
    "ionivac": {
        "prefix": "UNH:GEN:CRYO:ION",
        "module": "target_ioc.instruments.ionivac",
        "ca": 5114,
        "pva": 5214,
    },
    "ls455": {
        "prefix": "UNH:GEN:CRYO:FIELD",
        "module": "target_ioc.instruments.ls455",
        "ca": 5115,
        "pva": 5215,
    },
    "ls224_fridge": {
        "prefix": "UNH:GEN:CRYO:FRIDGE",
        "module": "target_ioc.instruments.ls224",
        "ca": 5116,
        "pva": 5216,
    },
    "ls224_magnet": {
        "prefix": "UNH:GEN:CRYO:MAG",
        "module": "target_ioc.instruments.ls224",
        "ca": 5117,
        "pva": 5217,
    },
}


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text())


def function_source(path: Path, function_name: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    found = []

    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ):
            found.append(ast.get_source_segment(text, node) or "")

    return found


def test_expected_farooq_target_drivers_are_preserved():
    actual = {
        path.name
        for path in TARGET.glob("*.py")
        if path.name != "__init__.py"
    }

    assert PORTED_DRIVERS <= actual
    assert not (SUBSYSTEM_OWNED & actual)


def test_old_devices_package_imports_are_not_reintroduced():
    paths = list(TARGET.glob("*.py")) + [
        ROOT / "genesis_common" / "modbus_base.py",
        ROOT / "genesis_common" / "serial_tcp_base.py",
        ROOT / "genesis_common" / "telnet_base.py",
        ROOT / "genesis_common" / "telnet_socket.py",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")

        assert "from devices" not in text, path
        assert "import devices" not in text, path


def test_confirmed_physical_profiles_are_read_only():
    for name, expected in PHYSICAL.items():
        path = PROFILE / name / "settings.yaml"

        assert path.is_file(), path

        data = load_yaml(path)

        assert data["general"]["prefix"] == expected["prefix"]

        cfg = data[name]

        assert cfg["module"] == expected["module"]
        assert cfg["write_enabled"] is False


def test_ls224_calibration_assets_exist():
    for name in ("ls224_fridge", "ls224_magnet"):
        cfg = load_yaml(
            PROFILE / name / "settings.yaml"
        )[name]

        calibration = ROOT / cfg["calibration"]

        assert calibration.is_file(), calibration


def test_cryo_manager_contract():
    manager = load_yaml(
        PROFILE / "ioc_manager" / "settings.yaml"
    )

    general = manager["general"]

    assert general["prefix"] == "UNH:GEN:CRYO"
    assert general["manager_ca_server_port"] == 5110
    assert general["manager_pva_server_port"] == 5210

    ca_ports = {general["manager_ca_server_port"]}
    pva_ports = {general["manager_pva_server_port"]}

    logic_owners = {
        "alarm_summary",
        "safety_interlock",
        "target_status",
        "save_restore",
        "archiver",
    }

    assert set(manager) - {"general"} == (
        set(PHYSICAL) | logic_owners
    )

    for name, expected in PHYSICAL.items():
        cfg = manager[name]

        assert cfg["autostart"] is False
        assert cfg["ioc"] == name

        assert cfg["settings_folder"] == (
            f"configs/target_physical/{name}"
        )

        assert cfg["heartbeat_pv"] == (
            f"{expected['prefix']}:MAN:{name}_time"
        )

        ca = cfg["ca_server_port"]
        pva = cfg["pva_server_port"]

        assert ca == expected["ca"]
        assert pva == expected["pva"]

        assert ca not in ca_ports
        assert pva not in pva_ports

        ca_ports.add(ca)
        pva_ports.add(pva)


def test_every_do_sets_hardware_callback_uses_genesis_write_gate():
    found = 0

    for path in sorted(TARGET.glob("*.py")):
        for source in function_source(path, "do_sets"):
            found += 1
            assert "can_write(" in source, path

    assert found > 0


def test_zaber_named_position_callback_is_write_gated():
    sources = function_source(
        TARGET / "zaber_motor.py",
        "set_position",
    )

    assert len(sources) == 1
    assert "can_write(" in sources[0]


def test_feedforward_external_caput_requires_write_enabled():
    text = (
        TARGET / "feedforward.py"
    ).read_text(encoding="utf-8")

    assert "aioca.caput(self.output_pv" in text
    assert 'self.settings.get("write_enabled", False)' in text


def test_target_runtime_dependencies_are_declared():
    requirements = (
        ROOT / "requirements.txt"
    ).read_text().lower()

    for dependency in (
        "pymodbustcp",
        "simple-pid",
        "psutil",
        "alicat",
        "zaber-motion",
    ):
        assert dependency in requirements


def test_python313_telnet_compatibility_does_not_import_telnetlib():
    paths = [
        ROOT / "genesis_common" / "telnet_base.py",
        ROOT / "genesis_common" / "telnet_socket.py",
    ]

    for path in paths:
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name != "telnetlib"
                    for alias in node.names
                )

            if isinstance(node, ast.ImportFrom):
                assert node.module != "telnetlib"
