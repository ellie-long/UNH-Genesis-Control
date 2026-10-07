#!/usr/bin/env python3
"""
EPICS Archive Viewer - Streamlit Web Application

Run with:
    streamlit run archive_viewer.py
"""

import re
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from pathlib import Path

try:
    from viewer_filters import (
        SUBSYSTEM_OPTIONS,
        MICROWAVE_GROUP_OPTIONS,
        MICROWAVE_PRESETS,
        CRYO_GROUP_OPTIONS,
        CRYO_PRESETS,
        filter_pvs,
        resolve_microwave_preset,
        resolve_cryo_preset,
    )
except ImportError:
    # Supports module-style imports used by Genesis tests.
    from archive.viewer_filters import (
        SUBSYSTEM_OPTIONS,
        MICROWAVE_GROUP_OPTIONS,
        MICROWAVE_PRESETS,
        CRYO_GROUP_OPTIONS,
        CRYO_PRESETS,
        filter_pvs,
        resolve_microwave_preset,
        resolve_cryo_preset,
    )

# Page configuration
st.set_page_config(
    page_title="EPICS Archive Viewer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main {
        padding-top: 1rem;
    }
    .stPlotlyChart {
        background-color: white;
        border-radius: 5px;
        padding: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
</style>
""", unsafe_allow_html=True)


# Cache functions for better performance
@st.cache_data(ttl=60)  # Cache for 1 minute
def get_archived_pvs(archive_path):
    """Get list of all archived PVs"""
    archive_dir = Path(archive_path)
    if not archive_dir.exists():
        print("Error, directory does not exist.", archive_path)
        return []

    pv_names = set()
    for file in archive_dir.glob("*.csv"):
        # Extract PV name from filename
        # Format is: {safe_pv_name}_{YYYY-MM-DD}.csv
        parts = file.stem.rsplit('_', 1)  # Split on last underscore only
        if len(parts) == 2:
            safe_pv_name, date_part = parts
            # Check if date_part looks like a date (YYYY-MM-DD)
            if re.match(r'\d{4}-\d{2}-\d{2}', date_part):
                # Convert back to PV name format
                pv_name = safe_pv_name.replace('_', ':', 1)
                pv_names.add(pv_name)
    return sorted(pv_names)


@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_pv_data(archive_path, pv_name, start_date, end_date):
    """Load data for a PV within date range"""
    archive_dir = Path(archive_path)
    safe_pv_name = pv_name.replace(':', '_').replace('/', '_')

    pattern = f"{safe_pv_name}_*.csv"
    files = sorted(archive_dir.glob(pattern))
    all_data = []

    for file in files:
        try:
            # Extract date from filename: {safe_pv_name}_{YYYY-MM-DD}.csv
            parts = file.stem.rsplit('_', 1)
            if len(parts) != 2:
                continue

            safe_pv_name_part, file_date_str = parts

            # Verify this is the right PV
            if safe_pv_name_part != safe_pv_name:
                continue

            file_date = datetime.strptime(file_date_str, '%Y-%m-%d')

            # Skip files outside date range
            if file_date.date() < start_date.date() or file_date.date() > end_date.date():
                continue

            # Read CSV
            df = pd.read_csv(file)
            df['Timestamp'] = pd.to_datetime(df['Timestamp'])

            # Filter by exact timestamp
            mask = (df['Timestamp'] >= start_date) & (df['Timestamp'] <= end_date)
            filtered_df = df.loc[mask]

            if not filtered_df.empty:
                all_data.append(filtered_df)

        except Exception as e:
            st.error(f"Error reading {file.name}: {e}")

    if all_data:
        combined_df = pd.concat(all_data, ignore_index=True)
        combined_df = combined_df.sort_values('Timestamp')

        # Try to convert values to numeric
        combined_df['Value'] = pd.to_numeric(combined_df['Value'], errors='coerce')

        return combined_df

    return pd.DataFrame()

def compact_axis_label(pv_name):
    """Short display-only PV label for plot axes."""
    label = str(pv_name)

    if label.startswith("UNH:GEN_"):
        label = label[len("UNH:GEN_"):]

    return label.replace("_", ":")


def create_time_series_plot(data_dict, title=""):
    """Create time series plot for multiple PVs"""
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    colors = px.colors.qualitative.Plotly

    for idx, (pv_name, df) in enumerate(data_dict.items()):
        if df.empty or df['Value'].isna().all():
            continue

        color = colors[idx % len(colors)]

        # Add trace
        fig.add_trace(
            go.Scatter(
                x=df['Timestamp'],
                y=df['Value'],
                mode='lines',
                name=pv_name,
                line=dict(color=color, width=2),
                hovertemplate='<b>%{fullData.name}</b><br>' +
                              'Time: %{x}<br>' +
                              'Value: %{y:.6g}<br>' +
                              '<extra></extra>'
            ),
            secondary_y=idx > 0 and len(data_dict) == 2  # Use secondary y-axis for 2nd PV only
        )

    # Common plot layout. Keep enough margin for both y-axis titles.
    fig.update_xaxes(
        title_text="Time",
        showgrid=True,
        gridcolor='lightgray',
        automargin=True,
    )

    fig.update_yaxes(
        title_text="Value",
        showgrid=True,
        gridcolor='lightgray',
        automargin=True,
    )

    if len(data_dict) == 2:
        pv_names = list(data_dict.keys())

        fig.update_yaxes(
            title_text=compact_axis_label(pv_names[0]),
            secondary_y=False,
            automargin=True,
            title_standoff=14,
        )

        fig.update_yaxes(
            title_text=compact_axis_label(pv_names[1]),
            secondary_y=True,
            automargin=True,
            title_standoff=14,
        )

    fig.update_layout(
        title=title,
        hovermode='x unified',
        height=500,

        # Explicit outer margins prevent the secondary-axis title from
        # being clipped by the Streamlit plot container.
        margin=dict(
            l=115,
            r=175,
            t=80,
            b=75,
        ),

        showlegend=True,
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=0.01,
            bgcolor="rgba(255, 255, 255, 0.8)"
        )
    )

    return fig


def calculate_statistics(df):
    """Calculate statistics for numeric data"""
    if df.empty or df['Value'].isna().all():
        return None

    numeric_values = df['Value'].dropna()

    if len(numeric_values) == 0:
        return None

    stats = {
        'Count': len(numeric_values),
        'Mean': numeric_values.mean(),
        'Std Dev': numeric_values.std(),
        'Min': numeric_values.min(),
        'Max': numeric_values.max(),
        'First': df.iloc[0]['Value'],
        'Last': df.iloc[-1]['Value'],
        'First Time': df.iloc[0]['Timestamp'],
        'Last Time': df.iloc[-1]['Timestamp']
    }

    return stats


def main():
    st.title("📊 EPICS Archive Viewer")
    st.markdown("Interactive viewer for archived EPICS data")

    # Sidebar configuration
    with st.sidebar:
        st.header("Configuration")

        # Archive path
        archive_path = st.text_input(
            "Archive Path",
            value="data",
            help="Path to the archive directory"
        )

        # Get available PVs
        available_pvs = get_archived_pvs(archive_path)

        if not available_pvs:
            st.error(f"No archived PVs found in '{archive_path}'")
            return

        # Optional organization layer over the existing generic
        # archived-PV viewer. This does not alter CSV loading or plotting.
        st.subheader("Archive Scope")

        subsystem = st.selectbox(
            "Subsystem",
            options=SUBSYSTEM_OPTIONS,
            index=0,
            help=(
                "Filter the existing Genesis EPICS archive by subsystem. "
                "This does not change archived data."
            ),
        )

        microwave_group = "All Microwave"
        cryo_group = "All Cryo"

        if subsystem == "Microwave":
            microwave_group = st.selectbox(
                "Microwave device group",
                options=MICROWAVE_GROUP_OPTIONS,
                index=0,
            )

        if subsystem == "Cryo":
            cryo_group = st.selectbox(
                "Cryo device group",
                options=CRYO_GROUP_OPTIONS,
                index=0,
            )

        filtered_pvs = filter_pvs(
            available_pvs,
            subsystem=subsystem,
            microwave_group=microwave_group,
            cryo_group=cryo_group,
        )

        if not filtered_pvs:
            st.warning(
                "No archived PVs match the selected scope."
            )
            return

        st.caption(
            f"Showing {len(filtered_pvs)} of "
            f"{len(available_pvs)} archived PVs"
        )

        preset_name = "Manual selection"

        if subsystem == "Microwave":
            preset_name = st.selectbox(
                "Quick view",
                options=list(MICROWAVE_PRESETS),
                index=0,
                help=(
                    "Optional Phase-I Microwave plot presets. "
                    "Manual PV selection remains available."
                ),
            )

        elif subsystem == "Cryo":
            preset_name = st.selectbox(
                "Quick view",
                options=list(CRYO_PRESETS),
                index=0,
                help=(
                    "Cryo cooldown / target quick-view presets. "
                    "Manual PV selection remains available."
                ),
            )

        if subsystem == "Microwave":
            default_pvs = resolve_microwave_preset(
                filtered_pvs,
                preset_name,
            )

        elif subsystem == "Cryo":
            default_pvs = resolve_cryo_preset(
                filtered_pvs,
                preset_name,
            )

        else:
            default_pvs = []

        if not default_pvs:
            default_pvs = [
                filtered_pvs[0]
            ]

        st.subheader(
            f"Available PVs ({len(filtered_pvs)})"
        )

        # Existing manual PV selection remains the primary viewer interface.
        selected_pvs = st.multiselect(
            "Select PVs to plot",
            options=filtered_pvs,
            default=default_pvs,
            help="Select up to 4 PVs to plot together",
            key=(
                "pv_selection_"
                f"{subsystem}_"
                f"{microwave_group}_"
                f"{cryo_group}_"
                f"{preset_name}"
            ),
        )

        if len(selected_pvs) > 4:
            st.warning("Maximum 4 PVs can be plotted together")
            selected_pvs = selected_pvs[:4]

        st.subheader("Time Range")

        # Time range selection
        time_option = st.radio(
            "Select time range",
            ["Last N hours/days", "Custom date range"]
        )

        if time_option == "Last N hours/days":
            col1, col2 = st.columns(2)
            with col1:
                time_value = st.number_input("Value", min_value=1, value=24)
            with col2:
                time_unit = st.selectbox("Unit", ["hours", "days"])

            end_date = datetime.now()
            if time_unit == "hours":
                start_date = end_date - timedelta(hours=time_value)
            else:
                start_date = end_date - timedelta(days=time_value)
        else:
            col1, col2 = st.columns(2)
            with col1:
                start_date = st.date_input("Start date", datetime.now() - timedelta(days=1))
                start_time = st.time_input("Start time", datetime.min.time())
            with col2:
                end_date = st.date_input("End date", datetime.now())
                end_time = st.time_input("End time", datetime.now().time())

            start_date = datetime.combine(start_date, start_time)
            end_date = datetime.combine(end_date, end_time)

        # Display options
        st.subheader("Display Options")
        show_stats = st.checkbox("Show statistics", value=True)
        show_raw_data = st.checkbox("Show raw data", value=False)

        # Load data button
        load_data = st.button("Load Data", type="primary", width="stretch")

    # Main content area
    if selected_pvs and load_data:
        with st.spinner("Loading data..."):
            # Load data for all selected PVs
            data_dict = {}
            for pv in selected_pvs:
                df = load_pv_data(archive_path, pv, start_date, end_date)
                if not df.empty:
                    data_dict[pv] = df

            if not data_dict:
                st.error("No data found for selected PVs in the specified time range")
                return

            # Create tabs
            if len(selected_pvs) == 1:
                # Single PV view
                tab1, tab2, tab3 = st.tabs(["📈 Plot", "📊 Statistics", "🔢 Raw Data"])
            else:
                # Multiple PV view
                tab1, tab2, tab3, tab4 = st.tabs(
                    ["📈 Combined Plot", "📊 Statistics", "🔢 Raw Data", "📈 Individual Plots"])

            # Plot tab
            with tab1:
                st.subheader("Time Series Plot")
                fig = create_time_series_plot(data_dict, title=f"{', '.join(selected_pvs)}")
                st.plotly_chart(fig, width="stretch")

                # Download buttons
                col1, col2 = st.columns(2)
                with col1:
                    # Combine all data for CSV export
                    all_data = []
                    for pv, df in data_dict.items():
                        df_export = df.copy()
                        df_export['PV'] = pv
                        all_data.append(df_export)

                    if all_data:
                        combined_df = pd.concat(all_data, ignore_index=True)
                        csv = combined_df.to_csv(index=False)
                        st.download_button(
                            label="Download data as CSV",
                            data=csv,
                            file_name=f"archive_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                            mime="text/csv"
                        )

            # Statistics tab
            if show_stats:
                with tab2:
                    st.subheader("Statistics")

                    for pv, df in data_dict.items():
                        stats = calculate_statistics(df)
                        if stats:
                            st.write(f"**{pv}**")

                            col1, col2, col3, col4 = st.columns(4)
                            with col1:
                                st.metric("Count", f"{stats['Count']:,}")
                                st.metric("Mean", f"{stats['Mean']:.6g}")
                            with col2:
                                st.metric("Min", f"{stats['Min']:.6g}")
                                st.metric("Max", f"{stats['Max']:.6g}")
                            with col3:
                                st.metric("Std Dev", f"{stats['Std Dev']:.6g}")
                                st.metric("Range", f"{stats['Max'] - stats['Min']:.6g}")
                            with col4:
                                st.metric("First", f"{stats['First']:.6g}")
                                st.metric("Last", f"{stats['Last']:.6g}")

                            st.caption(
                                f"Time range: {stats['First Time'].strftime('%Y-%m-%d %H:%M:%S')} to {stats['Last Time'].strftime('%Y-%m-%d %H:%M:%S')}")
                            st.divider()

            # Raw data tab
            if show_raw_data:
                with tab3:
                    st.subheader("Raw Data")

                    for pv, df in data_dict.items():
                        with st.expander(f"{pv} ({len(df)} points)", expanded=len(selected_pvs) == 1):
                            # Show sample of data
                            if len(df) > 1000:
                                st.info(f"Showing first and last 500 rows of {len(df)} total rows")
                                display_df = pd.concat([df.head(500), df.tail(500)])
                            else:
                                display_df = df

                            st.dataframe(
                                display_df,
                                width="stretch",
                                hide_index=True
                            )

            # Individual plots tab (for multiple PVs)
            if len(selected_pvs) > 1:
                with tab4:
                    st.subheader("Individual Plots")

                    # Create a plot for each PV
                    for pv, df in data_dict.items():
                        if not df.empty and not df['Value'].isna().all():
                            fig = create_time_series_plot({pv: df}, title=pv)
                            st.plotly_chart(fig, width="stretch")

    # Instructions when no data is loaded
    elif not selected_pvs:
        st.info("👈 Select one or more PVs from the sidebar to begin")

    # Footer
    st.markdown("---")
    st.caption("EPICS Archive Viewer - Real-time plotting of archived EPICS data")


if __name__ == "__main__":
    main()