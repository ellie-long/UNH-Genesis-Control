"""
Safe Genesis ssRF waveform staging boundary.

This layer may stage normalized ARB data into WaveGen IOC memory.

It has no authority to:
- commit the waveform to hardware
- modify WaveGen scalar hardware settings
- enable WaveGen output
- operate the SMC100A
- operate RF switches
- arm or start a burn
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect

import numpy as np


WAVEFORM_CMD_PV = (
    "UNH:GEN:SSRF:WGEN:WAVEFORM_CMD"
)

ALLOWED_STAGE_PVS = frozenset(
    {
        WAVEFORM_CMD_PV,
    }
)


class WaveformStageError(RuntimeError):
    """Waveform could not be safely staged."""


@dataclass(frozen=True)
class StageReceipt:
    pv: str
    points: int
    minimum: float
    maximum: float
    material: str
    case: str
    fit_ts: str | None


class WaveformStager:
    """
    Narrow IOC-memory waveform stager.

    The injected writer receives exactly one allowed PV:
    WAVEFORM_CMD.
    """

    def __init__(
        self,
        *,
        write,
    ):
        if not callable(write):
            raise TypeError(
                "write must be callable"
            )

        self._write = write

    async def stage(
        self,
        plan,
    ) -> StageReceipt:

        plan.validate()

        values = np.asarray(
            plan.normalized_samples,
            dtype=np.float32,
        ).reshape(-1)

        if values.size != 8192:
            raise WaveformStageError(
                "adaptive waveform must "
                "contain exactly 8192 points"
            )

        if not np.all(
            np.isfinite(values)
        ):
            raise WaveformStageError(
                "waveform contains "
                "non-finite values"
            )

        if (
            np.min(values) < -1.0
            or np.max(values) > 1.0
        ):
            raise WaveformStageError(
                "waveform exceeds "
                "normalized [-1, 1] range"
            )

        result = self._write(
            WAVEFORM_CMD_PV,
            values.copy(),
        )

        if inspect.isawaitable(result):
            await result

        return StageReceipt(
            pv=WAVEFORM_CMD_PV,
            points=int(values.size),
            minimum=float(
                np.min(values)
            ),
            maximum=float(
                np.max(values)
            ),
            material=str(
                plan.material
            ),
            case=str(
                plan.case
            ),
            fit_ts=plan.fit_ts,
        )
