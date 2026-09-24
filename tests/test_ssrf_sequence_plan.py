import unittest

from logic_devices.ssrf_sequence_core import (
    BurnParameters,
)

from logic_devices.ssrf_sequence_plan import (
    ILK,
    NMR_SW,
    OP_BEST_EFFORT_WRITE,
    OP_SAFE_VOLTAGE_WINDOW,
    OP_TRIGGER,
    OP_WAIT,
    SMC,
    SSRF_SW,
    WGEN,
    build_burn_plan,
    build_cleanup_plan,
)


def default_parameters():
    return BurnParameters(
        carrier_mhz=33.0,
        start_level_dbm=-120.0,
        target_level_dbm=-22.0,
        fm_deviation_khz=10.0,
        fm_period_s=0.010,
        vhigh_v=1.0,
        vlow_v=-1.0,
        fm_repeats=10,
    )


def by_name(plan):
    return {
        step.name: step
        for step in plan
    }


class TestSequencePlan(unittest.TestCase):

    def test_default_plan_builds(self):
        plan = build_burn_plan(
            default_parameters(),
            waveform_points=100,
            waveform_id_before=7,
        )

        self.assertGreater(
            len(plan),
            20,
        )

    def test_parameter_mapping(self):
        steps = by_name(
            build_burn_plan(
                default_parameters(),
                waveform_points=100,
                waveform_id_before=7,
            )
        )

        self.assertEqual(
            steps[
                "configure_sweep_step"
            ].command_value,
            98.0,
        )

        self.assertEqual(
            steps[
                "configure_sweep_dwell"
            ].command_value,
            100.0,
        )

        self.assertEqual(
            steps[
                "configure_smc_resting_level"
            ].command_value,
            -120.0,
        )

    def test_wavegen_voltage_window_is_special_operation(self):
        steps = by_name(
            build_burn_plan(
                default_parameters(),
                100,
                0,
            )
        )

        step = steps[
            "configure_wavegen_voltage_window"
        ]

        self.assertEqual(
            step.operation,
            OP_SAFE_VOLTAGE_WINDOW,
        )

        self.assertEqual(
            step.command_value,
            (-1.0, 1.0),
        )

    def test_waveform_upload_plan(self):
        plan = build_burn_plan(
            default_parameters(),
            waveform_points=321,
            waveform_id_before=9,
        )

        steps = by_name(plan)
        names = [
            step.name
            for step in plan
        ]

        load = steps["load_waveform"]

        self.assertEqual(
            load.command_pv,
            f"{WGEN}:LOAD_CMD",
        )
        self.assertEqual(
            load.verify_pv,
            f"{WGEN}:WAVEFORM_READY",
        )
        self.assertEqual(
            load.verify_value,
            1,
        )

        self.assertEqual(
            steps[
                "verify_waveform_points"
            ].verify_value,
            321,
        )

        self.assertEqual(
            steps[
                "verify_waveform_id_advanced"
            ].verify_rule,
            "gt",
        )

        self.assertEqual(
            steps[
                "verify_waveform_id_advanced"
            ].verify_value,
            9,
        )

        self.assertLess(
            names.index("load_waveform"),
            names.index("disconnect_nmr"),
        )

    def test_preupload_only_requires_staged_points(self):
        # WAVEFORM_READY is intentionally not an input to this planner.
        plan = build_burn_plan(
            default_parameters(),
            waveform_points=2,
            waveform_id_before=0,
        )

        self.assertTrue(plan)

    def test_rejects_missing_waveform(self):
        with self.assertRaises(ValueError):
            build_burn_plan(
                default_parameters(),
                waveform_points=1,
                waveform_id_before=0,
            )

    def test_critical_switch_and_permit_order(self):
        plan = build_burn_plan(
            default_parameters(),
            100,
            0,
        )

        names = [
            step.name
            for step in plan
        ]

        ordered = [
            "disconnect_nmr",
            "reverify_nmr_disconnected",
            "require_ssrf_connect_permit",
            "connect_ssrf",
            "wavegen_on",
            "smc_fm_on",
            "require_rf_enable_permit",
            "smc_rf_on",
            "arm_sweep",
            "recheck_rf_enable_permit",
            "trigger_sweep",
        ]

        indexes = [
            names.index(name)
            for name in ordered
        ]

        self.assertEqual(
            indexes,
            sorted(indexes),
        )

    def test_switch_command_mapping(self):
        steps = by_name(
            build_burn_plan(
                default_parameters(),
                100,
                0,
            )
        )

        self.assertEqual(
            (
                steps["disconnect_nmr"].command_value,
                steps["disconnect_nmr"].verify_value,
            ),
            (1, 3),
        )

        self.assertEqual(
            (
                steps["connect_ssrf"].command_value,
                steps["connect_ssrf"].verify_value,
            ),
            (0, 0),
        )

        self.assertEqual(
            (
                steps[
                    "cleanup_disconnect_ssrf"
                ].command_value,
                steps[
                    "cleanup_disconnect_ssrf"
                ].verify_value,
            ),
            (1, 3),
        )

        self.assertEqual(
            (
                steps[
                    "cleanup_restore_nmr"
                ].command_value,
                steps[
                    "cleanup_restore_nmr"
                ].verify_value,
            ),
            (0, 0),
        )

    def test_trigger_is_momentary_plan(self):
        steps = by_name(
            build_burn_plan(
                default_parameters(),
                100,
                0,
            )
        )

        trigger = steps["trigger_sweep"]

        self.assertEqual(
            trigger.operation,
            OP_TRIGGER,
        )
        self.assertEqual(
            trigger.command_pv,
            f"{SMC}:SWEEP_TRIGGER_CMD",
        )
        self.assertEqual(
            trigger.command_value,
            1,
        )
        self.assertEqual(
            trigger.monitor_pv,
            f"{ILK}:RF_ENABLE_PERMIT",
        )

    def test_wait_is_three_dwell_plus_guard(self):
        steps = by_name(
            build_burn_plan(
                default_parameters(),
                100,
                0,
            )
        )

        wait = steps["wait_for_sweep"]

        # dwell = 10 * 0.010 s = 100 ms
        # total = 3 * 100 + 500 = 800 ms
        self.assertEqual(
            wait.operation,
            OP_WAIT,
        )
        self.assertAlmostEqual(
            wait.command_value,
            800.0,
        )

    def test_cleanup_attempts_all_off_commands_first(self):
        cleanup = build_cleanup_plan()

        first_four = cleanup[:4]

        self.assertTrue(
            all(
                step.operation
                == OP_BEST_EFFORT_WRITE
                for step in first_four
            )
        )

        self.assertEqual(
            [
                step.command_pv
                for step in first_four
            ],
            [
                f"{SMC}:SWEEP_ARM_CMD",
                f"{SMC}:RF_CMD",
                f"{SMC}:FM_CMD",
                f"{WGEN}:OUTPUT_CMD",
            ],
        )

        self.assertTrue(
            all(
                step.command_value == 0
                for step in first_four
            )
        )

    def test_cleanup_verifies_off_before_switch_motion(self):
        cleanup = build_cleanup_plan()

        names = [
            step.name
            for step in cleanup
        ]

        self.assertLess(
            names.index(
                "cleanup_verify_wavegen_off"
            ),
            names.index(
                "cleanup_disconnect_ssrf"
            ),
        )

        self.assertLess(
            names.index(
                "cleanup_reverify_ssrf_disconnected"
            ),
            names.index(
                "cleanup_require_nmr_permit"
            ),
        )

        self.assertLess(
            names.index(
                "cleanup_require_nmr_permit"
            ),
            names.index(
                "cleanup_restore_nmr"
            ),
        )

    def test_cleanup_uses_authoritative_switch_readbacks(self):
        steps = by_name(
            build_cleanup_plan()
        )

        self.assertEqual(
            steps[
                "cleanup_disconnect_ssrf"
            ].verify_pv,
            f"{SSRF_SW}:MASK_RBV",
        )

        self.assertEqual(
            steps[
                "cleanup_restore_nmr"
            ].verify_pv,
            f"{NMR_SW}:MASK_RBV",
        )


if __name__ == "__main__":
    unittest.main()
