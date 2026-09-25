"""example_data_reader.py -- a heavily-commented, copy-me example of reading the Online NMR
Fitter's broadcast stream from your own program.

WHAT THIS IS
------------
While the fitter is running (``python -m online_fitter.run``), it *broadcasts* every fitted
sweep on a local network port so OTHER programs can use the results. This script is a minimal
consumer of that stream. It assumes NO prior background: every step is explained.

You do not need the fitter's code to consume the stream -- only Python's standard library
(``socket`` + ``json``). numpy is used here just to turn the number lists into arrays; it is
optional and clearly marked.

THE BIG PICTURE (three ideas)
-----------------------------
1. A TCP *socket* is a two-way pipe between two programs. The fitter is the "server" and holds
   one end open on your own computer at address ``127.0.0.1`` ("localhost" -- this machine) and
   port ``8778``. Your program is the "client": it connects to that address and then just reads.

2. The fitter sends **NDJSON** = "newline-delimited JSON". That is simply: one JSON object per
   line, each line ended by a newline. So you read the pipe one line at a time, and each line is
   one fitted sweep.

3. Each line is a JSON object. In Python, ``json.loads(line)`` turns that line of text into a
   normal ``dict``. Then you read values out of the dict by name, e.g. ``msg["P_fit"]``.

WHAT EACH LINE CONTAINS (the four things the fitter streams)
-----------------------------------------------------------
    1. Good fit constants      -> msg["constants"]  (+ msg["constants_good"] is True once
                                   the operator has run "Update Constants" or imported constants)
    2. Processed NMR data      -> msg["data"]                (the cubic-subtracted line)
    3. Processed NMR fit       -> msg["total_fit"] and the four components:
                                   msg["I_plus_primary"],  msg["I_minus_primary"],
                                   msg["I_plus_secondary"], msg["I_minus_secondary"]
                                   (secondaries are all 0 for single-bond ND3)
    4. Raw NMR                 -> msg["raw"]
Every array above lines up with the frequency axis ``msg["freqs_mhz"]`` (same length, same order).
Also present each line: ``P_area, P_fit, Q_eq, Q_fit, r, phase_deg, red_chisq`` (this sweep's
polarizations), and ``material, model, fit_type, npts, ts``.

A NOTE ON "null"
----------------
JSON has no NaN. Any sample the fitter couldn't compute (e.g. component curves while the
operator is in Baseline / No-NMR mode) is sent as JSON ``null``, which becomes Python ``None``.
The ``to_array`` helper below turns ``None`` into ``float('nan')`` so you get clean numpy arrays.

RUN IT
------
    # 1. start the fitter (it broadcasts by default):
    python -m online_fitter.run
    # 2. in another terminal, run this reader:
    python -m online_fitter.example_data_reader
    #    ...or point it somewhere else:
    python -m online_fitter.example_data_reader --host 127.0.0.1 --port 8778
"""
import argparse
import json
import socket

try:
    import numpy as np                      # OPTIONAL: only used to make arrays + find the peak
except Exception:                           # noqa: BLE001 -- run fine without numpy
    np = None

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8778


def to_array(values):
    """Turn a JSON number list (which may contain ``None`` for non-finite samples) into a numpy
    float array, mapping ``None`` -> NaN. Returns a plain list if numpy isn't installed."""
    if values is None:
        return None
    cleaned = [float("nan") if v is None else float(v) for v in values]
    return np.asarray(cleaned, float) if np is not None else cleaned


def handle_message(msg):
    """Do something useful with ONE fitted sweep (one line of the stream). This is the part you
    replace with your own logic -- here we just print a readable summary and show how to reach
    each of the four streamed items."""

    # ---- 1. GOOD FIT CONSTANTS -------------------------------------------------------------
    # constants_good is False until the operator runs "Update Constants" (or imports constants).
    # Until then the calibration (CC etc.) is a preset seed, not a real calibration -- so it is
    # good practice to WAIT for constants_good == True before trusting the numbers.
    good = msg.get("constants_good", False)
    const = msg.get("constants") or {}
    cc = const.get("CC")
    cc_daq = const.get("CC_DAQ")

    # ---- 2. PROCESSED NMR DATA -------------------------------------------------------------
    freqs = to_array(msg.get("freqs_mhz"))     # x-axis (MHz), shared by every array
    data = to_array(msg.get("data"))           # the processed (cubic-subtracted) NMR line

    # ---- 3. PROCESSED NMR FIT (total + the four component curves) --------------------------
    total_fit = to_array(msg.get("total_fit"))
    ip_primary = to_array(msg.get("I_plus_primary"))
    im_primary = to_array(msg.get("I_minus_primary"))
    ip_secondary = to_array(msg.get("I_plus_secondary"))    # all zeros for ND3
    im_secondary = to_array(msg.get("I_minus_secondary"))

    # ---- 4. RAW NMR ------------------------------------------------------------------------
    raw = to_array(msg.get("raw"))

    # ---- example use: find the peak frequency of the processed data ------------------------
    peak_txt = "--"
    if np is not None and data is not None and freqs is not None and np.isfinite(data).any():
        peak_txt = f"{freqs[int(np.nanargmax(data))]:.4f} MHz"

    def num(x, nd=2):
        return "--" if x is None else f"{x:.{nd}f}"

    print(
        f"[{msg.get('ts')}] {msg.get('material')}/{msg.get('model')} {msg.get('fit_type'):<11} "
        f"P_fit={num(msg.get('P_fit'))}% Q_fit={num(msg.get('Q_fit'))}% r={num(msg.get('r'),3)} | "
        f"constants[{'GOOD' if good else 'preset'}] CC={num(cc,5)} CC_DAQ={num(cc_daq,2)} | "
        f"npts: data={_len(data)} fit={_len(total_fit)} raw={_len(raw)} "
        f"I+prim={_len(ip_primary)} I-prim={_len(im_primary)} "
        f"I+sec={_len(ip_secondary)} I-sec={_len(im_secondary)} | data peak @ {peak_txt}")


def _len(a):
    return 0 if a is None else len(a)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST, help="fitter host (default 127.0.0.1 = this PC)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="broadcast port (default 8778)")
    a = ap.parse_args(argv)

    print(f"connecting to the fitter's stream at {a.host}:{a.port} ... (Ctrl+C to stop)")
    # socket.create_connection opens the pipe to the fitter and waits until it is connected.
    with socket.create_connection((a.host, a.port), timeout=30) as sock:
        # .makefile('rb') lets us read the pipe one line at a time, exactly like reading a file.
        stream = sock.makefile("rb")
        for raw_line in stream:               # each iteration = the next newline-terminated line
            raw_line = raw_line.strip()
            if not raw_line:                  # skip blank keep-alive lines
                continue
            msg = json.loads(raw_line)        # text line  ->  Python dict
            handle_message(msg)               # <-- your logic goes here


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
    except (ConnectionError, OSError) as exc:
        print(f"\nconnection problem: {exc}\n"
              "Is the fitter running? Start it with:  python -m online_fitter.run")
