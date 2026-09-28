#!/usr/bin/env python3
"""
Genesis ssRF Session Viewer.

This is a Streamlit multipage view used alongside archive/archive_viewer.py.
It is read-only and performs no EPICS or hardware access.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import streamlit as st


REPO_ROOT = Path(
    __file__
).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(REPO_ROOT),
    )

from archive.ssrf_session_view import (
    SessionViewError,
    create_arb_figure,
    create_control_trajectory_figure,
    create_event_timeline_figure,
    create_nmr_fit_figure,
    create_weighting_figure,
    event_dataframe,
    load_event_records,
    load_session_bundle,
    session_summaries,
)


st.set_page_config(
    page_title="Genesis ssRF Sessions",
    page_icon="📡",
    layout="wide",
)


def _period_s(record):
    if record.waveform is None:
        return None

    data = record.waveform.get(
        "data",
        {},
    )

    if not isinstance(
        data,
        dict,
    ):
        return None

    return data.get(
        "period_s"
    )


def main():
    st.title(
        "📡 Genesis ssRF Session Viewer"
    )

    st.caption(
        "Read-only view of correlated ssRF fitter, "
        "adaptive-waveform, and event data."
    )

    default_event_dir = (
        REPO_ROOT
        / "archive"
        / "events"
        / "ssrf"
    )

    with st.sidebar:
        st.header(
            "ssRF Session Data"
        )

        event_dir = st.text_input(
            "Event directory",
            value=str(
                default_event_dir
            ),
        )

        if st.button(
            "Refresh sessions",
            width="stretch",
        ):
            st.rerun()

    try:
        records = load_event_records(
            event_dir
        )
    except Exception as exc:
        st.error(
            f"Unable to load event records: {exc}"
        )
        return

    summaries = session_summaries(
        records
    )

    if not summaries:
        st.info(
            "No ssRF session events were found. "
            "Run the fitter runtime with event logging enabled."
        )
        return

    by_id = {
        item["session_id"]: item
        for item in summaries
    }

    session_ids = [
        item["session_id"]
        for item in summaries
    ]

    def format_session(
        session_id,
    ):
        item = by_id[
            session_id
        ]

        pieces = [
            str(
                item.get(
                    "started_utc"
                )
                or "unknown time"
            ),
            str(
                item.get(
                    "status"
                )
                or "unknown"
            ),
        ]

        if item.get(
            "material"
        ):
            pieces.append(
                str(
                    item[
                        "material"
                    ]
                )
            )

        if item.get(
            "case"
        ):
            pieces.append(
                str(
                    item[
                        "case"
                    ]
                )
            )

        pieces.append(
            session_id
        )

        return " | ".join(
            pieces
        )

    selected = st.selectbox(
        "Session",
        options=session_ids,
        format_func=format_session,
    )

    try:
        bundle = load_session_bundle(
            event_dir=event_dir,
            session_id=selected,
            repo_root=REPO_ROOT,
        )
    except SessionViewError as exc:
        st.error(
            str(exc)
        )
        return
    except Exception as exc:
        st.error(
            f"Unable to load session: {exc}"
        )
        return

    record = bundle[
        "record"
    ]

    summary = by_id[
        selected
    ]

    fit_data = bundle[
        "fit_data"
    ]

    waveform_data = bundle[
        "waveform_data"
    ]

    top = st.columns(
        6
    )

    top[0].metric(
        "Status",
        record.status,
    )

    top[1].metric(
        "Material",
        summary.get(
            "material"
        )
        or "—",
    )

    top[2].metric(
        "Case",
        summary.get(
            "case"
        )
        or "—",
    )

    top[3].metric(
        "Events",
        record.event_count,
    )

    top[4].metric(
        "Fit data",
        (
            "available"
            if fit_data
            else "none"
        ),
    )

    scaling = (
        summary.get(
            "scaling_verified_for_hardware"
        )
    )

    if scaling is True:
        scaling_text = "VERIFIED"
    elif scaling is False:
        scaling_text = "NOT VERIFIED"
    else:
        scaling_text = "—"

    top[5].metric(
        "Physical scaling",
        scaling_text,
    )

    st.caption(
        f"Session ID: `{record.session_id}`"
    )

    tabs = st.tabs(
        [
            "NMR + Fit",
            "Adaptive Waveform",
            "Event Timeline",
            "Session Record",
        ]
    )

    with tabs[0]:
        if not fit_data:
            st.info(
                "This session has no fit_data.npz product."
            )
        else:
            fig = create_nmr_fit_figure(
                fit_data
            )

            if fig is not None:
                st.plotly_chart(
                    fig,
                    width="stretch",
                )
            else:
                st.info(
                    "No frequency-domain NMR/fit arrays "
                    "are available for plotting."
                )

            raw = fit_data.get(
                "raw"
            )

            if raw is not None:
                st.caption(
                    f"Raw NMR vector: {len(raw)} samples"
                )

            fit_path = bundle.get(
                "fit_data_path"
            )

            if fit_path is not None:
                st.code(
                    str(fit_path),
                    language=None,
                )

    with tabs[1]:
        if not waveform_data:
            st.info(
                "This session has no waveform_data.npz product."
            )
        else:
            trajectory = (
                create_control_trajectory_figure(
                    waveform_data
                )
            )

            if trajectory is not None:
                st.plotly_chart(
                    trajectory,
                    width="stretch",
                )

            weighting = (
                create_weighting_figure(
                    waveform_data
                )
            )

            if weighting is not None:
                st.plotly_chart(
                    weighting,
                    width="stretch",
                )

            arb = create_arb_figure(
                waveform_data,
                period_s=_period_s(
                    record
                ),
            )

            if arb is not None:
                st.plotly_chart(
                    arb,
                    width="stretch",
                )

            control_points = len(
                waveform_data.get(
                    "control_time_s",
                    [],
                )
            )

            arb_points = len(
                waveform_data.get(
                    "normalized_samples",
                    [],
                )
            )

            cols = st.columns(
                2
            )

            cols[0].metric(
                "Control trajectory",
                f"{control_points} points",
            )

            cols[1].metric(
                "Final ARB",
                f"{arb_points} points",
            )

            waveform_path = bundle.get(
                "waveform_data_path"
            )

            if waveform_path is not None:
                st.code(
                    str(
                        waveform_path
                    ),
                    language=None,
                )

    with tabs[2]:
        timeline = (
            create_event_timeline_figure(
                bundle["events"]
            )
        )

        if timeline is not None:
            st.plotly_chart(
                timeline,
                width="stretch",
            )

        frame = event_dataframe(
            bundle["events"]
        )

        st.dataframe(
            frame,
            width="stretch",
            hide_index=True,
        )

    with tabs[3]:
        st.subheader(
            "Derived correlated session record"
        )

        st.json(
            record.to_dict(),
            expanded=True,
        )

        st.caption(
            "The JSONL event stream remains the "
            "authoritative detailed history. "
            "This record is a derived summary."
        )

        with st.expander(
            "Raw event records"
        ):
            st.code(
                json.dumps(
                    bundle[
                        "events"
                    ],
                    indent=2,
                    sort_keys=True,
                ),
                language="json",
            )


if __name__ == "__main__":
    main()
