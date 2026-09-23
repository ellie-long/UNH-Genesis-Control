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

    def test_safe_idle(self):
        d = judge()

        self.assertEqual(d.state, SAFE_IDLE)
        self.assertEqual(d.interlock_ok, 1)
        self.assertEqual(d.nmr_connect_permit, 1)
        self.assertEqual(d.ssrf_connect_permit, 1)
        self.assertEqual(d.rf_enable_permit, 0)

    def test_nmr_live(self):
        d = judge(
            nmr_mask=0,
            ssrf_mask=3,
            smc_rf=0,
        )

        self.assertEqual(d.state, NMR_LIVE)
        self.assertEqual(d.nmr_connect_permit, 1)
        self.assertEqual(d.ssrf_connect_permit, 0)
        self.assertEqual(d.rf_enable_permit, 0)

    def test_ssrf_ready(self):
        d = judge(
            nmr_mask=3,
            ssrf_mask=0,
            smc_rf=0,
        )

        self.assertEqual(d.state, SSRF_READY)
        self.assertEqual(d.nmr_connect_permit, 0)
        self.assertEqual(d.ssrf_connect_permit, 1)
        self.assertEqual(d.rf_enable_permit, 1)

    def test_burning_topology_is_safe(self):
        d = judge(
            nmr_mask=3,
            ssrf_mask=0,
            smc_rf=1,
        )

        self.assertEqual(d.state, BURNING)
        self.assertEqual(d.interlock_ok, 1)

        # Do not move either switch while RF is active.
        self.assertEqual(d.nmr_connect_permit, 0)
        self.assertEqual(d.ssrf_connect_permit, 0)

        # Existing RF topology remains permitted.
        self.assertEqual(d.rf_enable_permit, 1)

    def test_both_paths_connected_is_conflict(self):
        d = judge(
            nmr_mask=0,
            ssrf_mask=0,
            smc_rf=0,
        )

        self.assertEqual(d.state, CONFLICT)
        self.assertEqual(d.interlock_ok, 0)
        self.assertEqual(d.nmr_connect_permit, 0)
        self.assertEqual(d.ssrf_connect_permit, 0)
        self.assertEqual(d.rf_enable_permit, 0)

    def test_rf_with_nmr_connected_is_conflict(self):
        d = judge(
            nmr_mask=0,
            ssrf_mask=3,
            smc_rf=1,
        )

        self.assertEqual(d.state, CONFLICT)
        self.assertEqual(d.interlock_ok, 0)
        self.assertIn(
            "RF enabled while NMR",
            d.reason,
        )

    def test_rf_blocked_with_both_paths_off(self):
        d = judge(
            nmr_mask=3,
            ssrf_mask=3,
            smc_rf=1,
        )

        self.assertEqual(d.state, RF_BLOCKED)
        self.assertEqual(d.interlock_ok, 1)
        self.assertEqual(d.nmr_connect_permit, 0)
        self.assertEqual(d.ssrf_connect_permit, 0)
        self.assertEqual(d.rf_enable_permit, 0)

    def test_nmr_mixed_switch_fails_closed(self):
        d = judge(
            nmr_mask=1,
        )

        self.assertEqual(d.state, MIXED_SWITCH)
        self.assertEqual(d.interlock_ok, 0)
        self.assertEqual(d.nmr_connect_permit, 0)
        self.assertEqual(d.ssrf_connect_permit, 0)
        self.assertEqual(d.rf_enable_permit, 0)

    def test_ssrf_mixed_switch_fails_closed(self):
        d = judge(
            ssrf_mask=2,
        )

        self.assertEqual(d.state, MIXED_SWITCH)
        self.assertEqual(d.interlock_ok, 0)

    def test_disconnected_input_fails_closed(self):
        d = judge(
            nmr_comm=0,
        )

        self.assertEqual(d.state, UNKNOWN)
        self.assertEqual(d.inputs_ok, 0)
        self.assertEqual(d.interlock_ok, 0)
        self.assertEqual(d.nmr_connect_permit, 0)
        self.assertEqual(d.ssrf_connect_permit, 0)
        self.assertEqual(d.rf_enable_permit, 0)

    def test_missing_readback_fails_closed(self):
        d = judge(
            smc_rf=None,
        )

        self.assertEqual(d.state, UNKNOWN)
        self.assertEqual(d.interlock_ok, 0)

    def test_invalid_mask_fails_closed(self):
        d = judge(
            nmr_mask=7,
        )

        self.assertEqual(d.state, MIXED_SWITCH)
        self.assertEqual(d.interlock_ok, 0)


if __name__ == "__main__":
    unittest.main()
