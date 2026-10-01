"""Guarded manual ssRF operation. Injected I/O; no implicit hardware access."""
import math

from logic_devices.ssrf_sequence_executor import SequenceExecutor
from logic_devices.ssrf_sequence_plan import (
    SMC, WGEN, NMR_SW, SSRF_SW, ILK, _check, _write_verify,
    build_cleanup_plan, SequenceStep, OP_READINESS_CHECK,
)


class ManualRF:
    def __init__(self, io, abort_requested):
        self.io = io
        self.armed = False
        self.state = "DISARMED"
        self.reason = "Explicit manual ARM required"
        self.fault = ""
        self.executor = SequenceExecutor(
            read=io.read_safety, write=io.write, sleep=io.sleep,
            abort_requested=abort_requested, readiness_check=self.energize_ready,
        )

    async def require(self, pv, value):
        actual = await self.io.read_safety(pv)
        if getattr(actual, "severity", 0) or actual != value:
            raise RuntimeError(f"{pv}: expected {value}, got {actual}")

    async def safety(self):
        for prefix in (SMC, NMR_SW, SSRF_SW):
            await self.require(f"{prefix}:COMM", 1)
            await self.require(f"{prefix}:WRITE_ENABLED", 1)
        await self.require(f"{ILK}:INPUTS_OK", 1)
        await self.require(f"{ILK}:INTERLOCK_OK", 1)

    async def energize_ready(self):
        await self.safety()
        await self.require(f"{NMR_SW}:MASK_RBV", 3)
        await self.require(f"{SSRF_SW}:MASK_RBV", 0)
        await self.require(f"{SMC}:SWEEP_ARM_RBV", 0)
        return True, "Manual safety valid"

    async def idle_safety(self):
        await self.safety()
        for pv in (f"{SMC}:RF_RBV", f"{SMC}:FM_RBV",
                   f"{SMC}:SWEEP_ARM_RBV", f"{WGEN}:OUTPUT_RBV"):
            await self.require(pv, 0)
        # WaveGen is required only to establish/verify the safe source state.
        await self.require(f"{WGEN}:COMM", 1)
        await self.require(f"{SSRF_SW}:MASK_RBV", 3)

    async def arm(self):
        if not self.io.execution_enabled:
            raise RuntimeError("Execution disabled")
        await self.idle_safety()
        self.fault = ""
        self.armed = True
        self.state, self.reason = "ARMED", "Explicit RF ON required"

    async def transition(self, plan):
        result = await self.executor.run_preparation(plan, build_cleanup_plan())
        if not result.ok:
            raise RuntimeError(result.reason)

    async def rf_on(self, carrier, level):
        if not self.armed:
            raise RuntimeError("Manual RF not armed")
        self.armed = False
        await self.idle_safety()
        if not math.isfinite(carrier) or not 0.1 <= carrier <= 3200:
            raise ValueError("Invalid carrier MHz")
        if not math.isfinite(level) or not -145 <= level <= 10:
            raise ValueError("Invalid RF level dBm")
        plan = (
            _write_verify("manual_carrier", f"{SMC}:FREQ_CMD", carrier,
                          f"{SMC}:FREQ_RBV", carrier, rule="close"),
            _write_verify("manual_level", f"{SMC}:LEVEL_CMD", level,
                          f"{SMC}:LEVEL_RBV", level, rule="close"),
            _check("manual_verify_rf_off", f"{SMC}:RF_RBV", 0),
            _write_verify("manual_disconnect_nmr", f"{NMR_SW}:PORT_CMD", 1,
                          f"{NMR_SW}:MASK_RBV", 3),
            _check("manual_ssrf_permit", f"{ILK}:SSRF_CONNECT_PERMIT", 1),
            _write_verify("manual_connect_ssrf", f"{SSRF_SW}:PORT_CMD", 0,
                          f"{SSRF_SW}:MASK_RBV", 0),
            _check("manual_verify_fm_off", f"{SMC}:FM_RBV", 0),
            _check("manual_verify_wavegen_off", f"{WGEN}:OUTPUT_RBV", 0),
            SequenceStep("manual_safety", OP_READINESS_CHECK),
            _check("manual_rf_permit", f"{ILK}:RF_ENABLE_PERMIT", 1),
            _write_verify("manual_rf_on", f"{SMC}:RF_CMD", 1,
                          f"{SMC}:RF_RBV", 1),
        )
        await self.transition(plan)
        self.carrier, self.level = carrier, level
        self.state, self.reason = "RF_ON", "Manual RF active; FM OFF"

    async def monitor(self):
        await self.safety()
        await self.require(f"{ILK}:RF_ENABLE_PERMIT", 1)
        await self.require(f"{NMR_SW}:MASK_RBV", 3)
        await self.require(f"{SSRF_SW}:MASK_RBV", 0)
        await self.require(f"{SMC}:RF_RBV", 1)
        await self.require(f"{SMC}:SWEEP_ARM_RBV", 0)
        await self.require(f"{WGEN}:COMM", 1)
        for pv, expected in ((f"{SMC}:FREQ_RBV", self.carrier), (f"{SMC}:LEVEL_RBV", self.level)):
            actual = await self.io.read_safety(pv)
            if getattr(actual, "severity", 0) or not math.isclose(float(actual), expected, rel_tol=1e-6, abs_tol=1e-6):
                raise RuntimeError(f"{pv}: operating configuration changed")
        active = int(self.state == "RF_FM_ACTIVE")
        await self.require(f"{SMC}:FM_RBV", active)
        await self.require(f"{WGEN}:OUTPUT_RBV", active)
        if active:
            await self.require(f"{WGEN}:WRITE_ENABLED", 1)
            await self.require(f"{WGEN}:WAVEFORM_READY", 1)
            await self.require(f"{WGEN}:WAVEFORM_ID", self.waveform_id)
            await self.require(f"{SMC}:FM_SOURCE_RBV", 1)
            for pv, expected in self.fm_configuration:
                actual = await self.io.read_safety(pv)
                if getattr(actual, "severity", 0) or not math.isclose(float(actual), expected, rel_tol=1e-6, abs_tol=1e-6):
                    raise RuntimeError(f"{pv}: FM configuration changed")

    async def fm_on(self, parameters, prepared_id):
        if self.state != "RF_ON" or prepared_id < 1:
            raise RuntimeError("FM requires manual RF and prepared waveform")
        await self.monitor()
        await self.require(f"{WGEN}:WRITE_ENABLED", 1)
        from logic_devices.ssrf_sequence_core import validate_parameters
        valid, reason = validate_parameters(parameters)
        if not valid:
            raise ValueError(reason)
        self.waveform_id = prepared_id
        self.fm_configuration = (
            (f"{SMC}:FM_DEV_RBV", parameters.fm_deviation_khz),
            (f"{WGEN}:PERIOD_RBV", parameters.fm_period_s),
            (f"{WGEN}:VHIGH_RBV", parameters.vhigh_v),
            (f"{WGEN}:VLOW_RBV", parameters.vlow_v),
        )
        checks = (
            _check("fm_source", f"{SMC}:FM_SOURCE_RBV", 1),
            _check("fm_deviation", f"{SMC}:FM_DEV_RBV", parameters.fm_deviation_khz, rule="close"),
            _check("fm_period", f"{WGEN}:PERIOD_RBV", parameters.fm_period_s, rule="close"),
            _check("fm_high", f"{WGEN}:VHIGH_RBV", parameters.vhigh_v, rule="close"),
            _check("fm_low", f"{WGEN}:VLOW_RBV", parameters.vlow_v, rule="close"),
            _check("fm_waveform", f"{WGEN}:WAVEFORM_READY", 1),
            _check("fm_identity", f"{WGEN}:WAVEFORM_ID", prepared_id),
            _check("fm_points", f"{WGEN}:ARB_POINTS_RBV", 8192),
            SequenceStep("fm_safety", OP_READINESS_CHECK),
            _check("fm_rf_permit", f"{ILK}:RF_ENABLE_PERMIT", 1),
            _write_verify("fm_wavegen_on", f"{WGEN}:OUTPUT_CMD", 1, f"{WGEN}:OUTPUT_RBV", 1),
            _check("fm_recheck_permit", f"{ILK}:RF_ENABLE_PERMIT", 1),
            _write_verify("fm_on", f"{SMC}:FM_CMD", 1, f"{SMC}:FM_RBV", 1),
        )
        await self.transition(checks)
        self.state, self.reason = "RF_FM_ACTIVE", "Manual RF and external FM active"

    async def fm_off(self):
        if self.state != "RF_FM_ACTIVE":
            raise RuntimeError("Manual FM not active")
        await self.transition((
            _write_verify("fm_off", f"{SMC}:FM_CMD", 0, f"{SMC}:FM_RBV", 0),
            _write_verify("fm_wavegen_off", f"{WGEN}:OUTPUT_CMD", 0, f"{WGEN}:OUTPUT_RBV", 0),
        ))
        self.state, self.reason = "RF_ON", "Manual RF active; FM OFF"

    async def safe_off(self):
        self.armed = False
        self.state, self.reason = "STOPPING", "Verifying safe baseline"
        ok, reason = await self.executor.cleanup(build_cleanup_plan())
        self.state = "DISARMED" if ok else "CLEANUP_FAILED"
        self.reason = "Safe baseline verified" if ok else reason
        return ok
