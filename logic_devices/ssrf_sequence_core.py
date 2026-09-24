"""
Pure safety/parameter kernel for the Genesis ssRF burn sequencer.

This module performs NO EPICS access and NO hardware writes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


PORT1_MASK = 0
PORT2_MASK = 3


@dataclass(frozen=True)
class BurnParameters:
    carrier_mhz: float
    start_level_dbm: float
    target_level_dbm: float
    fm_deviation_khz: float

    fm_period_s: float
    vhigh_v: float
    vlow_v: float

    fm_repeats: int = 10

    @property
    def sweep_step_db(self) -> float:
        return (
            self.target_level_dbm
            - self.start_level_dbm
        )

    @property
    def sweep_dwell_ms(self) -> float:
        return (
            self.fm_repeats
            * self.fm_period_s
            * 1000.0
        )


@dataclass(frozen=True)
class BurnReadinessInputs:
    smc_comm: int
    smc_write_enabled: int
    smc_rf: int
    smc_fm: int
    smc_sweep_arm: int

    wavegen_comm: int
    wavegen_write_enabled: int
    wavegen_output: int
    waveform_points: int

    nmr_switch_comm: int
    nmr_switch_write_enabled: int
    nmr_mask: int

    ssrf_switch_comm: int
    ssrf_switch_write_enabled: int
    ssrf_mask: int

    interlock_inputs_ok: int
    interlock_ok: int


@dataclass(frozen=True)
class BurnReadinessDecision:
    ready: int
    reason: str


def validate_parameters(
    p: BurnParameters,
) -> tuple[bool, str]:
    numeric = {
        "carrier_mhz": p.carrier_mhz,
        "start_level_dbm": p.start_level_dbm,
        "target_level_dbm": p.target_level_dbm,
        "fm_deviation_khz": p.fm_deviation_khz,
        "fm_period_s": p.fm_period_s,
        "vhigh_v": p.vhigh_v,
        "vlow_v": p.vlow_v,
    }

    for name, value in numeric.items():
        if not math.isfinite(float(value)):
            return False, f"{name} must be finite"

    if p.carrier_mhz <= 0:
        return False, "carrier frequency must be positive"

    if p.fm_deviation_khz <= 0:
        return False, "FM deviation must be positive"

    if p.fm_period_s <= 0:
        return False, "FM period must be positive"

    if p.fm_repeats < 1:
        return False, "FM repeats must be >= 1"

    if p.vhigh_v <= p.vlow_v:
        return False, "VHIGH must be greater than VLOW"

    if p.target_level_dbm <= p.start_level_dbm:
        return (
            False,
            "target RF level must exceed start RF level",
        )

    if p.sweep_step_db <= 0:
        return False, "power sweep step must be positive"

    if p.sweep_dwell_ms <= 0:
        return False, "power sweep dwell must be positive"

    return True, "parameters valid"


def evaluate_burn_readiness(
    s: BurnReadinessInputs,
) -> BurnReadinessDecision:
    """
    Decide whether a burn sequence may begin.

    This does not require WAVEFORM_READY because the staged waveform
    has not necessarily been uploaded to hardware yet.
    """

    communications = {
        "SMC100A": s.smc_comm,
        "WaveGen": s.wavegen_comm,
        "NMR switch": s.nmr_switch_comm,
        "ssRF switch": s.ssrf_switch_comm,
    }

    for name, value in communications.items():
        if int(value) != 1:
            return BurnReadinessDecision(
                0,
                f"{name} disconnected",
            )

    write_gates = {
        "SMC100A": s.smc_write_enabled,
        "WaveGen": s.wavegen_write_enabled,
        "NMR switch": s.nmr_switch_write_enabled,
        "ssRF switch": s.ssrf_switch_write_enabled,
    }

    for name, value in write_gates.items():
        if int(value) != 1:
            return BurnReadinessDecision(
                0,
                f"{name} writes disabled",
            )

    if int(s.interlock_inputs_ok) != 1:
        return BurnReadinessDecision(
            0,
            "interlock inputs invalid",
        )

    if int(s.interlock_ok) != 1:
        return BurnReadinessDecision(
            0,
            "interlock blocked",
        )

    if int(s.smc_rf) != 0:
        return BurnReadinessDecision(
            0,
            "SMC RF must be off",
        )

    if int(s.smc_fm) != 0:
        return BurnReadinessDecision(
            0,
            "SMC FM must be off",
        )

    if int(s.smc_sweep_arm) != 0:
        return BurnReadinessDecision(
            0,
            "SMC sweep must be disarmed",
        )

    if int(s.wavegen_output) != 0:
        return BurnReadinessDecision(
            0,
            "WaveGen output must be off",
        )

    if int(s.waveform_points) < 2:
        return BurnReadinessDecision(
            0,
            "no valid waveform staged",
        )

    if int(s.nmr_mask) not in (PORT1_MASK, PORT2_MASK):
        return BurnReadinessDecision(
            0,
            "NMR switch mixed/unknown",
        )

    if int(s.ssrf_mask) != PORT2_MASK:
        return BurnReadinessDecision(
            0,
            "ssRF path must start disconnected",
        )

    return BurnReadinessDecision(
        1,
        "burn ready",
    )
