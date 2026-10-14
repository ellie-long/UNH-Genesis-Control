"""ssRF burn history from the simplified ``blast_log_YYYY-MM-DD.csv`` files.

Tab-separated, one row per burn (produced by blastlogSimplify from the raw
instrument sequence logs)::

    Start time  End time  Central Freq (MHz)  FM Deviation (kHz)  Step Size (dB)
    Sweep Min (dBm)  Sweep Max (dBm)  Dwell Time (ms)  Status

``Status`` is ``Success`` or ``Failed``; a failed burn has empty parameters.
Times are lab-local wall time.
"""

from __future__ import annotations

import csv
import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

from .aliases import parse_number
from .records import lab_tz


@dataclass(frozen=True)
class Burn:
    start: float            # epoch s
    end: float              # epoch s
    centre_mhz: float
    fm_dev_khz: float
    step_db: float
    sweep_min_dbm: float
    sweep_max_dbm: float
    dwell_ms: float
    status: str
    source: str = ""

    @property
    def ok(self) -> bool:
        return self.status.lower() == "success"

    @property
    def duration_s(self) -> float:
        return self.end - self.start


def _epoch(text: str, tz) -> float:
    return _dt.datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz).timestamp()


def read_blast_log(path: Path) -> list[Burn]:
    tz = lab_tz()
    burns = []
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            try:
                start, end = _epoch(row["Start time"], tz), _epoch(row["End time"], tz)
            except (KeyError, ValueError):
                continue
            burns.append(Burn(
                start=start, end=end,
                centre_mhz=parse_number(row.get("Central Freq (MHz)", "")),
                fm_dev_khz=parse_number(row.get("FM Deviation (kHz)", "")),
                step_db=parse_number(row.get("Step Size (dB)", "")),
                sweep_min_dbm=parse_number(row.get("Sweep Min (dBm)", "")),
                sweep_max_dbm=parse_number(row.get("Sweep Max (dBm)", "")),
                dwell_ms=parse_number(row.get("Dwell Time (ms)", "")),
                status=row.get("Status", ""),
                source=str(path),
            ))
    return burns


def read_cooldown_burns(cooldown_dir: Path) -> list[Burn]:
    burns: list[Burn] = []
    for path in sorted(Path(cooldown_dir).glob("blast_log_*.csv")):
        burns.extend(read_blast_log(path))
    burns.sort(key=lambda b: b.start)
    return burns
