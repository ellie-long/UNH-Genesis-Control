from pathlib import Path
import unittest
import yaml


ROOT = Path(__file__).resolve().parents[1]


class MicrowavePhaseICloseout(unittest.TestCase):

    def test_normal_startup_is_conservative(self):
        text = (ROOT / "scripts/start_genesis_mac.sh").read_text()
        self.assertIn('start_managed_ioc "mw_spd1305x"', text)
        for name in ("mw_vaunix_lms", "mw_supervisor", "mw_pcl602"):
            self.assertNotIn(f'start_managed_ioc "{name}"', text)

    def test_shutdown_knows_full_microwave_stack(self):
        text = (ROOT / "scripts/stop_genesis_mac.sh").read_text()
        required = [
            'stop_managed_ioc "mw_pcl602"',
            'stop_managed_ioc "mw_supervisor"',
            'stop_managed_ioc "mw_vaunix_lms"',
            'stop_managed_ioc "mw_spd1305x"',
            'stop_managed_ioc "archiver"',
        ]
        positions = [text.index(item) for item in required]
        self.assertEqual(positions, sorted(positions))

    def test_shutdown_checks_rf_and_robot_state(self):
        text = (ROOT / "scripts/stop_genesis_mac.sh").read_text()
        for pv in (
            "UNH:GEN:MW:SUP:RFEnable",
            "UNH:GEN:MW:SRC:MW_RF",
            "UNH:GEN:MW:ROBOT:X_BUSY",
            "UNH:GEN:MW:ROBOT:Y_BUSY",
            "UNH:GEN:MW:ROBOT:X_MOTOR_ENABLE",
            "UNH:GEN:MW:ROBOT:Y_MOTOR_ENABLE",
        ):
            self.assertIn(pv, text)

    def test_physical_iocs_not_autostarted(self):
        with (ROOT / "configs/ioc_manager/settings.yaml").open() as stream:
            cfg = yaml.safe_load(stream)
        for name in (
            "mw_spd1305x",
            "mw_vaunix_lms",
            "mw_supervisor",
            "mw_pcl602",
        ):
            self.assertFalse(cfg[name]["autostart"])

    def test_supervisor_default_remains_simulation(self):
        with (ROOT / "configs/microwave/supervisor/settings.yaml").open() as stream:
            cfg = yaml.safe_load(stream)
        self.assertTrue(cfg["microwave_supervisor"]["simulation"])

    def test_current_vaunix_range(self):
        with (ROOT / "configs/microwave/vaunix_lms/settings.yaml").open() as stream:
            cfg = yaml.safe_load(stream)
        dev = cfg["vaunix_lms"]
        self.assertAlmostEqual(
            dev["frequency_min_ghz"] * dev["multiplier"], 138.0, places=6
        )
        self.assertAlmostEqual(
            dev["frequency_max_ghz"] * dev["multiplier"], 142.0, places=6
        )

    def test_genesisctl_has_microwave_regression(self):
        text = (ROOT / "scripts/genesisctl.sh").read_text()
        self.assertIn("test-mw", text)
        self.assertIn("test_mw()", text)

    def test_closeout_document_records_manual_safety(self):
        text = (ROOT / "docs/MICROWAVE_PHASE1_CLOSEOUT.md").read_text()
        for phrase in (
            "VDI green light",
            "SIG",
            "138 to 142 GHz",
            "verified home reference",
            "travel limits",
            "robot-position visualization",
        ):
            self.assertIn(phrase, text)

    def test_imported_readme_no_longer_uses_old_namespace(self):
        text = (ROOT / "logic_devices/microwave_supervisor/README.md").read_text()
        self.assertNotIn("P=MYLAB", text)
        self.assertNotIn("134--142", text)
        self.assertIn("138 to 142 GHz", text)


if __name__ == "__main__":
    unittest.main()
