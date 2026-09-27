"""
Safe Genesis ssRF waveform staging boundary.

This layer submits validated normalized ARB data to the Genesis
sequencer staging-request interface.

The analysis layer never writes the protected WaveGen WAVEFORM_CMD
record directly.

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

from genesis_common.ssrf_waveform_contract import (
    SEQUENCER_WAVEFORM_STAGE_REQUEST_PV,
)


WAVEFORM_STAGE_REQUEST_PV = (
    SEQUENCER_WAVEFORM_STAGE_REQUEST_PV
)

# Compatibility alias for existing callers/tests.
# This now names the SEQUENCER REQUEST PV, never the
# protected WaveGen device-command PV.
WAVEFORM_CMD_PV = WAVEFORM_STAGE_REQUEST_PV

ALLOWED_STAGE_PVS = frozenset(
    {
        WAVEFORM_STAGE_REQUEST_PV,
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
    Narrow sequencer-request waveform stager.

    The injected writer receives exactly one allowed PV:
    WAVEFORM_STAGE_REQUEST_PV.

    Only the sequencer may forward the request to the protected
    WaveGen WAVEFORM_CMD record.
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
            WAVEFORM_STAGE_REQUEST_PV,
            values.copy(),
        )

        if inspect.isawaitable(result):
            await result

        return StageReceipt(
            pv=WAVEFORM_STAGE_REQUEST_PV,
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
