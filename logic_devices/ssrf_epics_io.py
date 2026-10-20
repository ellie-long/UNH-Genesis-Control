"""
Genesis Phase-I ssRF EPICS I/O adapter.

This is the controlled EPICS boundary used by the future active
ssRF sequencer.

Safety properties
-----------------
- execution_enabled defaults to False.
- Reads are permitted while execution is disabled.
- Every write is rejected unless execution_enabled is exactly True.
- Only explicitly approved ssRF command PVs may be written.
- There is no EPICS PV that can enable this adapter.
- Device-level WRITE_ENABLED gates remain independent and must also
  permit writes before hardware can act.
"""

from __future__ import annotations

import asyncio
import math

import aioca


SMC = "UNH:GEN:SSRF:GEN"
WGEN = "UNH:GEN:SSRF:WGEN"
NMR_SW = "UNH:GEN:SSRF:NMR_SW"
SSRF_SW = "UNH:GEN:SSRF:RF_SW"


ALLOWED_COMMAND_PVS = frozenset(
    {
        # SMC100A
        f"{SMC}:FREQ_CMD",
        f"{SMC}:LEVEL_CMD",
        f"{SMC}:RF_CMD",
        f"{SMC}:FM_CMD",
        f"{SMC}:FM_SOURCE_CMD",
        f"{SMC}:FM_DEV_CMD",
        f"{SMC}:SWEEP_START_CMD",
        f"{SMC}:SWEEP_STOP_CMD",
        f"{SMC}:SWEEP_STEP_CMD",
        f"{SMC}:SWEEP_DWELL_CMD",
        f"{SMC}:SWEEP_SHAPE_CMD",
        f"{SMC}:SWEEP_ARM_CMD",
        f"{SMC}:SWEEP_TRIGGER_CMD",

        # Keysight WaveGen
        f"{WGEN}:OUTPUT_CMD",
        f"{WGEN}:PERIOD_CMD",
        f"{WGEN}:VHIGH_CMD",
        f"{WGEN}:VLOW_CMD",
        f"{WGEN}:WAVEFORM_CMD",
        f"{WGEN}:WAVEFORM_STAGE_CMD",
        f"{WGEN}:LOAD_CMD",

        # Mini-Circuits switches
        f"{NMR_SW}:PORT_CMD",
        f"{SSRF_SW}:PORT_CMD",
    }
)


class EpicsExecutionDisabled(RuntimeError):
    """Raised when an EPICS write is attempted while disabled."""


class EpicsWriteNotAllowed(RuntimeError):
    """Raised when a PV is outside the explicit write allowlist."""


class EpicsIO:
    """
    Narrow EPICS read/write adapter for the ssRF sequence executor.

    execution_enabled is intentionally a process-local Python setting.
    It is not controlled by an EPICS PV.
    """

    def __init__(
        self,
        *,
        execution_enabled=False,
        timeout_s=2.0,
    ):
        if not isinstance(
            execution_enabled,
            bool,
        ):
            raise TypeError(
                "execution_enabled must be bool"
            )

        self._execution_enabled = (
            execution_enabled
        )

        self.timeout_s = float(
            timeout_s
        )

        if self.timeout_s <= 0:
            raise ValueError(
                "timeout_s must be positive"
            )

    @property
    def execution_enabled(self):
        return self._execution_enabled

    async def read(self, pv):
        """
        Read one EPICS PV.

        Reads remain available even when execution is disabled.
        """

        return await aioca.caget(
            pv,
            timeout=self.timeout_s,
        )

    async def read_safety(self, pv):
        """Read scalar value with actual alarm status/severity (aioca 1.8.1).

        Raw reads remain unchanged. Safety consumers must never substitute a
        raw read when metadata is missing. Reject all alarms, as intended by
        the existing executor/manual verification policy.
        """
        value = await aioca.caget(pv, format=aioca.FORMAT_TIME,
                                 timeout=self.timeout_s)
        if not all(hasattr(value, name) for name in ("severity", "status", "ok")):
            raise RuntimeError(f"{pv}: safety metadata unavailable")
        if not value.ok or value.severity != 0 or value.status != 0:
            raise RuntimeError(f"{pv}: invalid/alarmed readback severity={value.severity} status={value.status}")
        if isinstance(value, str):
            return value
        try:
            if not math.isfinite(float(value)):
                raise ValueError("nonfinite")
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"{pv}: invalid safety scalar value") from exc
        return value

    async def write(
        self,
        pv,
        value,
    ):
        """
        Write one approved command PV.

        Two checks occur before aioca.caput:
        1. local execution gate
        2. explicit PV allowlist
        """

        if self._execution_enabled is not True:
            raise EpicsExecutionDisabled(
                "ssRF EPICS execution is disabled"
            )

        if pv not in ALLOWED_COMMAND_PVS:
            raise EpicsWriteNotAllowed(
                f"EPICS write not allowed: {pv}"
            )

        await aioca.caput(
            pv,
            value,
            wait=True,
            timeout=self.timeout_s,
        )

    async def sleep(
        self,
        seconds,
    ):
        await asyncio.sleep(
            float(seconds)
        )
