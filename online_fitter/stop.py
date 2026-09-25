"""Stop a running online_fitter.run -- and ONLY online_fitter.run.

    python -m online_fitter.stop                 # stop the fitter on the default port (8777)
    python -m online_fitter.stop --port 8779

Intended for the LabVIEW "Reset Fitter" button: run this first, then launch a fresh
``python -m online_fitter.run``. It kills only the fitter (by command line + port owner),
never the other Python scripts on the machine, and hard-kills a hung instance. See
``procctl.terminate_previous`` for exactly what it targets.
"""
import argparse

from . import protocol
from . import procctl


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=protocol.DEFAULT_PORT,
                    help=f"fitter loopback port to clear (default {protocol.DEFAULT_PORT})")
    a = ap.parse_args(argv)
    killed = procctl.terminate_previous(port=a.port, verbose=True)
    if killed:
        print(f"stopped online_fitter.run (PIDs: {', '.join(map(str, killed))})")
    else:
        print("no running online_fitter.run found")


if __name__ == "__main__":
    main()
