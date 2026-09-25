"""
Read-only Genesis ssRF session visualization helpers.

This module loads the structured JSONL event stream and the numeric NPZ
session products created by ssrf_analysis.fitter_stage_runtime.

It performs no EPICS access and no hardware access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ssrf_analysis.event_logger import read_jsonl
from ssrf_analysis.session_data import load_npz_numeric
from ssrf_analysis.session_record import (
    SessionRecord,
    build_session_record,
    group_session_records,
)


DEFAULT_EVENT_DIR = Path("archive/events/ssrf")


class SessionViewError(RuntimeError):
    """Session visualization data could not be loaded consistently."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_event_records(
    event_dir: str | Path = DEFAULT_EVENT_DIR,
) -> list[dict[str, Any]]:
    """
    Load all ssRF JSONL event files from one directory.

    Records are ordered by timestamp, with event_id as a stable tie-breaker.
    """

    event_dir = Path(event_dir)

    if not event_dir.exists():
        return []

    if not event_dir.is_dir():
        raise SessionViewError(
            f"event path is not a directory: {event_dir}"
        )

    records: list[dict[str, Any]] = []

    for path in sorted(
        event_dir.glob("*.jsonl")
    ):
        records.extend(
            read_jsonl(path)
        )

    records.sort(
        key=lambda row: (
            str(
                row.get(
                    "timestamp_utc",
                    "",
                )
            ),
            str(
                row.get(
                    "event_id",
                    "",
                )
            ),
        )
    )

    return records


def session_events(
    records: Iterable[
        Mapping[str, Any]
    ],
    session_id: str,
) -> list[dict[str, Any]]:
    session_id = str(
        session_id
    ).strip()

    if not session_id:
        raise ValueError(
            "session_id must not be empty"
        )

    selected = [
        dict(row)
        for row in records
        if str(
            row.get("session_id")
        )
        == session_id
    ]

    selected.sort(
        key=lambda row: (
            str(
                row.get(
                    "timestamp_utc",
                    "",
                )
            ),
            str(
                row.get(
                    "event_id",
                    "",
                )
            ),
        )
    )

    return selected


def session_summaries(
    records: Iterable[
        Mapping[str, Any]
    ],
) -> list[dict[str, Any]]:
    """
    Produce one compact selectable summary per session.
    """

    records = [
        dict(row)
        for row in records
    ]

    if not records:
        return []

    grouped = group_session_records(
        records
    )

    summaries = []

    for session_id, record in grouped.items():
        material = None
        case = None
        fit_ts = None
        scaling_verified = None

        if record.fit is not None:
            fit_data = record.fit.get(
                "data",
                {},
            )

            material = fit_data.get(
                "material"
            )

            fit_ts = fit_data.get(
                "fit_ts"
            )

        if record.waveform is not None:
            waveform_data = (
                record.waveform.get(
                    "data",
                    {},
                )
            )

            case = waveform_data.get(
                "case"
            )

            if material is None:
                material = (
                    waveform_data.get(
                        "material"
                    )
                )

            scaling_verified = (
                waveform_data.get(
                    "scaling_verified_for_hardware"
                )
            )

        summaries.append(
            {
                "session_id": session_id,
                "status": record.status,
                "started_utc": (
                    record.started_utc
                ),
                "completed_utc": (
                    record.completed_utc
                ),
                "event_count": (
                    record.event_count
                ),
                "material": material,
                "case": case,
                "fit_ts": fit_ts,
                "scaling_verified_for_hardware": (
                    scaling_verified
                ),
            }
        )

    summaries.sort(
        key=lambda item: (
            str(
                item.get(
                    "started_utc",
                    "",
                )
            ),
            item["session_id"],
        ),
        reverse=True,
    )

    return summaries


def _last_data_path(
    events: Iterable[
        Mapping[str, Any]
    ],
    *,
    event_type: str,
    key: str,
) -> str | None:
    for row in reversed(
        list(events)
    ):
        if (
            row.get("event_type")
            != event_type
        ):
            continue

        data = row.get(
            "data",
            {},
        )

        if not isinstance(
            data,
            Mapping,
        ):
            continue

        value = data.get(
            key
        )

        if value:
            return str(value)

    return None


def resolve_product_path(
    value: str | Path | None,
    *,
    repo_root: str | Path | None = None,
) -> Path | None:
    if value is None:
        return None

    path = Path(value)

    if path.is_absolute():
        return path

    root = (
        Path(repo_root)
        if repo_root is not None
        else _repo_root()
    )

    return (
        root
        / path
    ).resolve()


