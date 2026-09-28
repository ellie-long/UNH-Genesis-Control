from __future__ import annotations

import unittest
from pathlib import Path

from logic_devices.ssrf_sequence_core import BurnParameters
from logic_devices.ssrf_sequence_plan import (
    build_prepare_cleanup_plan,
    build_prepare_plan,
    build_prepared_burn_plan,
)


class TestFinalPreparePlan(unittest.TestCase):
    def setUp(self):
        self.p = BurnParameters(
            carrier_mhz=33.0,
            start_level_dbm=-120.0,
            target_level_dbm=-22.0,
            fm_deviation_khz=10.0,
            fm_period_s=0.010,
            vhigh_v=0.7,
            vlow_v=-0.7,
            fm_repeats=10,
        )

    def test_prepare_loads_waveform_but_never_enables_energy_or_moves_switches(self):
        plan = build_prepare_plan(self.p, 8192, 4)
        command_pvs = [s.command_pv for s in plan if s.command_pv]
        self.assertIn("UNH:GEN:SSRF:WGEN:LOAD_CMD", command_pvs)
        self.assertNotIn("UNH:GEN:SSRF:WGEN:OUTPUT_CMD", command_pvs)
        self.assertNotIn("UNH:GEN:SSRF:GEN:RF_CMD", command_pvs)
        self.assertNotIn("UNH:GEN:SSRF:GEN:FM_CMD", command_pvs)
        self.assertNotIn("UNH:GEN:SSRF:NMR_SW:PORT_CMD", command_pvs)
        self.assertNotIn("UNH:GEN:SSRF:RF_SW:PORT_CMD", command_pvs)

    def test_prepared_burn_does_not_reload_or_reconfigure_recipe(self):
        plan = build_prepared_burn_plan(self.p, 8192, 5)
        command_pvs = [s.command_pv for s in plan if s.command_pv]
        self.assertNotIn("UNH:GEN:SSRF:WGEN:LOAD_CMD", command_pvs)
        self.assertNotIn("UNH:GEN:SSRF:WGEN:PERIOD_CMD", command_pvs)
        self.assertNotIn("UNH:GEN:SSRF:GEN:FREQ_CMD", command_pvs)
        self.assertIn("UNH:GEN:SSRF:GEN:RF_CMD", command_pvs)
        self.assertIn("UNH:GEN:SSRF:GEN:SWEEP_TRIGGER_CMD", command_pvs)

    def test_prepared_burn_retains_common_cleanup(self):
        names = [s.name for s in build_prepared_burn_plan(self.p, 8192, 5)]
        self.assertIn("cleanup_rf_off", names)
        self.assertIn("cleanup_fm_off", names)
        self.assertIn("cleanup_wavegen_off", names)
        self.assertIn("cleanup_disconnect_ssrf", names)
        self.assertIn("cleanup_restore_nmr", names)

    def test_prepare_recovery_never_moves_switches(self):
        pvs = [s.command_pv for s in build_prepare_cleanup_plan() if s.command_pv]
        self.assertNotIn("UNH:GEN:SSRF:NMR_SW:PORT_CMD", pvs)
        self.assertNotIn("UNH:GEN:SSRF:RF_SW:PORT_CMD", pvs)


class TestRecipeIocStatic(unittest.TestCase):
    def test_analysis_ioc_has_no_direct_hardware_command_authority(self):
        text = (
            Path(__file__).resolve().parents[1]
            / "ssrf_analysis"
            / "burn_recipe_ioc.py"
        ).read_text()
        self.assertIn("build_waveform_plan", text)
        self.assertIn("WAVEFORM_STAGE_ID", text)
        self.assertIn("PREPARE_CMD", text)
        for forbidden in (
            "UNH:GEN:SSRF:GEN:RF_CMD",
            "UNH:GEN:SSRF:GEN:FM_CMD",
            "UNH:GEN:SSRF:WGEN:OUTPUT_CMD",
            "UNH:GEN:SSRF:NMR_SW:PORT_CMD",
            "UNH:GEN:SSRF:RF_SW:PORT_CMD",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
