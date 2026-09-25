"""Pre-flatten a previous cool-down's raw ``data_record_*.csv`` into a compact replay
file whose lines ARE the wire messages (one NDJSON 'sweep' object per line).

This keeps the LabVIEW ``NMRReplay.vi`` trivial: it reads a line and TCP-writes it to
the fit server -- no 745-column parsing or baseline detection in LabVIEW. The heavy
work (column resolution, per-row baseline subtraction) is done once here in Python.

    python -m online_fitter.make_replay_file --limit 5000 -o replay_2026-06-17.ndjson
"""
import argparse
import os

from . import protocol
from . import replay_source

_DEFAULT_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "test_data", "2026-06-Cooldown", "data_record_2026-06-17.csv")


def build(path, out, limit=0, start=0, stride=1, statuses=None):
    n = 0
    with open(out, "wb") as fh:
        for sw in replay_source.iter_sweeps(path, statuses=statuses, start=start, stride=stride):
            fh.write(protocol.encode_sweep(sw["centfreq_mhz"], sw["span_mhz"],
                                           sw["raw"], sw["baseline_sub"], ts=sw["ts"]))
            n += 1
            if limit and n >= limit:
                break
    print(f"wrote {n} sweep messages -> {out}")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=_DEFAULT_DATA, help="data_record_*.csv to convert")
    ap.add_argument("-o", "--out", default="replay.ndjson", help="output replay file")
    ap.add_argument("--limit", type=int, default=0, help="max sweeps (0 = all)")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--polarization-only", action="store_true")
    a = ap.parse_args(argv)
    statuses = {"Polarization"} if a.polarization_only else None
    build(a.path, a.out, a.limit, a.start, a.stride, statuses)


if __name__ == "__main__":
    main()