def load_session_bundle(
    *,
    event_dir: str | Path,
    session_id: str,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """
    Load one correlated session, including scientific NPZ products when present.
    """

    records = load_event_records(
        event_dir
    )

    events = session_events(
        records,
        session_id,
    )

    if not events:
        raise SessionViewError(
            f"session not found: {session_id}"
        )

    record = build_session_record(
        events
    )

    fit_path = resolve_product_path(
        _last_data_path(
            events,
            event_type="fit_received",
            key="fit_data_path",
        ),
        repo_root=repo_root,
    )

    waveform_path = (
        resolve_product_path(
            _last_data_path(
                events,
                event_type="waveform_planned",
                key="waveform_data_path",
            ),
            repo_root=repo_root,
        )
    )

    fit_data = {}

    if fit_path is not None:
        if not fit_path.is_file():
            raise SessionViewError(
                "fit_data_path does not exist: "
                f"{fit_path}"
            )

        fit_data = load_npz_numeric(
            fit_path
        )

    waveform_data = {}

    if waveform_path is not None:
        if not waveform_path.is_file():
            raise SessionViewError(
                "waveform_data_path does not exist: "
                f"{waveform_path}"
            )

        waveform_data = (
            load_npz_numeric(
                waveform_path
            )
        )

    return {
        "record": record,
        "events": events,
        "fit_data": fit_data,
        "waveform_data": waveform_data,
        "fit_data_path": fit_path,
        "waveform_data_path": (
            waveform_path
        ),
    }


def event_dataframe(
    events: Iterable[
        Mapping[str, Any]
    ],
) -> pd.DataFrame:
    rows = []

    for event in events:
        data = event.get(
            "data",
            {},
        )

        if not isinstance(
            data,
            Mapping,
        ):
            data = {}

        rows.append(
            {
                "timestamp_utc": (
                    event.get(
                        "timestamp_utc"
                    )
                ),
                "event_type": (
                    event.get(
                        "event_type"
                    )
                ),
                "level": event.get(
                    "level"
                ),
                "source": event.get(
                    "source"
                ),
                "event_id": event.get(
                    "event_id"
                ),
                "material": data.get(
                    "material"
                ),
                "case": data.get(
                    "case"
                ),
                "points": data.get(
                    "points",
                    data.get(
                        "waveform_points"
                    ),
                ),
                "status": data.get(
                    "status"
                ),
            }
        )

    frame = pd.DataFrame(
        rows
    )

    if (
        not frame.empty
        and "timestamp_utc"
        in frame
    ):
        frame[
            "timestamp_utc"
        ] = pd.to_datetime(
            frame[
                "timestamp_utc"
            ],
            utc=True,
            errors="coerce",
        )

    return frame


def create_nmr_fit_figure(
    fit_data: Mapping[
        str,
        np.ndarray,
    ],
):
    freqs = fit_data.get(
        "freqs_mhz"
    )

    if freqs is None:
        return None

    freqs = np.asarray(
        freqs
    )

    if freqs.size == 0:
        return None

    fig = go.Figure()

    trace_fields = (
        ("data", "Processed NMR"),
        ("total_fit", "Total fit"),
        (
            "I_plus_primary",
            "I+ primary",
        ),
        (
            "I_minus_primary",
            "I- primary",
        ),
        (
            "I_plus_secondary",
            "I+ secondary",
        ),
        (
            "I_minus_secondary",
            "I- secondary",
        ),
    )

    for key, label in trace_fields:
        values = fit_data.get(
            key
        )

        if values is None:
            continue

        values = np.asarray(
            values
        )

        if values.size != freqs.size:
            continue

        fig.add_trace(
            go.Scatter(
                x=freqs,
                y=values,
                mode="lines",
                name=label,
            )
        )

    if not fig.data:
        return None

    fig.update_layout(
        title="NMR spectrum and fit",
        xaxis_title="Frequency (MHz)",
        yaxis_title="Signal",
        hovermode="x unified",
        height=480,
    )

    return fig


def create_control_trajectory_figure(
    waveform_data: Mapping[
        str,
        np.ndarray,
    ],
):
    time_s = waveform_data.get(
        "control_time_s"
    )

    offset = waveform_data.get(
        "control_offset_mhz"
    )

    if (
        time_s is None
        or offset is None
    ):
        return None

    time_s = np.asarray(
        time_s
    )

    offset = np.asarray(
        offset
    )

    if (
        time_s.size == 0
        or time_s.size
        != offset.size
    ):
        return None

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=time_s * 1e3,
            y=offset,
            mode="lines+markers",
            name="Adaptive control trajectory",
        )
    )

    fig.update_layout(
        title="Adaptive pre-resampling control trajectory",
        xaxis_title="Control time (ms)",
        yaxis_title="Frequency offset (MHz)",
        hovermode="x unified",
        height=420,
    )

    return fig


