import unittest

from logic_devices.ssrf_interlock import (
    BURNING,
    CONFLICT,
    MIXED_SWITCH,
    NMR_LIVE,
    RF_BLOCKED,
    SAFE_IDLE,
    SSRF_READY,
    UNKNOWN,
    evaluate_interlock,
)


def judge(
    *,
    nmr_comm=1,
    nmr_mask=3,
    ssrf_comm=1,
    ssrf_mask=3,
    smc_comm=1,
    smc_rf=0,
):
    return evaluate_interlock(
        nmr_comm=nmr_comm,
        nmr_mask=nmr_mask,
        ssrf_comm=ssrf_comm,
        ssrf_mask=ssrf_mask,
        smc_comm=smc_comm,
        smc_rf=smc_rf,
    )


class TestGenesisSsrfInterlock(unittest.TestCase):

    def test_labview_route_connected(self):
        d = judge(nmr_mask=0)
        self.assertEqual(d.state, NMR_LIVE)
        self.assertEqual(d.interlock_ok, 1)
        self.assertEqual(d.nmr_path_off, 0)
        self.assertIn("LabVIEW", d.reason)
        self.assertEqual(d.rf_enable_permit, 0)
        self.assertEqual(d.nmr_connect_permit, 0)
        self.assertEqual(d.ssrf_connect_permit, 0)

    def test_pynmr_route_connected(self):
        d = judge(nmr_mask=3)
        self.assertEqual(d.state, NMR_LIVE)
        self.assertEqual(d.interlock_ok, 1)
        self.assertEqual(d.nmr_path_off, 0)
        self.assertIn("PyNMR", d.reason)
        self.assertEqual(d.rf_enable_permit, 0)
        self.assertEqual(d.nmr_connect_permit, 0)
        self.assertEqual(d.ssrf_connect_permit, 0)

    def test_no_route_is_safe_idle(self):
        for mask in (0, 3):
            with self.subTest(mask=mask):
                d = judge(nmr_mask=mask)
                self.assertNotEqual(d.state, SAFE_IDLE)
                self.assertEqual(d.nmr_path_off, 0)

    def test_ssrf_connection_conflicts_with_both_nmr_routes(self):
        for mask in (0, 3):
            with self.subTest(mask=mask):
                d = judge(
                    nmr_mask=mask,
                    ssrf_mask=0,
                    smc_rf=0,
                )
                self.assertEqual(d.state, CONFLICT)
                self.assertEqual(d.interlock_ok, 0)
                self.assertEqual(d.rf_enable_permit, 0)

    def test_rf_active_conflicts_with_both_nmr_routes(self):
        for mask in (0, 3):
            with self.subTest(mask=mask):
                d = judge(
                    nmr_mask=mask,
                    ssrf_mask=3,
                    smc_rf=1,
                )
                self.assertEqual(d.state, CONFLICT)
                self.assertEqual(d.interlock_ok, 0)
                self.assertEqual(d.rf_enable_permit, 0)

    def test_all_valid_route_combinations_inhibit_rf(self):
        for nmr in (0, 3):
            for ssrf in (0, 3):
                for rf in (0, 1):
                    with self.subTest(
                        nmr=nmr, ssrf=ssrf, rf=rf
                    ):
                        d = judge(
                            nmr_mask=nmr,
                            ssrf_mask=ssrf,
                            smc_rf=rf,
                        )
                        self.assertEqual(d.nmr_path_off, 0)
                        self.assertEqual(d.rf_enable_permit, 0)
                        self.assertEqual(d.ssrf_connect_permit, 0)
                        self.assertEqual(d.nmr_connect_permit, 0)

    def test_mixed_nmr_switch_fails_closed(self):
        for mask in (1, 2, 7):
            with self.subTest(mask=mask):
                d = judge(nmr_mask=mask)
                self.assertEqual(d.state, MIXED_SWITCH)
                self.assertEqual(d.interlock_ok, 0)
                self.assertEqual(d.rf_enable_permit, 0)
                self.assertEqual(d.nmr_connect_permit, 0)

    def test_mixed_ssrf_switch_fails_closed(self):
        for mask in (1, 2):
            with self.subTest(mask=mask):
                d = judge(ssrf_mask=mask)
                self.assertEqual(d.state, MIXED_SWITCH)
                self.assertEqual(d.interlock_ok, 0)
                self.assertEqual(d.rf_enable_permit, 0)

    def test_missing_inputs_fail_closed(self):
        for name in (
            "nmr_comm",
            "nmr_mask",
            "ssrf_comm",
            "ssrf_mask",
            "smc_comm",
            "smc_rf",
        ):
            with self.subTest(name=name):
                d = judge(**{name: None})
                self.assertEqual(d.state, UNKNOWN)
                self.assertEqual(d.interlock_ok, 0)
                self.assertEqual(d.rf_enable_permit, 0)

    def test_disconnected_inputs_fail_closed(self):
        for name in (
            "nmr_comm",
            "ssrf_comm",
            "smc_comm",
        ):
            with self.subTest(name=name):
                d = judge(**{name: 0})
                self.assertEqual(d.state, UNKNOWN)
                self.assertEqual(d.interlock_ok, 0)
                self.assertEqual(d.rf_enable_permit, 0)

    def test_switch_selection_never_authorized_by_smc_only(self):
        for mask in (0, 3):
            d = judge(
                nmr_mask=mask,
                ssrf_mask=3,
                smc_rf=0,
            )
            self.assertEqual(d.nmr_connect_permit, 0)
            self.assertEqual(d.ssrf_connect_permit, 0)
            self.assertIn("RF-isolation", d.reason)

if __name__ == "__main__":
    unittest.main()
