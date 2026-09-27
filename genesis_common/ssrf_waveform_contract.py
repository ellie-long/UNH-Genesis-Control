"""
Genesis ssRF waveform-staging contract.

This module contains no EPICS I/O and no hardware I/O.

The adaptive analysis layer may submit a normalized waveform only to the
sequencer request PV.  Only the sequencer execution boundary may forward
that validated request to the protected WaveGen WAVEFORM_CMD record.

WAVEFORM_CMD remains IOC-memory staging only.  LOAD_CMD is a separate
hardware-commit operation.
"""

from __future__ import annotations

import numpy as np


WAVEFORM_POINTS = 8192

SEQUENCER_WAVEFORM_STAGE_REQUEST_PV = (
    "UNH:GEN:SSRF:SEQ:WAVEFORM_STAGE_REQ"
)

WAVEGEN_WAVEFORM_CMD_PV = (
    "UNH:GEN:SSRF:WGEN:WAVEFORM_CMD"
)

WAVEGEN_WAVEFORM_POINTS_PV = (
    "UNH:GEN:SSRF:WGEN:WAVEFORM_POINTS"
)

WAVEGEN_WAVEFORM_STAGE_ID_PV = (
    "UNH:GEN:SSRF:WGEN:WAVEFORM_STAGE_ID"
)


class WaveformContractError(ValueError):
    """Waveform does not satisfy the Genesis staging contract."""


def validate_normalized_waveform(
    values,
    *,
    exact_points=WAVEFORM_POINTS,
):
    """
    Return a defensive float32 copy of one valid normalized waveform.

    Requirements:
    - exactly 8192 points by default
    - all finite
    - every sample in [-1, +1]
    """

    array = np.asarray(
        values,
        dtype=np.float32,
    ).reshape(-1)

    if array.size != int(exact_points):
        raise WaveformContractError(
            "adaptive waveform must contain exactly "
            f"{int(exact_points)} points"
        )

    if not np.all(
        np.isfinite(array)
    ):
        raise WaveformContractError(
            "waveform contains non-finite values"
        )

    if (
        np.min(array) < -1.0
        or np.max(array) > 1.0
    ):
        raise WaveformContractError(
            "waveform exceeds normalized [-1, 1] range"
        )

    return array.copy()