def create_weighting_figure(
    waveform_data: Mapping[
        str,
        np.ndarray,
    ],
):
    fig = go.Figure()

    pairs = (
        (
            "f_peak_mhz",
            "peak_weights",
            "Peak weights",
        ),
        (
            "f_shoulder_mhz",
            "shoulder_weights",
            "Shoulder weights",
        ),
    )

    for x_key, y_key, label in pairs:
        x = waveform_data.get(
            x_key
        )

        y = waveform_data.get(
            y_key
        )

        if (
            x is None
            or y is None
        ):
            continue

        x = np.asarray(x)
        y = np.asarray(y)

        if (
            x.size == 0
            or x.size != y.size
        ):
            continue

        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines+markers",
                name=label,
            )
        )

    if not fig.data:
        return None

    fig.update_layout(
        title="Adaptive peak / shoulder weighting",
        xaxis_title="Frequency (MHz)",
        yaxis_title="Relative weight",
        hovermode="x unified",
        height=420,
    )

    return fig


def create_arb_figure(
    waveform_data: Mapping[
        str,
        np.ndarray,
    ],
    *,
    period_s: float | None = None,
):
    normalized = waveform_data.get(
        "normalized_samples"
    )

    physical = waveform_data.get(
        "physical_voltage_v"
    )

    if (
        normalized is None
        and physical is None
    ):
        return None

    lengths = [
        np.asarray(values).size
        for values in (
            normalized,
            physical,
        )
        if values is not None
    ]

    if not lengths:
        return None

    points = max(
        lengths
    )

    if period_s is not None:
        period_s = float(
            period_s
        )

        if period_s <= 0:
            period_s = None

    if period_s is None:
        x = np.arange(
            points
        )

        x_title = "ARB sample"
    else:
        x = np.linspace(
            0.0,
            period_s * 1e3,
            points,
            endpoint=False,
        )

        x_title = "ARB time (ms)"

    fig = make_subplots(
        specs=[
            [
                {
                    "secondary_y": True
                }
            ]
        ]
    )

    if normalized is not None:
        normalized = np.asarray(
            normalized
        )

        if normalized.size == points:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=normalized,
                    mode="lines",
                    name="Normalized ARB",
                ),
                secondary_y=False,
            )

    if physical is not None:
        physical = np.asarray(
            physical
        )

        if physical.size == points:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=physical,
                    mode="lines",
                    name="Physical voltage",
                ),
                secondary_y=True,
            )

    if not fig.data:
        return None

    fig.update_xaxes(
        title_text=x_title
    )

    fig.update_yaxes(
        title_text="Normalized amplitude",
        secondary_y=False,
    )

    fig.update_yaxes(
        title_text="Voltage (V)",
        secondary_y=True,
    )

    fig.update_layout(
        title="Final uniformly resampled Keysight ARB",
        hovermode="x unified",
        height=480,
    )

    return fig


def create_event_timeline_figure(
    events: Iterable[
        Mapping[str, Any]
    ],
):
    frame = event_dataframe(
        events
    )

    if frame.empty:
        return None

    frame = frame.dropna(
        subset=[
            "timestamp_utc",
            "event_type",
        ]
    ).copy()

    if frame.empty:
        return None

    frame[
        "sequence"
    ] = np.arange(
        1,
        len(frame) + 1,
    )

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=frame[
                "timestamp_utc"
            ],
            y=frame[
                "sequence"
            ],
            mode="lines+markers",
            text=frame[
                "event_type"
            ],
            customdata=frame[
                [
                    "level",
                    "source",
                ]
            ].to_numpy(),
            hovertemplate=(
                "%{text}<br>"
                "Time: %{x}<br>"
                "Level: %{customdata[0]}<br>"
                "Source: %{customdata[1]}"
                "<extra></extra>"
            ),
            name="Session events",
        )
    )

    fig.update_layout(
        title="Session event timeline",
        xaxis_title="UTC time",
        yaxis_title="Event sequence",
        height=420,
        showlegend=False,
    )

    return fig
