"""
Genesis Phase-I ssRF sequence execution engine.

This module executes the declarative plan from ssrf_sequence_plan.

Important:
- No EPICS dependency exists here.
- Hardware I/O is injected through read/write/sleep callables.
- READINESS_CHECK occurs before any command.
- Command writes are followed by authoritative readback verification.
- RF-enable permit is monitored during the active burn.
- Normal completion, failure, and abort use the same cleanup plan.
- Cleanup attempts all de-energizing commands before its verification barrier.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass

from logic_devices.ssrf_sequence_plan import (
    ILK,
    WGEN,
    OP_BEST_EFFORT_WRITE,
    OP_CHECK,
    OP_READINESS_CHECK,
    OP_SAFE_VOLTAGE_WINDOW,
    OP_TRIGGER,
    OP_WAIT,
    OP_WRITE_VERIFY,
)


STATE_COMPLETE = "COMPLETE"
STATE_FAILED = "FAILED"
STATE_ABORTED = "ABORTED"
STATE_CLEANUP_FAILED = "CLEANUP_FAILED"


class SequenceExecutionError(RuntimeError):
    """Sequence execution or verification failure."""


class SequenceAbortError(SequenceExecutionError):
    """Sequence abort request."""


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    state: str
    reason: str
    completed_steps: tuple[str, ...]
    failed_step: str = ""
    cleanup_attempted: bool = False
    cleanup_ok: bool = False
    warnings: tuple[str, ...] = ()


class SequenceExecutor:
    """
    Execute a frozen ssRF sequence plan using injected asynchronous I/O.

    Required callables:
        read(pv) -> value
        write(pv, value)
        sleep(seconds)

    Optional callables:
        readiness_check() -> bool or (bool, reason)
        abort_requested() -> bool
    """

    def __init__(
        self,
        *,
        read,
        write,
        sleep,
        readiness_check=None,
        abort_requested=None,
        verify_timeout_s=2.0,
        verify_interval_s=0.05,
        monitor_interval_s=0.05,
        close_rel_tol=1e-6,
        close_abs_tol=1e-6,
    ):
        self.read = read
        self.write = write
        self.sleep = sleep

        self.readiness_check = readiness_check
        self.abort_requested = abort_requested

        self.verify_timeout_s = float(
            verify_timeout_s
        )
        self.verify_interval_s = float(
            verify_interval_s
        )
        self.monitor_interval_s = float(
            monitor_interval_s
        )

        self.close_rel_tol = float(
            close_rel_tol
        )
        self.close_abs_tol = float(
            close_abs_tol
        )

        if self.verify_timeout_s <= 0:
            raise ValueError(
                "verify_timeout_s must be positive"
            )

        if self.verify_interval_s <= 0:
            raise ValueError(
                "verify_interval_s must be positive"
            )

        if self.monitor_interval_s <= 0:
            raise ValueError(
                "monitor_interval_s must be positive"
            )

        self._warnings = []
        self._completed = []
        self._writes_started = False
        self._current_step = ""

    # ------------------------------------------------------------
    # Generic callable handling
    # ------------------------------------------------------------

    @staticmethod
    async def _maybe_await(value):
        if inspect.isawaitable(value):
            return await value
        return value

    async def _read(self, pv):
        value = await self._maybe_await(
            self.read(pv)
        )

        if value is None:
            raise SequenceExecutionError(
                f"{pv} unavailable"
            )

        return value

    async def _write(self, pv, value):
        self._writes_started = True

        return await self._maybe_await(
            self.write(
                pv,
                value,
            )
        )

    async def _sleep(self, seconds):
        return await self._maybe_await(
            self.sleep(float(seconds))
        )

    # ------------------------------------------------------------
    # Comparison / verification
    # ------------------------------------------------------------

    def _matches(
        self,
        actual,
        rule,
        expected,
    ):
        if rule == "eq":
            return actual == expected

        if rule == "close":
            return math.isclose(
                float(actual),
                float(expected),
                rel_tol=self.close_rel_tol,
                abs_tol=self.close_abs_tol,
            )

        if rule == "gt":
            return (
                float(actual)
                > float(expected)
            )

        raise SequenceExecutionError(
            f"unsupported verification rule: {rule}"
        )

    async def _verify(
        self,
        pv,
        rule,
        expected,
    ):
        attempts = max(
            1,
            int(
                self.verify_timeout_s
                / self.verify_interval_s
            ) + 1,
        )

        actual = None

        for attempt in range(attempts):
            actual = await self._read(pv)

            if self._matches(
                actual,
                rule,
                expected,
            ):
                return actual

            if attempt + 1 < attempts:
                await self._sleep(
                    self.verify_interval_s
                )

        raise SequenceExecutionError(
            f"{pv} verification failed: "
            f"actual={actual!r}, "
            f"rule={rule}, "
            f"expected={expected!r}"
        )

    async def _assert_now(
        self,
        pv,
        expected,
    ):
        actual = await self._read(pv)

        if actual != expected:
            raise SequenceExecutionError(
                f"{pv} safety monitor failed: "
                f"actual={actual!r}, "
                f"expected={expected!r}"
            )

        return actual

    # ------------------------------------------------------------
    # Abort / readiness
    # ------------------------------------------------------------

    async def _check_abort(self):
        if self.abort_requested is None:
            return

        requested = await self._maybe_await(
            self.abort_requested()
        )

        if bool(requested):
            raise SequenceAbortError(
                "abort requested"
            )

    async def _check_readiness(self):
        if self.readiness_check is None:
            raise SequenceExecutionError(
                "readiness callback not configured"
            )

        result = await self._maybe_await(
            self.readiness_check()
        )

        if isinstance(result, tuple):
            ready = bool(result[0])
            reason = str(result[1])
        else:
            ready = bool(result)
            reason = (
                "burn ready"
                if ready
                else "burn not ready"
            )

        if not ready:
            raise SequenceExecutionError(
                reason
            )

    # ------------------------------------------------------------
    # Monitored waits
    # ------------------------------------------------------------

    async def _monitored_wait(
        self,
        duration_ms,
        monitor_pv,
        expected,
    ):
        remaining = (
            float(duration_ms)
            / 1000.0
        )

        if remaining < 0:
            raise SequenceExecutionError(
                "wait duration cannot be negative"
            )

        await self._check_abort()

        if monitor_pv:
            await self._assert_now(
                monitor_pv,
                expected,
            )

        while remaining > 0:
            chunk = min(
                remaining,
                self.monitor_interval_s,
            )

            await self._sleep(chunk)

            remaining -= chunk

            await self._check_abort()

            if monitor_pv:
                await self._assert_now(
                    monitor_pv,
                    expected,
                )

    # ------------------------------------------------------------
    # Keysight voltage-window handling
    # ------------------------------------------------------------

    async def _safe_voltage_window(
        self,
        target_low,
        target_high,
    ):
        target_low = float(target_low)
        target_high = float(target_high)

        if not target_low < target_high:
            raise SequenceExecutionError(
                "target voltage window must satisfy "
                "VLOW < VHIGH"
            )

        low_rbv = f"{WGEN}:VLOW_RBV"
        high_rbv = f"{WGEN}:VHIGH_RBV"

        low_cmd = f"{WGEN}:VLOW_CMD"
        high_cmd = f"{WGEN}:VHIGH_CMD"

        current_low = float(
            await self._read(low_rbv)
        )
        current_high = float(
            await self._read(high_rbv)
        )

        if not current_low < current_high:
            raise SequenceExecutionError(
                "current Keysight voltage window "
                "is invalid"
            )

        # Preserve VLOW < VHIGH after every individual write.
        #
        # If the requested low value still fits below the current
        # high value, lower/set VLOW first. Otherwise VHIGH must be
        # raised first before VLOW can move upward.
        if target_low < current_high:
            operations = (
                (
                    low_cmd,
                    target_low,
                    low_rbv,
                ),
                (
                    high_cmd,
                    target_high,
                    high_rbv,
                ),
            )
        else:
            operations = (
                (
                    high_cmd,
                    target_high,
                    high_rbv,
                ),
                (
                    low_cmd,
                    target_low,
                    low_rbv,
                ),
            )

        for command_pv, value, verify_pv in operations:
            await self._write(
                command_pv,
                value,
            )

            await self._verify(
                verify_pv,
                "close",
                value,
            )

            current_low = float(
                await self._read(low_rbv)
            )
            current_high = float(
                await self._read(high_rbv)
            )

            if not current_low < current_high:
                raise SequenceExecutionError(
                    "Keysight voltage ordering "
                    "became invalid"
                )

        final_low = float(
            await self._read(low_rbv)
        )
        final_high = float(
            await self._read(high_rbv)
        )

        if not math.isclose(
            final_low,
            target_low,
            rel_tol=self.close_rel_tol,
            abs_tol=self.close_abs_tol,
        ):
            raise SequenceExecutionError(
                "final VLOW verification failed"
            )

        if not math.isclose(
            final_high,
            target_high,
            rel_tol=self.close_rel_tol,
            abs_tol=self.close_abs_tol,
        ):
            raise SequenceExecutionError(
                "final VHIGH verification failed"
            )

    # ------------------------------------------------------------
    # One sequence step
    # ------------------------------------------------------------

    async def _execute_step(
        self,
        step,
        *,
        cleanup_mode=False,
    ):
        self._current_step = step.name

        if not cleanup_mode:
            await self._check_abort()

        operation = step.operation

        if operation == OP_READINESS_CHECK:
            await self._check_readiness()
            return

        if operation == OP_CHECK:
            await self._verify(
                step.verify_pv,
                step.verify_rule,
                step.verify_value,
            )
            return

        if operation == OP_WRITE_VERIFY:
            await self._write(
                step.command_pv,
                step.command_value,
            )

            await self._verify(
                step.verify_pv,
                step.verify_rule,
                step.verify_value,
            )
            return

        if operation == OP_BEST_EFFORT_WRITE:
            try:
                await self._write(
                    step.command_pv,
                    step.command_value,
                )
            except Exception as exc:
                self._warnings.append(
                    f"{step.name}: {exc}"
                )
            return

        if operation == OP_SAFE_VOLTAGE_WINDOW:
            target_low, target_high = (
                step.command_value
            )

            await self._safe_voltage_window(
                target_low,
                target_high,
            )
            return

        if operation == OP_TRIGGER:
            if step.monitor_pv:
                await self._assert_now(
                    step.monitor_pv,
                    step.verify_value,
                )

            await self._write(
                step.command_pv,
                step.command_value,
            )
            return

        if operation == OP_WAIT:
            await self._monitored_wait(
                step.command_value,
                step.monitor_pv,
                step.verify_value,
            )
            return

        raise SequenceExecutionError(
            f"unsupported sequence operation: "
            f"{operation}"
        )

    # ------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------

    async def _run_cleanup(
        self,
        cleanup_steps,
    ):
        try:
            for step in cleanup_steps:
                await self._execute_step(
                    step,
                    cleanup_mode=True,
                )
                self._completed.append(
                    step.name
                )

            return True, ""

        except Exception as exc:
            return (
                False,
                (
                    f"{self._current_step}: "
                    f"{exc}"
                ),
            )

    # ------------------------------------------------------------
    # Full burn
    # ------------------------------------------------------------

    async def run(self, plan):
        plan = tuple(plan)

        self._warnings = []
        self._completed = []
        self._writes_started = False
        self._current_step = ""

        cleanup_index = None

        for index, step in enumerate(plan):
            if step.name.startswith(
                "cleanup_"
            ):
                cleanup_index = index
                break

        if cleanup_index is None:
            raise ValueError(
                "sequence plan has no cleanup section"
            )

        nominal_steps = plan[
            :cleanup_index
        ]
        cleanup_steps = plan[
            cleanup_index:
        ]

        if not cleanup_steps:
            raise ValueError(
                "cleanup section is empty"
            )

        if any(
            not step.name.startswith(
                "cleanup_"
            )
            for step in cleanup_steps
        ):
            raise ValueError(
                "non-cleanup step found after "
                "cleanup section begins"
            )

        try:
            for step in nominal_steps:
                await self._execute_step(step)
                self._completed.append(
                    step.name
                )

        except SequenceAbortError as exc:
            failed_step = (
                self._current_step
            )

            if not self._writes_started:
                return ExecutionResult(
                    ok=False,
                    state=STATE_ABORTED,
                    reason=str(exc),
                    completed_steps=tuple(
                        self._completed
                    ),
                    failed_step=failed_step,
                    cleanup_attempted=False,
                    cleanup_ok=False,
                    warnings=tuple(
                        self._warnings
                    ),
                )

            cleanup_ok, cleanup_reason = (
                await self._run_cleanup(
                    cleanup_steps
                )
            )

            reason = str(exc)

            if not cleanup_ok:
                reason = (
                    f"{reason}; cleanup failed: "
                    f"{cleanup_reason}"
                )

            return ExecutionResult(
                ok=False,
                state=(
                    STATE_ABORTED
                    if cleanup_ok
                    else STATE_CLEANUP_FAILED
                ),
                reason=reason,
                completed_steps=tuple(
                    self._completed
                ),
                failed_step=failed_step,
                cleanup_attempted=True,
                cleanup_ok=cleanup_ok,
                warnings=tuple(
                    self._warnings
                ),
            )

        except Exception as exc:
            failed_step = (
                self._current_step
            )

            # Most importantly: a failed readiness check before any
            # command must not cause cleanup writes.
            if not self._writes_started:
                return ExecutionResult(
                    ok=False,
                    state=STATE_FAILED,
                    reason=str(exc),
                    completed_steps=tuple(
                        self._completed
                    ),
                    failed_step=failed_step,
                    cleanup_attempted=False,
                    cleanup_ok=False,
                    warnings=tuple(
                        self._warnings
                    ),
                )

            cleanup_ok, cleanup_reason = (
                await self._run_cleanup(
                    cleanup_steps
                )
            )

            reason = str(exc)

            if not cleanup_ok:
                reason = (
                    f"{reason}; cleanup failed: "
                    f"{cleanup_reason}"
                )

            return ExecutionResult(
                ok=False,
                state=(
                    STATE_FAILED
                    if cleanup_ok
                    else STATE_CLEANUP_FAILED
                ),
                reason=reason,
                completed_steps=tuple(
                    self._completed
                ),
                failed_step=failed_step,
                cleanup_attempted=True,
                cleanup_ok=cleanup_ok,
                warnings=tuple(
                    self._warnings
                ),
            )

        # Nominal burn has completed. Always run the same cleanup.
        cleanup_ok, cleanup_reason = (
            await self._run_cleanup(
                cleanup_steps
            )
        )

        if not cleanup_ok:
            return ExecutionResult(
                ok=False,
                state=STATE_CLEANUP_FAILED,
                reason=cleanup_reason,
                completed_steps=tuple(
                    self._completed
                ),
                failed_step=self._current_step,
                cleanup_attempted=True,
                cleanup_ok=False,
                warnings=tuple(
                    self._warnings
                ),
            )

        return ExecutionResult(
            ok=True,
            state=STATE_COMPLETE,
            reason="burn sequence complete",
            completed_steps=tuple(
                self._completed
            ),
            cleanup_attempted=True,
            cleanup_ok=True,
            warnings=tuple(
                self._warnings
            ),
        )
