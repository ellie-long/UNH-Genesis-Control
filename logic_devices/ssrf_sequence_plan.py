"""
Pure ssRF burn sequence plan.

This module describes the intended Genesis ssRF burn sequence but performs
NO EPICS access and NO hardware writes.

It exists so ordering, readback verification, interlock permits, waveform
upload semantics, and fail-safe cleanup can be unit tested before an active
executor is added to the sequencer IOC.
"""

from __future__ import annotations

from dataclasses import dataclass

from logic_devices.ssrf_sequence_core import (
    BurnParameters,
    validate_parameters,
)


# ----------------------------------------------------------------------
# Canonical Genesis ssRF PVs
# ----------------------------------------------------------------------

SMC = "UNH:GEN:SSRF:GEN"
WGEN = "UNH:GEN:SSRF:WGEN"
NMR_SW = "UNH:GEN:SSRF:NMR_SW"
SSRF_SW = "UNH:GEN:SSRF:RF_SW"
ILK = "UNH:GEN:SSRF:ILK"


# ----------------------------------------------------------------------
# Abstract operations.
#
# These are descriptions only.  Nothing in this module performs a put.
# ----------------------------------------------------------------------

OP_READINESS_CHECK = "READINESS_CHECK"
OP_CHECK = "CHECK"
OP_WRITE_VERIFY = "WRITE_VERIFY"
OP_BEST_EFFORT_WRITE = "BEST_EFFORT_WRITE"
OP_SAFE_VOLTAGE_WINDOW = "SAFE_VOLTAGE_WINDOW"
OP_TRIGGER = "TRIGGER"
OP_WAIT = "WAIT"


@dataclass(frozen=True)
class SequenceStep:
    """One declarative step in the future active sequencer."""

    name: str
    operation: str

    command_pv: str = ""
    command_value: object = None

    verify_pv: str = ""
    verify_rule: str = ""
    verify_value: object = None

    monitor_pv: str = ""
    note: str = ""


def _write_verify(
    name,
    command_pv,
    command_value,
    verify_pv,
    verify_value,
    *,
    rule="eq",
    note="",
):
    return SequenceStep(
        name=name,
        operation=OP_WRITE_VERIFY,
        command_pv=command_pv,
        command_value=command_value,
        verify_pv=verify_pv,
        verify_rule=rule,
        verify_value=verify_value,
        note=note,
    )


def _check(
    name,
    pv,
    value,
    *,
    rule="eq",
    note="",
):
    return SequenceStep(
        name=name,
        operation=OP_CHECK,
        verify_pv=pv,
        verify_rule=rule,
        verify_value=value,
        note=note,
    )


