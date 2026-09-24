import math
import unittest

from logic_devices.ssrf_sequence_core import (
    BurnParameters,
    PreflightInputs,
    evaluate_preflight,
    validate_parameters,
)


def good_params():
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


def good_state():
    return PreflightInputs(
        smc_comm=1,
        smc_write_enabled=1,
        smc_rf=0,
        smc_fm=0,
        smc_sweep_arm=0,

        wavegen_comm=1,
        wavegen_write_enabled=1,
        wavegen_output=0,
        waveform_points=100,

        nmr_switch_comm=1,
        nmr_switch_write_enabled=1,
        nmr_mask=0,

        ssrf_switch_comm=1,
        ssrf_switch_write_enabled=1,
        ssrf_mask=3,

        interlock_inputs_ok=1,
        interlock_ok=1,
    )


class TestBurnParameters(unittest.TestCase):

    def test_good_parameters(self):
        p = good_params()

        ok, reason = validate_parameters(p)

        self.assertTrue(ok)
        self.assertEqual(reason, "parameters valid")

        self.assertAlmostEqual(
            p.sweep_step_db,
            98.0,
        )

        self.assertAlmostEqual(
            p.sweep_dwell_ms,
            100.0,
        )

    def test_bad_period(self):
        p = BurnParameters(
            **{
                **good_params().__dict__,
                "fm_period_s": 0.0,
            }
        )

        ok, _ = validate_parameters(p)

        self.assertFalse(ok)

    def test_bad_voltage_order(self):
        p = BurnParameters(
            **{
                **good_params().__dict__,
                "vhigh_v": -1.0,
                "vlow_v": 1.0,
            }
        )

        ok, _ = validate_parameters(p)

        self.assertFalse(ok)

    def test_nonfinite_rejected(self):
        p = BurnParameters(
            **{
                **good_params().__dict__,
                "carrier_mhz": math.nan,
            }
        )

        ok, _ = validate_parameters(p)

        self.assertFalse(ok)


class TestPreflight(unittest.TestCase):

    def test_nmr_live_start_allowed(self):
        d = evaluate_preflight(
            good_state()
        )

        self.assertEqual(d.ready, 1)

    def test_safe_idle_start_allowed(self):
        s = PreflightInputs(
            **{
                **good_state().__dict__,
                "nmr_mask": 3,
            }
        )

        d = evaluate_preflight(s)

        self.assertEqual(d.ready, 1)

    def test_ssrf_connected_start_blocked(self):
        s = PreflightInputs(
            **{
                **good_state().__dict__,
                "ssrf_mask": 0,
            }
        )

        d = evaluate_preflight(s)

        self.assertEqual(d.ready, 0)

    def test_mixed_nmr_blocked(self):
        s = PreflightInputs(
            **{
                **good_state().__dict__,
                "nmr_mask": 1,
            }
        )

        d = evaluate_preflight(s)

        self.assertEqual(d.ready, 0)

    def test_rf_on_blocked(self):
        s = PreflightInputs(
            **{
                **good_state().__dict__,
                "smc_rf": 1,
            }
        )

        d = evaluate_preflight(s)

        self.assertEqual(d.ready, 0)

    def test_wavegen_on_blocked(self):
        s = PreflightInputs(
            **{
                **good_state().__dict__,
                "wavegen_output": 1,
            }
        )

        d = evaluate_preflight(s)

        self.assertEqual(d.ready, 0)

    def test_missing_waveform_blocked(self):
        s = PreflightInputs(
            **{
                **good_state().__dict__,
                "waveform_points": 0,
            }
        )

        d = evaluate_preflight(s)

        self.assertEqual(d.ready, 0)

    def test_write_gate_blocked(self):
        s = PreflightInputs(
            **{
                **good_state().__dict__,
                "smc_write_enabled": 0,
            }
        )

        d = evaluate_preflight(s)

        self.assertEqual(d.ready, 0)

    def test_interlock_blocked(self):
        s = PreflightInputs(
            **{
                **good_state().__dict__,
                "interlock_ok": 0,
            }
        )

        d = evaluate_preflight(s)

        self.assertEqual(d.ready, 0)


if __name__ == "__main__":
    unittest.main()
