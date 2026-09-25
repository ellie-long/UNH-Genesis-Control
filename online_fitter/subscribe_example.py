"""Example subscriber for the fit-result publish stream.

Run the fitter with publishing on:
    python -m online_fitter.run --publish
then run this:
    python -m online_fitter.subscribe_example

It connects to the loopback publish port and prints one summary line per fitted sweep.
This is a reference for consuming the stream from ANY software: connect a TCP client to
127.0.0.1:<publish port>, read newline-delimited JSON, and each line is one fit with the
frequency axis, the processed data, the total fit, and the four component curves:

    {"type":"fit","ts":"...","fit_type":"equilibrium","P_fit":41.2,"Q_fit":12.3,...,
     "freqs_mhz":[...], "data":[...], "total_fit":[...],
     "I_plus_primary":[...], "I_minus_primary":[...],
     "I_plus_secondary":[...], "I_minus_secondary":[...]}

Non-finite samples (e.g. all-null component arrays in Baseline mode) are JSON null.
"""
import argparse
import json
import socket

from . import publisher as P


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=P.DEFAULT_PUBLISH_PORT)
    a = ap.parse_args(argv)

    print(f"subscribing to fit stream at {a.host}:{a.port} ... (Ctrl+C to stop)")
    with socket.create_connection((a.host, a.port), timeout=10) as sock:
        rf = sock.makefile("rb")
        for raw_line in rf:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            m = json.loads(raw_line)

            def n(k):
                return "--" if m.get(k) is None else round(m[k], 2)

            def ln(k):
                v = m.get(k)
                return 0 if v is None else len(v)

            c = m.get("constants") or {}
            cc = "--" if c.get("CC") is None else round(c["CC"], 5)
            cc_daq = "--" if c.get("CC_DAQ") is None else round(c["CC_DAQ"], 3)
            good = "GOOD" if m.get("constants_good") else "preset"
            # 1. constants   2. processed data   3. fit + components   4. raw
            print(f"{m.get('ts')}  {m.get('material')}/{m.get('model')} {m.get('fit_type'):<11} "
                  f"P_fit={n('P_fit')} Q_fit={n('Q_fit')} P_area={n('P_area')} | "
                  f"const[{good}] CC={cc} CC_DAQ={cc_daq} | "
                  f"data={ln('data')} fit={ln('total_fit')} "
                  f"I+p={ln('I_plus_primary')} I-p={ln('I_minus_primary')} "
                  f"I+s={ln('I_plus_secondary')} I-s={ln('I_minus_secondary')} | "
                  f"raw={ln('raw')}")


if __name__ == "__main__":
    main()