def build_cleanup_plan():
    """
    Return the fail-safe cleanup plan.

    Safety rule:
      First ATTEMPT every de-energizing command.

      Only after all corresponding readbacks are confirmed OFF may the
      sequence move the ssRF switch.

      NMR restoration is allowed only after the ssRF path is confirmed
      disconnected and NMR_CONNECT_PERMIT is asserted.

    Therefore failure to confirm a safe state leaves the target paths
    disconnected rather than forcing the NMR route.
    """

    return (
        # --------------------------------------------------------------
        # Best-effort de-energization.
        #
        # A failure of one OFF command must not prevent attempts to send
        # the remaining OFF commands.
        # --------------------------------------------------------------

        SequenceStep(
            name="cleanup_disarm_sweep",
            operation=OP_BEST_EFFORT_WRITE,
            command_pv=f"{SMC}:SWEEP_ARM_CMD",
            command_value=0,
        ),

        SequenceStep(
            name="cleanup_rf_off",
            operation=OP_BEST_EFFORT_WRITE,
            command_pv=f"{SMC}:RF_CMD",
            command_value=0,
        ),

        SequenceStep(
            name="cleanup_fm_off",
            operation=OP_BEST_EFFORT_WRITE,
            command_pv=f"{SMC}:FM_CMD",
            command_value=0,
        ),

        SequenceStep(
            name="cleanup_wavegen_off",
            operation=OP_BEST_EFFORT_WRITE,
            command_pv=f"{WGEN}:OUTPUT_CMD",
            command_value=0,
        ),

        # --------------------------------------------------------------
        # Hard verification barrier.
        #
        # The executor must NOT proceed to switch motion if any of these
        # checks fails or becomes unavailable.
        # --------------------------------------------------------------

        _check(
            "cleanup_verify_sweep_disarmed",
            f"{SMC}:SWEEP_ARM_RBV",
            0,
        ),

        _check(
            "cleanup_verify_rf_off",
            f"{SMC}:RF_RBV",
            0,
        ),

        _check(
            "cleanup_verify_fm_off",
            f"{SMC}:FM_RBV",
            0,
        ),

        _check(
            "cleanup_verify_wavegen_off",
            f"{WGEN}:OUTPUT_RBV",
            0,
        ),

        # --------------------------------------------------------------
        # Remove the burn path first.
        # PORT_CMD 1 -> Port 2 -> authoritative mask 3.
        # --------------------------------------------------------------

        _write_verify(
            "cleanup_disconnect_ssrf",
            f"{SSRF_SW}:PORT_CMD",
            1,
            f"{SSRF_SW}:MASK_RBV",
            3,
        ),

        # Deliberate second verification immediately before NMR restore.
        _check(
            "cleanup_reverify_ssrf_disconnected",
            f"{SSRF_SW}:MASK_RBV",
            3,
        ),

        # Interlock must explicitly permit NMR connection.
        _check(
            "cleanup_require_nmr_permit",
            f"{ILK}:NMR_CONNECT_PERMIT",
            1,
        ),

        # PORT_CMD 0 -> Port 1 -> authoritative mask 0.
        _write_verify(
            "cleanup_restore_nmr",
            f"{NMR_SW}:PORT_CMD",
            0,
            f"{NMR_SW}:MASK_RBV",
            0,
        ),
    )


