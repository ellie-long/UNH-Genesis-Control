"""Lab-local time strings <-> epoch seconds (no EPICS imports)."""

from __future__ import annotations

import datetime as _dt

from .data.records import lab_tz

FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def parse_lab_time(text: str) -> float:
    """'YYYY-MM-DD HH:MM[:SS]' (lab-local; 'T' separator accepted) -> epoch s."""
    text = str(text).strip().replace("T", " ")
    for fmt in FORMATS:
        try:
            return _dt.datetime.strptime(text, fmt).replace(tzinfo=lab_tz()).timestamp()
        except ValueError:
            continue
    raise ValueError(f"time {text!r}: use YYYY-MM-DD HH:MM[:SS]")
