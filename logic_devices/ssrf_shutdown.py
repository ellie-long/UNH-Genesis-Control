"""Injected-I/O shutdown policy; no owner termination until verified safe."""
import time
from contextlib import nullcontext
from logic_devices.ssrf_lifecycle import OwnershipLock
from logic_devices.ssrf_sequence_executor import SequenceExecutor
from logic_devices.ssrf_sequence_plan import build_cleanup_plan

SEQ = "UNH:GEN:SSRF:SEQ"


async def establish_safe_shutdown(io, timeout_s=30, request_abort=None,
                                  sequencer_expected=False, lock_directory=None,
                                  lifecycle_held=False):
    # The physical script's parent holds lifecycle through owner termination.
    # Standalone/test callers acquire it here through verification.
    guard = nullcontext() if lifecycle_held else OwnershipLock("lifecycle", lock_directory)
    with guard:
        return await _establish_safe_shutdown(io, timeout_s, request_abort,
                                             sequencer_expected, lock_directory)


async def _establish_safe_shutdown(io, timeout_s, request_abort,
                                  sequencer_expected, lock_directory):
    try:
        enabled = await io.read_safety(f"{SEQ}:EXECUTION_ENABLED")
    except Exception:
        enabled = None
    if enabled is None and sequencer_expected:
        raise RuntimeError("sequencer owner detected but execution PV unavailable; refusing concurrent fallback")
    if enabled == 1:
        # A reachable sequencer remains the sole ordering owner. Do not race it.
        baseline = int(await io.read_safety(f"{SEQ}:SAFE_OFF_ID"))
        if request_abort is None:
            from aioca import caput
            async def request_abort():
                await caput(f"{SEQ}:ABORT_CMD", 1, wait=True, timeout=3)
        await request_abort()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state = str(await io.read_safety(f"{SEQ}:MANUAL_STATE"))
            reason = str(await io.read_safety(f"{SEQ}:MANUAL_REASON"))
            acknowledged = int(await io.read_safety(f"{SEQ}:SAFE_OFF_ID")) > baseline
            if acknowledged and state == "CLEANUP_FAILED":
                raise RuntimeError(reason)
            if acknowledged and state == "DISARMED" and reason == "Safe baseline verified":
                break
            await io.sleep(0.1)
        else:
            raise RuntimeError("sequencer safe-off acknowledgment timed out")
        # Independently verify all source states, routing and restore permit.
        plan = build_cleanup_plan()
        for step in plan:
            if step.verify_pv:
                actual = await io.read_safety(step.verify_pv)
                if getattr(actual, "severity", 0) or actual != step.verify_value:
                    raise RuntimeError(f"{step.verify_pv}: expected {step.verify_value}, got {actual}")
        return
    if enabled is not None:
        raise RuntimeError("sequencer present but execution unavailable; retaining owners")
    # Acquire lifetime command ownership before ANY device command. A live
    # physical sequencer holds this same lock even if its PV is unreachable.
    with OwnershipLock("control", lock_directory):
        # Recheck under ownership; do not guess that a responding IOC is absent.
        try:
            await io.read_safety(f"{SEQ}:EXECUTION_ENABLED")
        except Exception:
            pass
        else:
            raise RuntimeError("sequencer became reachable; retry STOP through sequencer")
        executor = SequenceExecutor(read=io.read_safety, write=io.write, sleep=io.sleep)
        ok, reason = await executor.cleanup(build_cleanup_plan())
        if not ok:
            raise RuntimeError(reason)