def build_burn_plan(
    parameters,
    waveform_points,
    waveform_id_before,
    *,
    guard_ms=500.0,
):
    """
    Build the complete nominal ssRF burn plan.

    This function performs validation and returns a tuple of declarative
    steps.  It performs no EPICS reads/writes.

    WaveGen upload plan:
      pre-upload:
          WAVEFORM_POINTS >= 2

      post-LOAD:
          WAVEFORM_READY == 1
          ARB_POINTS_RBV == staged point count
          WAVEFORM_ID > pre-load WAVEFORM_ID

    SMC burn timing:
          dwell_ms = fm_repeats * fm_period_s * 1000

    The TRI envelope is start -> target -> start, therefore the existing
    commissioning wait plan is:
          total_wait_ms = 3 * dwell_ms + guard_ms
    """

    parameters_ok, reason = validate_parameters(
        parameters
    )

    if not parameters_ok:
        raise ValueError(
            f"invalid burn parameters: {reason}"
        )

    points = int(waveform_points)

    if points < 2:
        raise ValueError(
            "waveform_points must be >= 2"
        )

    waveform_id_before = int(
        waveform_id_before
    )

    if waveform_id_before < 0:
        raise ValueError(
            "waveform_id_before must be >= 0"
        )

    guard_ms = float(guard_ms)

    if guard_ms < 0:
        raise ValueError(
            "guard_ms must be >= 0"
        )

    sweep_step_db = (
        float(parameters.target_level_dbm)
        - float(parameters.start_level_dbm)
    )

    sweep_dwell_ms = (
        int(parameters.fm_repeats)
        * float(parameters.fm_period_s)
        * 1000.0
    )

    total_wait_ms = (
        3.0 * sweep_dwell_ms
        + guard_ms
    )

    plan = [

        # --------------------------------------------------------------
        # READINESS CHECK
        # --------------------------------------------------------------

        SequenceStep(
            name="burn_readiness",
            operation=OP_READINESS_CHECK,
            note=(
                "Use live 5B burn readiness immediately before execution; "
                "all required IOCs, write gates, interlock state and "
                "safe initial outputs must be confirmed."
            ),
        ),

        # --------------------------------------------------------------
        # CONFIGURE — all outputs remain OFF.
        # --------------------------------------------------------------

        _write_verify(
            "configure_wavegen_period",
            f"{WGEN}:PERIOD_CMD",
            float(parameters.fm_period_s),
            f"{WGEN}:PERIOD_RBV",
            float(parameters.fm_period_s),
            rule="close",
        ),

        SequenceStep(
            name="configure_wavegen_voltage_window",
            operation=OP_SAFE_VOLTAGE_WINDOW,
            command_value=(
                float(parameters.vlow_v),
                float(parameters.vhigh_v),
            ),
            verify_rule="window",
            verify_value=(
                float(parameters.vlow_v),
                float(parameters.vhigh_v),
            ),
            note=(
                "Executor must read current VLOW/VHIGH and choose a "
                "safe update order so VLOW < VHIGH is preserved after "
                "every individual write."
            ),
        ),

        _write_verify(
            "configure_smc_carrier",
            f"{SMC}:FREQ_CMD",
            float(parameters.carrier_mhz),
            f"{SMC}:FREQ_RBV",
            float(parameters.carrier_mhz),
            rule="close",
        ),

        # The resting CW level is the minimum/start level before the
        # start -> target -> start power sweep is fired.
        _write_verify(
            "configure_smc_resting_level",
            f"{SMC}:LEVEL_CMD",
            float(parameters.start_level_dbm),
            f"{SMC}:LEVEL_RBV",
            float(parameters.start_level_dbm),
            rule="close",
        ),

        _write_verify(
            "configure_smc_fm_source",
            f"{SMC}:FM_SOURCE_CMD",
            1,
            f"{SMC}:FM_SOURCE_RBV",
            1,
        ),

        _write_verify(
            "configure_smc_fm_deviation",
            f"{SMC}:FM_DEV_CMD",
            float(parameters.fm_deviation_khz),
            f"{SMC}:FM_DEV_RBV",
            float(parameters.fm_deviation_khz),
            rule="close",
        ),

        _write_verify(
            "configure_sweep_start",
            f"{SMC}:SWEEP_START_CMD",
            float(parameters.start_level_dbm),
            f"{SMC}:SWEEP_START_RBV",
            float(parameters.start_level_dbm),
            rule="close",
        ),

        _write_verify(
            "configure_sweep_stop",
            f"{SMC}:SWEEP_STOP_CMD",
            float(parameters.target_level_dbm),
            f"{SMC}:SWEEP_STOP_RBV",
            float(parameters.target_level_dbm),
            rule="close",
        ),

        _write_verify(
            "configure_sweep_step",
            f"{SMC}:SWEEP_STEP_CMD",
            sweep_step_db,
            f"{SMC}:SWEEP_STEP_RBV",
            sweep_step_db,
            rule="close",
        ),

        _write_verify(
            "configure_sweep_dwell",
            f"{SMC}:SWEEP_DWELL_CMD",
            sweep_dwell_ms,
            f"{SMC}:SWEEP_DWELL_RBV",
            sweep_dwell_ms,
            rule="close",
        ),

        _write_verify(
            "configure_sweep_shape",
            f"{SMC}:SWEEP_SHAPE_CMD",
            1,
            f"{SMC}:SWEEP_SHAPE_RBV",
            1,
            note="1 = TRI",
        ),

        # --------------------------------------------------------------
        # COMMIT staged Keysight waveform.
        # --------------------------------------------------------------

        _write_verify(
            "load_waveform",
            f"{WGEN}:LOAD_CMD",
            1,
            f"{WGEN}:WAVEFORM_READY",
            1,
        ),

        _check(
            "verify_waveform_points",
            f"{WGEN}:ARB_POINTS_RBV",
            points,
        ),

        _check(
            "verify_waveform_id_advanced",
            f"{WGEN}:WAVEFORM_ID",
            waveform_id_before,
            rule="gt",
        ),

        # --------------------------------------------------------------
        # Re-confirm all energy-producing outputs are OFF before any
        # switch motion.
        # --------------------------------------------------------------

        _check(
            "verify_smc_rf_off",
            f"{SMC}:RF_RBV",
            0,
        ),

        _check(
            "verify_smc_fm_off",
            f"{SMC}:FM_RBV",
            0,
        ),

        _check(
            "verify_sweep_disarmed",
            f"{SMC}:SWEEP_ARM_RBV",
            0,
        ),

        _check(
            "verify_wavegen_off",
            f"{WGEN}:OUTPUT_RBV",
            0,
        ),

        # --------------------------------------------------------------
        # Protect NMR before connecting burn path.
        #
        # NMR PORT_CMD 1 -> Port 2 -> mask 3.
        # --------------------------------------------------------------

        _write_verify(
            "disconnect_nmr",
            f"{NMR_SW}:PORT_CMD",
            1,
            f"{NMR_SW}:MASK_RBV",
            3,
        ),

        # Deliberate second confirmation.
        _check(
            "reverify_nmr_disconnected",
            f"{NMR_SW}:MASK_RBV",
            3,
        ),

        # Must be checked immediately before ssRF connection.
        _check(
            "require_ssrf_connect_permit",
            f"{ILK}:SSRF_CONNECT_PERMIT",
            1,
        ),

        # ssRF PORT_CMD 0 -> Port 1 -> mask 0.
        _write_verify(
            "connect_ssrf",
            f"{SSRF_SW}:PORT_CMD",
            0,
            f"{SSRF_SW}:MASK_RBV",
            0,
        ),

        # --------------------------------------------------------------
        # Enable modulation.
        # --------------------------------------------------------------

        _write_verify(
            "wavegen_on",
            f"{WGEN}:OUTPUT_CMD",
            1,
            f"{WGEN}:OUTPUT_RBV",
            1,
        ),

        _write_verify(
            "smc_fm_on",
            f"{SMC}:FM_CMD",
            1,
            f"{SMC}:FM_RBV",
            1,
        ),

        # Must be checked immediately before RF ON.
        _check(
            "require_rf_enable_permit",
            f"{ILK}:RF_ENABLE_PERMIT",
            1,
        ),

        _write_verify(
            "smc_rf_on",
            f"{SMC}:RF_CMD",
            1,
            f"{SMC}:RF_RBV",
            1,
        ),

        SequenceStep(
            name="rf_settle",
            operation=OP_WAIT,
            command_value=100.0,
            monitor_pv=f"{ILK}:RF_ENABLE_PERMIT",
            verify_value=1,
            note="100 ms RF settle before sweep arm",
        ),

        _write_verify(
            "arm_sweep",
            f"{SMC}:SWEEP_ARM_CMD",
            1,
            f"{SMC}:SWEEP_ARM_RBV",
            1,
        ),

        # Recheck immediately before the irreversible burn trigger.
        _check(
            "recheck_rf_enable_permit",
            f"{ILK}:RF_ENABLE_PERMIT",
            1,
        ),

        SequenceStep(
            name="trigger_sweep",
            operation=OP_TRIGGER,
            command_pv=f"{SMC}:SWEEP_TRIGGER_CMD",
            command_value=1,
            monitor_pv=f"{ILK}:RF_ENABLE_PERMIT",
            verify_value=1,
            note=(
                "Momentary trigger; there is no dedicated trigger "
                "readback. Safety is established by pre-trigger state."
            ),
        ),

        SequenceStep(
            name="wait_for_sweep",
            operation=OP_WAIT,
            command_value=total_wait_ms,
            monitor_pv=f"{ILK}:RF_ENABLE_PERMIT",
            verify_value=1,
            note=(
                "Executor must continuously honor ABORT and fail closed "
                "if the interlock permit disappears."
            ),
        ),
    ]

    # Normal completion uses the same safety cleanup plan as ABORT/error.
    plan.extend(
        build_cleanup_plan()
    )

    return tuple(plan)
