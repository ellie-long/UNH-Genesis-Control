"""Local PID-bound PyNMR commissioning authorization.

This is not cryptographic production authentication.
"""

import os
from pathlib import Path

PID_ENV = "GENESIS_PYNMR_PRODUCER_PID"
START_ENV = "GENESIS_PYNMR_PRODUCER_START_TICKS"

EXPECTED_CWD = Path("/home/utsav/jlab_pynmr")
EXPECTED_EXE = Path(
    "/home/utsav/miniforge3/envs/pynmr/bin/python"
)


def process_start_ticks(pid):
    data = Path(f"/proc/{int(pid)}/stat").read_text()
    end = data.rfind(")")
    if end < 0:
        raise ValueError("Malformed process stat")
    return int(data[end + 1:].split()[19])


class PinnedPyNMRProducer:
    def __init__(self, pid, start_ticks):
        self.pid = int(pid)
        self.start_ticks = int(start_ticks)
        if self.pid <= 1 or self.start_ticks <= 0:
            raise ValueError("Invalid producer identity")

    @classmethod
    def from_environment(cls):
        pid = os.environ.get(PID_ENV)
        ticks = os.environ.get(START_ENV)

        if pid is None and ticks is None:
            return None
        if not pid or not ticks:
            raise ValueError(
                "Both PyNMR PID and start ticks required"
            )
        return cls(pid, ticks)

    def matches_process(self):
        try:
            proc = Path("/proc") / str(self.pid)

            if proc.stat().st_uid != os.getuid():
                return False
            if process_start_ticks(self.pid) != self.start_ticks:
                return False
            if (
                (proc / "cwd").resolve()
                != EXPECTED_CWD.resolve()
            ):
                return False
            if (
                (proc / "exe").resolve()
                != EXPECTED_EXE.resolve()
            ):
                return False

            argv = [
                s.decode("utf-8")
                for s in (
                    proc / "cmdline"
                ).read_bytes().split(b"\x00")
                if s
            ]

            if not any(
                x == "pynmr_main.py"
                or x.endswith("/pynmr_main.py")
                for x in argv
            ):
                return False

            if "-p" not in argv:
                return False

            index = argv.index("-p")
            return (
                index + 1 < len(argv)
                and argv[index + 1] == "Deuteron"
            )

        except (
            OSError, ValueError, IndexError, UnicodeError
        ):
            return False

    def peer_allowed(self, pid, uid, gid):
        return (
            type(pid) is int
            and type(uid) is int
            and pid == self.pid
            and uid == os.getuid()
            and self.matches_process()
        )
