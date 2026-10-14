"""Load a run directory's records into pandas frames."""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..clock import Checkpoint


def served(run: Path) -> pd.DataFrame:
    path = Path(run) / "served.csv"
    if not path.exists():
        return pd.DataFrame(columns=["wall", "sim", "instrument", "channel", "value"])
    df = pd.read_csv(path, dtype={"instrument": str, "channel": str})
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def probe(run: Path) -> pd.DataFrame:
    path = Path(run) / "probe.csv"
    df = pd.read_csv(path, dtype={"pv": str, "value": str}, keep_default_na=False)
    df["num"] = pd.to_numeric(df["value"].replace("", np.nan), errors="coerce")
    return df


def checkpoints(run: Path) -> list[Checkpoint]:
    path = Path(run) / "state" / "clock.json"
    return [Checkpoint(**c) for c in json.loads(path.read_text(encoding="utf-8"))]


def events(run: Path) -> pd.DataFrame:
    path = Path(run) / "events.csv"
    if not path.exists():
        return pd.DataFrame(columns=["wall", "sim", "sim_local", "kind", "detail"])
    return pd.read_csv(path, keep_default_na=False)


_ARCHIVE_NAME = re.compile(r"^(?P<pv>.+)_(?P<date>\d{4}-\d{2}-\d{2})$")


def archive_files(run: Path) -> dict[str, list[Path]]:
    """safe PV name -> daily CSV paths (archiver names them <PV>_<YYYY-MM-DD>.csv)."""
    out: dict[str, list[Path]] = {}
    for path in sorted((Path(run) / "archive" / "data").glob("*.csv")):
        m = _ARCHIVE_NAME.match(path.stem)
        if m:
            out.setdefault(m.group("pv"), []).append(path)
    return out


def archive_series(paths: list[Path]) -> pd.DataFrame:
    frames = []
    for p in paths:
        df = pd.read_csv(p, dtype={"Value": str}, keep_default_na=False)
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["wall", "value", "num"])
    df = pd.concat(frames, ignore_index=True)
    # archiver stamps naive local wall time (datetime.now())
    walls = [_dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f").timestamp() for s in df["Timestamp"]]
    out = pd.DataFrame({"wall": walls, "value": df["Value"]})
    out["num"] = pd.to_numeric(out["value"].replace("", np.nan), errors="coerce")
    return out


def safe_name(pv: str) -> str:
    return pv.replace(":", "_").replace("/", "_")
