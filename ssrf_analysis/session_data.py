"""
Genesis Phase-I ssRF session scientific-data products.

The structured JSONL event log stores metadata and correlation state.
This module stores the larger scientific arrays needed for later plotting.

It performs no EPICS access and no hardware access.

Default layout:

    archive/sessions/ssrf/<session_id>/
        fit_data.npz
        waveform_data.npz

The files are NumPy NPZ containers written with allow_pickle-free numeric
arrays only. JSONL events should reference these paths rather than embedding
large arrays directly.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Any, Mapping

import numpy as np

from archive.data_paths import ssrf_session_dir, resolve_recording_path


DEFAULT_SESSION_DATA_ROOT = ssrf_session_dir()

_SAFE_SESSION_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
)


class SessionDataError(RuntimeError):
    """Scientific session data could not be validated or persisted."""


@dataclass(frozen=True)
class SessionDataProducts:
    session_id: str
    directory: str
    fit_data_path: str | None
    waveform_data_path: str | None

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(self)


def validate_session_id(
    session_id: str,
) -> str:
    value = str(
        session_id
    ).strip()

    if not value:
        raise ValueError(
            "session_id must not be empty"
        )

    if value in {".", ".."}:
        raise ValueError(
            "invalid session_id"
        )

    if not _SAFE_SESSION_ID.fullmatch(
        value
    ):
        raise ValueError(
            "session_id may contain only "
            "letters, digits, '.', '_', and '-'"
        )

    return value


def _numeric_array(
    value: Any,
    *,
    name: str,
) -> np.ndarray | None:
    if value is None:
        return None

    try:
        array = np.asarray(
            value
        )
    except Exception as exc:
        raise SessionDataError(
            f"{name} could not be converted to an array"
        ) from exc

    if array.dtype == object:
        raise SessionDataError(
            f"{name} object arrays are not allowed"
        )

    if array.ndim != 1:
        raise SessionDataError(
            f"{name} must be one-dimensional"
        )

    try:
        finite = np.isfinite(
            array.astype(
                np.float64,
                copy=False,
            )
        )
    except Exception as exc:
        raise SessionDataError(
            f"{name} must be numeric"
        ) from exc

    if not np.all(finite):
        raise SessionDataError(
            f"{name} contains non-finite values"
        )

    return np.asarray(
        array,
        dtype=np.float64,
    )


def _write_npz(
    path: Path,
    arrays: Mapping[
        str,
        np.ndarray,
    ],
):
    if not arrays:
        return None

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        np.savez_compressed(
            path,
            **arrays,
        )
    except OSError as exc:
        raise SessionDataError(
            f"failed to write session data: {path}"
        ) from exc

    return path


class SessionDataWriter:
    """Write scientific arrays for one correlated ssRF session."""

    FIT_ARRAY_FIELDS = (
        "freqs_mhz",
        "data",
        "total_fit",
        "I_plus_primary",
        "I_minus_primary",
        "I_plus_secondary",
        "I_minus_secondary",
        "raw",
    )

    PLAN_ARRAY_FIELDS = (
        "f_peak_mhz",
        "f_shoulder_mhz",
        "peak_weights",
        "shoulder_weights",
        "control_time_s",
        "control_offset_mhz",
        "normalized_samples",
        "physical_voltage_v",
    )

    def __init__(
        self,
        *,
        root: str | Path | None = None,
    ):
        self.root = resolve_recording_path(root, ssrf_session_dir())

    def session_directory(
        self,
        session_id: str,
    ) -> Path:
        session_id = validate_session_id(
            session_id
        )

        return (
            self.root
            / session_id
        )

    def write_fit_data(
        self,
        *,
        session_id: str,
        message: Mapping[
            str,
            Any,
        ],
    ) -> Path | None:
        if not isinstance(
            message,
            Mapping,
        ):
            raise TypeError(
                "message must be a mapping"
            )

        arrays = {}

        lengths = {}

        for name in self.FIT_ARRAY_FIELDS:
            array = _numeric_array(
                message.get(name),
                name=name,
            )

            if array is None:
                continue

            arrays[name] = array
            lengths[name] = int(
                array.size
            )

        # Frequency-indexed fitted/processed arrays should agree in length
        # whenever both are present. `raw` is intentionally excluded because
        # acquisition raw vectors may have a different representation.
        frequency_length = lengths.get(
            "freqs_mhz"
        )

        if frequency_length is not None:
            for name in (
                "data",
                "total_fit",
                "I_plus_primary",
                "I_minus_primary",
                "I_plus_secondary",
                "I_minus_secondary",
            ):
                length = lengths.get(
                    name
                )

                if (
                    length is not None
                    and length
                    != frequency_length
                ):
                    raise SessionDataError(
                        f"{name} length {length} "
                        "does not match freqs_mhz "
                        f"length {frequency_length}"
                    )

        path = (
            self.session_directory(
                session_id
            )
            / "fit_data.npz"
        )

        return _write_npz(
            path,
            arrays,
        )

    def write_waveform_data(
        self,
        *,
        session_id: str,
        plan,
    ) -> Path:
        arrays = {}

        for name in self.PLAN_ARRAY_FIELDS:
            if not hasattr(
                plan,
                name,
            ):
                raise SessionDataError(
                    "WaveformPlan missing expected "
                    f"field: {name}"
                )

            array = _numeric_array(
                getattr(
                    plan,
                    name,
                ),
                name=name,
            )

            if array is None:
                raise SessionDataError(
                    "WaveformPlan field unexpectedly "
                    f"empty: {name}"
                )

            arrays[name] = array

        waveform_points = int(
            getattr(
                plan,
                "waveform_points",
            )
        )

        # ----------------------------------------------------
        # The planner contains TWO waveform representations.
        #
        # 1. control_time_s / control_offset_mhz:
        #    legacy pre-resampling control trajectory.
        #
        # 2. normalized_samples / physical_voltage_v:
        #    final uniformly resampled Keysight ARB.
        #
        # The control trajectory therefore does NOT have to
        # contain waveform_points samples.
        # ----------------------------------------------------

        if (
            arrays["control_time_s"].size
            != arrays["control_offset_mhz"].size
        ):
            raise SessionDataError(
                "control_time_s and control_offset_mhz "
                "must have matching lengths"
            )

        if arrays["control_time_s"].size < 2:
            raise SessionDataError(
                "control trajectory must contain "
                "at least two points"
            )

        if (
            arrays["f_peak_mhz"].size
            != arrays["peak_weights"].size
        ):
            raise SessionDataError(
                "f_peak_mhz and peak_weights "
                "must have matching lengths"
            )

        if (
            arrays["f_shoulder_mhz"].size
            != arrays["shoulder_weights"].size
        ):
            raise SessionDataError(
                "f_shoulder_mhz and shoulder_weights "
                "must have matching lengths"
            )

        for name in (
            "normalized_samples",
            "physical_voltage_v",
        ):
            if arrays[name].size != waveform_points:
                raise SessionDataError(
                    f"{name} length "
                    f"{arrays[name].size} does not "
                    "match waveform_points "
                    f"{waveform_points}"
                )

        if waveform_points != 8192:
            raise SessionDataError(
                "adaptive waveform plan must specify "
                "exactly 8192 waveform_points"
            )

        if (
            arrays["normalized_samples"].size
            != 8192
        ):
            raise SessionDataError(
                "adaptive waveform data product "
                "must contain exactly 8192 samples"
            )

        path = (
            self.session_directory(
                session_id
            )
            / "waveform_data.npz"
        )

        written = _write_npz(
            path,
            arrays,
        )

        if written is None:
            raise SessionDataError(
                "waveform data unexpectedly empty"
            )

        return written

    def write_products(
        self,
        *,
        session_id: str,
        message: Mapping[
            str,
            Any,
        ],
        plan,
    ) -> SessionDataProducts:
        session_id = validate_session_id(
            session_id
        )

        fit_path = self.write_fit_data(
            session_id=session_id,
            message=message,
        )

        waveform_path = (
            self.write_waveform_data(
                session_id=session_id,
                plan=plan,
            )
        )

        directory = self.session_directory(
            session_id
        )

        return SessionDataProducts(
            session_id=session_id,
            directory=str(directory),
            fit_data_path=(
                str(fit_path)
                if fit_path is not None
                else None
            ),
            waveform_data_path=(
                str(waveform_path)
            ),
        )


def load_npz_numeric(
    path: str | Path,
) -> dict[
    str,
    np.ndarray,
]:
    """
    Read one Genesis session NPZ without pickle/object loading.
    """

    path = Path(
        path
    )

    if not path.is_file():
        raise FileNotFoundError(
            path
        )

    result = {}

    try:
        with np.load(
            path,
            allow_pickle=False,
        ) as data:
            for name in data.files:
                array = np.asarray(
                    data[name]
                )

                if array.dtype == object:
                    raise SessionDataError(
                        f"object array rejected: {name}"
                    )

                result[name] = array.copy()
    except ValueError as exc:
        raise SessionDataError(
            f"invalid session NPZ: {path}"
        ) from exc

    return result
