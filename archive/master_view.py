"""Read-only adapters for master wide-CSV sessions in Streamlit.

The quality column is authoritative; unavailable or stale samples must not
be plotted as measurements. Only requested columns are loaded into memory.
"""
import json
from pathlib import Path
import pandas as pd
from archive.data_paths import master_root


def list_master_sessions(base=None):
    base = Path(base) if base is not None else master_root()
    results = []
    for meta in sorted(base.glob("*/session.json"), reverse=True):
        try:
            detail = json.loads(meta.read_text(encoding="utf-8"))
            if isinstance(detail, dict):
                results.append((meta.parent, detail))
        except (ValueError, OSError):
            continue
    return results


def master_pvs(session_dir):
    for metadata in sorted(Path(session_dir).glob("*/metadata.json")):
        try:
            detail = json.loads(metadata.read_text(encoding="utf-8"))
            return sorted(detail.get("pv_registry", {}))
        except (ValueError, OSError):
            continue
    return []


def load_master_pv_data(session_dir, pv, start_date, end_date):
    """Return Timestamp/Value/Quality for one PV, omitting invalid values."""
    session_dir = Path(session_dir)
    value_col = pv + "|value"
    quality_col = pv + "|quality"
    frames = []
    for csv_file in sorted(session_dir.glob("*/genesis_master.csv")):
        # Scope to UTC dates near selection; exact local-time filter below.
        try:
            for chunk in pd.read_csv(
                csv_file,
                usecols=["timestamp_utc", value_col, quality_col],
                chunksize=10000,
                low_memory=False,
            ):
                times = pd.to_datetime(chunk["timestamp_utc"], utc=True, errors="coerce")
                local_times = times.dt.tz_convert("America/New_York").dt.tz_localize(None)
                values = pd.to_numeric(chunk[value_col], errors="coerce")
                valid = chunk[quality_col] == "valid"
                selected = (local_times >= start_date) & (local_times <= end_date)
                if selected.any():
                    frames.append(pd.DataFrame({
                        "Timestamp": local_times[selected],
                        "Value": values[selected].where(valid[selected]),
                        "Quality": chunk[quality_col][selected],
                    }))
        except (ValueError, OSError, KeyError):
            # A malformed file or schema is not silently interpreted as valid.
            raise
    if not frames:
        return pd.DataFrame(columns=["Timestamp", "Value", "Quality"])
    return pd.concat(frames, ignore_index=True).sort_values("Timestamp")
