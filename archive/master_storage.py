"""Exclusive writer ownership and append-integrity validation.

Linux/WSL file locking. No EPICS or instrument interaction.
"""

import fcntl
import json
from pathlib import Path


class WriterLease:
    """Hold an exclusive OS lock for the recorder's full lifetime."""

    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        self.path = directory / ".genesis_master.lock"
        self.handle = self.path.open("a+")

        try:
            fcntl.flock(
                self.handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            raise RuntimeError(
                "Another master recorder owns this archive directory"
            ) from None
        except BaseException:
            self.handle.close()
            self.handle = None
            raise

    def close(self):
        if self.handle is not None:
            try:
                fcntl.flock(
                    self.handle.fileno(), fcntl.LOCK_UN
                )
            finally:
                self.handle.close()
                self.handle = None


def verify_metadata(path, expected):
    """Refuse incompatible restarts; never rewrite existing metadata."""
    path = Path(path)

    if path.exists():
        try:
            existing = json.loads(
                path.read_text(encoding="utf-8")
            )
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError(
                f"Invalid existing master metadata: {path}"
            ) from exc

        if existing != expected:
            raise RuntimeError(
                "Master archive schema/registry mismatch; "
                "a new dataset segment is required"
            )
        return

    # Exclusive creation; a partial file fails validation on restart.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(expected, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()


def verify_appendable(path):
    """Refuse appending to a file with an incomplete final line."""
    path = Path(path)

    if not path.exists() or path.stat().st_size == 0:
        return

    with path.open("rb") as handle:
        handle.seek(-1, 2)

        if handle.read(1) != b"\n":
            raise RuntimeError(
                f"Master archive has an incomplete final line: {path}"
            )
