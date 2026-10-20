from pathlib import Path
import xml.etree.ElementTree as ET
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


EXPECTED_PORTS = {
    "siglent_coldboard": (5111, 5211),
    "mks902b": (5112, 5212),
    "tpg262": (5113, 5213),
    "ionivac": (5114, 5214),
    "ionivac_pump": (5129, 5229),
    "ls455": (5115, 5215),
    "ls224_fridge": (5116, 5216),
    "ls224_magnet": (5117, 5217),
    "alarm_summary": (5118, 5218),
    "safety_interlock": (5119, 5219),
    "target_status": (5120, 5220),
    "save_restore": (5121, 5221),
    "archiver": (5122, 5222),
}


class CryoOperatorWorkflowStatic(unittest.TestCase):

    def test_runtime_files_exist(self):
        required = (
            "scripts/cryo_physical.sh",
            "scripts/start_cryo_phoebus.sh",
            "configs/phoebus/target_physical.ini",
            "gui/genesis_cryo.bob",
            "gui/genesis_cryo_safety.bob",
        )

        for rel in required:
            self.assertTrue(
                (ROOT / rel).is_file(),
                rel,
            )

    def test_manager_port_contract(self):
        manager = yaml.safe_load(
            (
                ROOT
                / "configs"
                / "target_physical"
                / "ioc_manager"
                / "settings.yaml"
            ).read_text()
        )

        self.assertEqual(
            manager["general"]["manager_ca_server_port"],
            5110,
        )

        self.assertEqual(
            manager["general"]["manager_pva_server_port"],
            5210,
        )

        for name, ports in EXPECTED_PORTS.items():
            self.assertIn(
                name,
                manager,
            )

            cfg = manager[name]

            self.assertFalse(
                cfg["autostart"],
                name,
            )

            self.assertEqual(
                cfg["ca_server_port"],
                ports[0],
                name,
            )

            self.assertEqual(
                cfg["pva_server_port"],
                ports[1],
                name,
            )

    def test_physical_profiles_start_read_only(self):
        manager = yaml.safe_load(
            (
                ROOT
                / "configs"
                / "target_physical"
                / "ioc_manager"
                / "settings.yaml"
            ).read_text()
        )

        physical = (
            "siglent_coldboard",
            "mks902b",
            "tpg262",
            "ionivac",
            "ionivac_pump",
            "ls455",
            "ls224_fridge",
            "ls224_magnet",
        )

        for name in physical:
            cfg = manager[name]

            settings_path = (
                ROOT
                / cfg["settings_folder"]
                / "settings.yaml"
            )

            settings = yaml.safe_load(
                settings_path.read_text()
            )

            ioc_name = cfg.get(
                "ioc",
                name,
            )

            self.assertIs(
                settings[ioc_name]["write_enabled"],
                False,
                name,
            )

    def test_target_state_is_advisory(self):
        settings = yaml.safe_load(
            (
                ROOT
                / "configs"
                / "target_physical"
                / "target_status"
                / "settings.yaml"
            ).read_text()
        )

        cfg = settings["target_status"]

        self.assertIs(
            cfg["state_table_validated"],
            False,
        )

        self.assertIs(
            cfg["apply_enabled"],
            False,
        )

        source = (
            ROOT
            / "logic_devices"
            / "target_status.py"
        ).read_text()

        self.assertIn(
            "if not self.state_table_validated or not self.apply_enabled",
            source,
        )

        self.assertIn(
            "state table unvalidated",
            source,
        )

    def test_state_gui_controls_are_read_only(self):
        root = ET.parse(
            ROOT
            / "gui"
            / "genesis_cryo_safety.bob"
        ).getroot()

        state_pvs = {
            "ca://UNH:GEN:CRYO:STATE:status",
            "ca://UNH:GEN:CRYO:STATE:material",
            "ca://UNH:GEN:CRYO:STATE:auto",
        }

        seen = set()

        for widget in root.findall(
            ".//widget"
        ):
            pv = widget.findtext(
                "pv_name",
                "",
            )

            if pv in state_pvs:
                seen.add(pv)

                self.assertEqual(
                    widget.get("type"),
                    "textupdate",
                    pv,
                )

        self.assertEqual(
            seen,
            state_pvs,
        )

        for action in root.findall(
            ".//action[@type='write_pv']"
        ):
            self.assertNotIn(
                action.findtext(
                    "pv_name",
                    "",
                ),
                state_pvs,
            )

    def test_save_restore_enable_restore_fails_closed(self):
        source = (
            ROOT
            / "logic_devices"
            / "save_restore.py"
        ).read_text()

        start = source.index(
            "async def _interlock_clear"
        )

        end = source.index(
            "async def restore"
        )

        block = source[start:end]

        self.assertIn(
            "return False",
            block,
        )

        self.assertNotIn(
            "no interlock IOC running\n            return True",
            block,
        )

    def test_pid_is_not_standard_physical_owner(self):
        manager_text = (
            ROOT
            / "configs"
            / "target_physical"
            / "ioc_manager"
            / "settings.yaml"
        ).read_text()

        lifecycle = (
            ROOT
            / "scripts"
            / "cryo_physical.sh"
        ).read_text()

        self.assertNotIn(
            "pid_coldboard",
            manager_text,
        )

        self.assertNotIn(
            "logic_devices.pid",
            manager_text,
        )

        self.assertNotIn(
            "pid_coldboard",
            lifecycle,
        )

    def test_lifecycle_has_duplicate_owner_and_shutdown_guards(self):
        source = (
            ROOT
            / "scripts"
            / "cryo_physical.sh"
        ).read_text()

        for token in (
            "legacy_owner_guard",
            "WRITE_ENABLED",
            "Coldboard_Mode",
            "Archive_Enable",
            "require_physical_host",
            "genesis-cryo-",
        ):
            self.assertIn(
                token,
                source,
            )

    def test_phoebus_ca_surface(self):
        profile = (
            ROOT
            / "configs"
            / "phoebus"
            / "target_physical.ini"
        ).read_text()

        for port in range(
            5111,
            5123,
        ):
            self.assertIn(
                f"127.0.0.1:{port}",
                profile,
            )

        self.assertIn(
            "org.phoebus.pv/default=ca",
            profile,
        )

    def test_genesisctl_exposes_cryo_commands_once(self):
        source = (
            ROOT
            / "scripts"
            / "genesisctl.sh"
        ).read_text()

        for command in (
            "cryo-start",
            "cryo-status",
            "cryo-stop",
            "cryo-phoebus",
            "cryo-viewer",
        ):
            self.assertEqual(
                source.count(
                    f"    {command})"
                ),
                1,
                command,
            )

    def test_gui_generator_has_no_developer_path(self):
        source = (
            ROOT
            / "tools"
            / "make_cryo_screens.py"
        ).read_text()

        self.assertNotIn(
            "/Users/shrestha",
            source,
        )

        self.assertIn(
            "GENESIS_FAROOQ_GUI_SOURCE",
            source,
        )


if __name__ == "__main__":
    unittest.main()
