"""Pure-Python stand-in for LabVIEW: stream a previous cool-down's sweeps to the
running fit server over the loopback socket, emulating the VME Loop. No LabVIEW or
hardware needed -- the fast dev/test loop for the GUI and engine.

    python -m online_fitter.mock_labview_client --rate 20 --limit 2000

By default it streams the copied test_data/2026-06-Cooldown/data_record_2026-06-17.csv.
"""
import argparse
import os
import socket
import time

from . import protocol
from . import replay_source

_DEFAULT_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "test_data", "2026-06-Cooldown", "data_record_2026-06-17.csv")


def stream(path, host, port, rate, limit, start, stride, statuses):
    delay = 1.0 / rate if rate and rate > 0 else 0.0
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.settimeout(60)         # generous read timeout: Update Constants no longer blocks the server,
        rf = sock.makefile("rb")    # but tolerate an occasional slow reply rather than crashing
        sent = 0
        for sw in replay_source.iter_sweeps(path, statuses=statuses, start=start, stride=stride):
            msg = protocol.encode_sweep(sw["centfreq_mhz"], sw["span_mhz"],
                                        sw["raw"], sw["baseline_sub"], ts=sw["ts"])
            sock.sendall(msg)
            line = rf.readline()
            if not line:
                print("server closed the connection -- stopping."); break
            reply = protocol.decode_message(line)
            sent += 1
            if sent <= 3 or sent % 200 == 0:
                op = sw["online_p"]
                print(f"[{sent}] {sw['ts']}  status={sw['status']:12s} "
                      f"file_onlineP={op if op is None else round(op,1)}  ->  "
                      f"fit={reply.get('fit_type')} P_fit={reply.get('P_fit')} "
                      f"P_area={reply.get('P_area')} Q_eq={reply.get('Q_eq')}"
                      + (f"  WARN:{reply['warning']}" if reply.get("warning") else ""))
            if limit and sent >= limit:
                break
            if delay:
                time.sleep(delay)
        print(f"done: sent {sent} sweeps.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=_DEFAULT_DATA, help="data_record_*.csv to replay")
    ap.add_argument("--host", default=protocol.DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    ap.add_argument("--rate", type=float, default=20.0, help="sweeps/second (0 = as fast as possible)")
    ap.add_argument("--limit", type=int, default=0, help="max sweeps (0 = all)")
    ap.add_argument("--start", type=int, default=0, help="start row index")
    ap.add_argument("--stride", type=int, default=1, help="send every Nth row")
    ap.add_argument("--polarization-only", action="store_true",
                    help="only stream rows with NMR Status == Polarization")
    a = ap.parse_args(argv)
    statuses = {"Polarization"} if a.polarization_only else None
    stream(a.path, a.host, a.port, a.rate, a.limit, a.start, a.stride, statuses)


if __name__ == "__main__":
    main()
