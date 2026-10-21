from pathlib import Path
import ast
import unittest

import yaml

from archive.viewer_filters import (
    CRYO_GROUP_OPTIONS,
    CRYO_PRESETS,
    cryo_group_for_pv,
    filter_pvs,
    resolve_cryo_preset,
    subsystem_for_pv,
)


ROOT = Path(__file__).resolve().parents[1]

ARCHIVER = (
    ROOT
    / "configs"
    / "target_physical"
    / "archiver"
    / "settings.yaml"
)

MANAGER = (
    ROOT
    / "configs"
    / "target_physical"
    / "ioc_manager"
    / "settings.yaml"
)


class CryoArchiverViewerContract(unittest.TestCase):

    def test_archiver_profile(self):
        cfg = yaml.safe_load(
            ARCHIVER.read_text()
        )

        arch = cfg["archiver"]

        self.assertEqual(
            arch["module"],
            "logic_devices.archiver",
        )

        self.assertFalse(
            arch["autostart"]
        )

        self.assertEqual(
            arch["archive_path"],
            "archive/subsystems/pv_csv",
        )

        self.assertGreaterEqual(
            len(arch["explicit_pvs"]),
            130,
        )

        self.assertFalse(
            any(
                pv.startswith(
                    "UNH:GEN:CRYO:ARCH:"
                )
                for pv in arch["explicit_pvs"]
            )
        )

    def test_manager_archiver_ports(self):
        cfg = yaml.safe_load(
            MANAGER.read_text()
        )

        arch = cfg["archiver"]

        self.assertEqual(
            arch["ca_server_port"],
            5122,
        )

        self.assertEqual(
            arch["pva_server_port"],
            5222,
        )

        self.assertFalse(
            arch["autostart"]
        )

    def test_cryo_subsystem_classification(self):
        self.assertEqual(
            subsystem_for_pv(
                "UNH:GEN_CRYO_FRIDGE_F1_TI"
            ),
            "Cryo",
        )

    def test_cryo_groups(self):
        expected = {
            "UNH:GEN_CRYO_FRIDGE_F1_TI":
                "Fridge",
            "UNH:GEN_CRYO_MAG_M4_TI":
                "Magnet",
            "UNH:GEN_CRYO_VAC_OVC_PI":
                "Vacuum",
            "UNH:GEN_CRYO_CELL_Cell_PI":
                "Cell",
            "UNH:GEN_CRYO_ION_Ion_PI":
                "Ion Gauge",
            "UNH:GEN_CRYO_FIELD_Fringe_FieldT":
                "Fringe Field",
            "UNH:GEN_CRYO_COLDBOARD_Coldboard_PI":
                "Cold Board",
            "UNH:GEN_CRYO_SAFE_Safe_Permit":
                "Safety",
            "UNH:GEN_CRYO_ALARM_Alarm_Count":
                "Alarms",
            "UNH:GEN_CRYO_STATE_production":
                "Target State",
        }

        for pv, group in expected.items():
            self.assertEqual(
                cryo_group_for_pv(pv),
                group,
            )

    def test_cryo_filter(self):
        pvs = [
            "UNH:GEN_CRYO_FRIDGE_F1_TI",
            "UNH:GEN_CRYO_MAG_M1_TI",
            "UNH:GEN_MW_PS_VMEAS",
        ]

        self.assertEqual(
            filter_pvs(
                pvs,
                subsystem="Cryo",
                cryo_group="Fridge",
            ),
            [
                "UNH:GEN_CRYO_FRIDGE_F1_TI",
            ],
        )

    def test_cryo_presets_never_exceed_four(self):
        available = [
            value
            for values in CRYO_PRESETS.values()
            for value in values
        ]

        for name in CRYO_PRESETS:
            self.assertLessEqual(
                len(
                    resolve_cryo_preset(
                        available,
                        name,
                    )
                ),
                4,
            )

    def test_cryo_group_selector_exists(self):
        self.assertIn(
            "All Cryo",
            CRYO_GROUP_OPTIONS,
        )

    def test_archiver_exposes_write_counter(self):
        source = (
            ROOT
            / "logic_devices"
            / "archiver.py"
        ).read_text()

        ast.parse(source)

        self.assertIn(
            "Archive_Writes",
            source,
        )


if __name__ == "__main__":
    unittest.main()
