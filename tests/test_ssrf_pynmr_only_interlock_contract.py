"""Single-source PyNMR switch-state contract.

Software-only regression tests. Passing these does not authorize
hardware RF operation or establish physical transmitter isolation.
"""

import pytest

from logic_devices.ssrf_interlock import (
    BURNING,
    CONFLICT,
    NMR_LIVE,
    SAFE_IDLE,
    SSRF_READY,
    evaluate_interlock,
)


def evaluate(nmr_mask, ssrf_mask, rf):
    return evaluate_interlock(
        nmr_comm=1,
        nmr_mask=nmr_mask,
        ssrf_comm=1,
        ssrf_mask=ssrf_mask,
        smc_comm=1,
        smc_rf=rf,
    )


@pytest.mark.parametrize(
    "nmr_mask,ssrf_mask,rf,state",
    [
        (0, 3, 0, NMR_LIVE),
        (3, 3, 0, SAFE_IDLE),
        (3, 0, 0, SSRF_READY),
        (3, 0, 1, BURNING),
        (0, 0, 0, CONFLICT),
    ],
)
def test_single_source_switch_states(nmr_mask, ssrf_mask, rf, state):
    decision = evaluate(nmr_mask, ssrf_mask, rf)
    assert decision.state == state


def test_terminated_nmr_path_reports_isolation():
    decision = evaluate(3, 3, 0)
    assert decision.nmr_path_off == 1
    assert decision.rf_enable_permit == 0


def test_rf_permit_requires_isolated_nmr_and_connected_ssrf():
    assert evaluate(0, 3, 0).rf_enable_permit == 0
    assert evaluate(3, 3, 0).rf_enable_permit == 0
    assert evaluate(3, 0, 0).rf_enable_permit == 1
    assert evaluate(3, 0, 1).rf_enable_permit == 1


def test_conflicting_target_paths_fail_closed():
    decision = evaluate(0, 0, 0)
    assert decision.interlock_ok == 0
    assert decision.rf_enable_permit == 0
