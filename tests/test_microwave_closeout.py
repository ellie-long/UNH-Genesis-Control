from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]

PHYSICAL_SCRIPT = ROOT / "scripts" / "microwave_physical.sh"

PHYSICAL_MANAGER = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "ioc_manager"
    / "settings.yaml"
)

PHYSICAL_SUPERVISOR = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "supervisor"
    / "settings.yaml"
)

PHYSICAL_VAUNIX = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "vaunix_lms"
    / "settings.yaml"
)

PHYSICAL_SPD = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "spd1305x"
    / "settings.yaml"
)

PHYSICAL_ROBOT = (
    ROOT
    / "configs"
    / "commissioning"
    / "microwave_physical"
    / "pcl602"
    / "settings.yaml"
)


def load(file_path):
    with file_path.open() as stream:
        return yaml.safe_load(stream)


class MicrowavePhaseICloseout(unittest.TestCase):

    def test_physical_launcher_uses_canonical_profile(self):
        text = PHYSICAL_SCRIPT.read_text()

        self.assertIn(
            'PROFILE="${ROOT}/configs/commissioning/microwave_physical"',
            text,
        )

        self.assertIn(
            'python "$ROOT/tools/microwave_shutdown.py"',
            text,
        )

        self.assertIn(
            "./scripts/start_microwave_phoebus.sh",
            text,
        )

    def test_physical_start_preserves_required_hardware_order(self):
        text = PHYSICAL_SCRIPT.read_text()

        start = text.index("start_stack() {")
        stop = text.index("stop_stack() {", start)
        block = text[start:stop]

        required = [
            "start_manager",
            "manager_put mw_spd1305x 1",
            "set_uca_safe",
            "Type READY after the physical USB/VDI sequence is satisfied",
            "check_vaunix_service",
            "manager_put mw_vaunix_lms 1",
            "manager_put mw_pcl602 1",
            "manager_put mw_supervisor 1",
            "prepare_source",
            "manager_put archiver 1",
            "enable_archiver",
            "status_stack",
            "rf_ready",
        ]

        positions = [block.index(item) for item in required]

        self.assertEqual(
            positions,
            sorted(positions),
        )

    def test_startup_documents_vdi_before_usb_invariant(self):
        text = PHYSICAL_SCRIPT.read_text()

        self.assertIn(
            "VDI-175T green light ON",
            text,
        )

        self.assertIn(
            "UCA already at 5 V",
            text,
        )

        self.assertIn(
            "USB connected only after the above state",
            text,
        )

    def test_physical_iocs_require_explicit_start(self):
        cfg = load(PHYSICAL_MANAGER)

        for name in (
            "mw_spd1305x",
            "mw_vaunix_lms",
            "mw_supervisor",
            "mw_pcl602",
            "archiver",
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    cfg[name]["autostart"]
                )

    def test_physical_profiles_are_not_simulation(self):
        supervisor = load(PHYSICAL_SUPERVISOR)
        vaunix = load(PHYSICAL_VAUNIX)
        robot = load(PHYSICAL_ROBOT)

        self.assertFalse(
            supervisor["microwave_supervisor"]["simulation"]
        )

        self.assertFalse(
            vaunix["vaunix_lms"]["simulation"]
        )

        self.assertFalse(
            robot["pcl602"]["simulation"]
        )

    def test_physical_uca_safety_configuration(self):
        cfg = load(PHYSICAL_SPD)
        dev = cfg["spd1305x"]

        self.assertEqual(
            dev["ip"],
            "192.168.1.19",
        )

        self.assertEqual(
            dev["port"],
            5025,
        )

        self.assertTrue(
            dev["write_enabled"]
        )

        self.assertFalse(
            dev["initialize_on_connect"]
        )

        self.assertAlmostEqual(
            dev["max_voltage"],
            5.0,
        )

        self.assertAlmostEqual(
            dev["max_current"],
            0.1,
        )

    def test_current_vaunix_physical_range(self):
        cfg = load(PHYSICAL_VAUNIX)
        dev = cfg["vaunix_lms"]

        self.assertAlmostEqual(
            dev["frequency_min_ghz"] * dev["multiplier"],
            138.0,
            places=6,
        )

        self.assertAlmostEqual(
            dev["frequency_max_ghz"] * dev["multiplier"],
            142.0,
            places=6,
        )

        self.assertAlmostEqual(
            dev["power_min_dbm"],
            -40.0,
        )

        self.assertAlmostEqual(
            dev["power_max_dbm"],
            10.0,
        )

    def test_robot_physical_profile_requires_speed_profiles(self):
        cfg = load(PHYSICAL_ROBOT)
        dev = cfg["pcl602"]

        self.assertEqual(
            dev["ip"],
            "mmWaveRobot.local",
        )

        self.assertEqual(
            dev["port"],
            1702,
        )

        self.assertTrue(
            dev["require_speed_profiles"]
        )

    def test_genesisctl_has_microwave_regression(self):
        text = (ROOT / "scripts/genesisctl.sh").read_text()

        self.assertIn(
            "test-mw",
            text,
        )

        self.assertIn(
            "test_mw()",
            text,
        )

    def test_closeout_document_retains_manual_safety_record(self):
        text = (
            ROOT
            / "docs"
            / "MICROWAVE_PHASE1_CLOSEOUT.md"
        ).read_text()

        for phrase in (
            "VDI green light",
            "SIG",
            "138 to 142 GHz",
            "verified home reference",
            "travel limits",
            "robot-position visualization",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(
                    phrase,
                    text,
                )

    def test_supervisor_readme_uses_genesis_namespace_and_range(self):
        text = (
            ROOT
            / "logic_devices"
            / "microwave_supervisor"
            / "README.md"
        ).read_text()

        self.assertNotIn(
            "P=MYLAB",
            text,
        )

        self.assertNotIn(
            "134--142",
            text,
        )

        self.assertIn(
            "UNH:GEN",
            text,
        )

        self.assertIn(
            "138 to 142 GHz",
            text,
        )


if __name__ == "__main__":
    unittest.main()
