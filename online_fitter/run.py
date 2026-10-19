"""Entry point: start the loopback fit server and (optionally) the live pyqtgraph GUI.

    python -m online_fitter.run                     # server + GUI (normal use)
    python -m online_fitter.run --material ND3
    python -m online_fitter.run --no-gui            # headless server (for tests/CI)
    python -m online_fitter.run --port 8777

The server binds 127.0.0.1 ONLY, so it never touches the lab LAN (VME crate / RF
switches). Point LabVIEW's NMRPythonFit.vi (or the mock client) at the same port.
"""
import argparse
import logging
import logging.handlers
import os
import sys
import time

from . import protocol
from . import engine as E
from . import server as S
# publisher (fit-result export stream) and procctl (--replace) are OPTIONAL extras. A partial file
# copy that is missing one of them must never stop the core fitter (server + GUI) from starting.
try:
    from . import publisher as P
except Exception:
    P = None
try:
    from . import procctl
except Exception:
    procctl = None

_DEFAULT_PUBLISH_PORT = getattr(P, "DEFAULT_PUBLISH_PORT", 8778) if P is not None else 8778


def _setup_logging():
    """Log to console AND a rotating file under logs/, so an overnight run leaves a trail we can read
    if it stops (heartbeats, connection open/close, exceptions). The logs/ folder is git-tracked but
    its contents are git-ignored (machine-specific)."""
    handlers = [logging.StreamHandler(sys.stdout)]
    logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")   # inside online_fitter/
    try:
        os.makedirs(logdir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(logdir, "online_fitter.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        handlers.append(fh)
    except Exception:
        logdir = None
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    if logdir:
        logging.getLogger("online_fitter").info("logging to %s", os.path.join(logdir, "online_fitter.log"))


def _win_set_dpi_awareness(mode):
    """Set the Windows process DPI awareness BEFORE Qt initializes. No-op off Windows.

    - system:     one uniform scale (the primary monitor's); on another monitor Windows scales the
                  whole window and translates input, so it stays CLICKABLE everywhere (may look soft).
    - permonitor: crisp per-monitor scaling, but fractional scales can offset hit-testing on the
                  non-primary monitor (the "can't click / clipped on the 2nd monitor" symptom).
    - unaware:    native pixels; Windows bitmap-stretches everything.
    """
    if mode == "unaware":
        # Pin Qt to a fixed 96-DPI / 1x scale via env vars (must be set BEFORE Qt import). This forces
        # logicalDPI to MATCH the device-pixel ratio (both 1x), which is what stops pyqtgraph's axes
        # from being 'dropped' on a fractional-scale monitor (e.g. 125% -> logicalDPI 120). Env vars
        # win even when python.exe's manifest locks the process DPI awareness so the ctypes call below
        # is ignored (the case on the DAQ's miniconda Python).
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
        os.environ["QT_SCALE_FACTOR"] = "1"
        os.environ["QT_FONT_DPI"] = "96"
    if sys.platform != "win32":
        return
    ctx = {"unaware": -1, "system": -2, "permonitor": -4}.get(mode, -2)      # SetProcessDpiAwarenessContext
    legacy = {"unaware": 0, "system": 1, "permonitor": 2}.get(mode, 1)       # older shcore fallback
    try:
        import ctypes
        user32 = ctypes.windll.user32
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(ctx))
        else:
            ctypes.windll.shcore.SetProcessDpiAwareness(legacy)
    except Exception:
        pass                                     # not fatal -- fall back to Qt's own handling


def _qt_set_dpi_attrs(mode, QtWidgets, QtCore):
    """Qt high-DPI attributes matching the process awareness. Must run before the QApplication exists."""
    if mode == "unaware":
        return                                   # native pixels; let Windows scale
    try:
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
        # INTEGER rounding keeps widget hit-areas aligned with what's drawn. PassThrough's fractional
        # scale factors were misplacing clicks on the non-primary monitor.
        pol = getattr(QtCore.Qt, "HighDpiScaleFactorRoundingPolicy", None)
        if pol is not None:
            QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(pol.Round)
    except Exception:
        pass                                     # older Qt without one of these knobs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=protocol.DEFAULT_HOST, help="loopback host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    ap.add_argument(
        "--nmr-protected",
        action="store_true",
        help="opt-in protected dual-source NMR receiving mode",
    )
    ap.add_argument(
        "--nmr-source",
        choices=("labview", "pynmr"),
        default=None,
        help="software source selection; does not move hardware",
    )
    ap.add_argument(
        "--pynmr-port",
        type=int,
        default=8779,
        help="protected local PyNMR input port",
    )
    ap.add_argument(
        "--pynmr-ipc-path",
        default=None,
        help="Opt-in absolute PyNMR IPC Unix socket path",
    )
    ap.add_argument(
        "--pynmr-ipc-allowed-uid",
        type=int,
        action="append",
        default=None,
        help="Allowed IPC peer UID; repeatable",
    )
    ap.add_argument(
        "--pynmr-ipc-shared-gid",
        type=int,
        default=None,
        help="Optional Unix socket shared-group GID",
    )
    ap.add_argument("--material", default="d-butanol", choices=list(E.MATERIAL_NAMES))
    ap.add_argument("--mode", default=E.MODE_BASELINE, choices=list(E.ALL_MODES))
    ap.add_argument("--eq-buffer", type=int, default=E.EQ_BUFFER_DEFAULT,
                    help="# recent equilibrium sweeps averaged by Update Constants")
    ap.add_argument("--no-gui", action="store_true", help="run the server only (no display)")
    ap.add_argument("--verbose", action="store_true",
                    help="print every connection, received line, and reply (LabVIEW debugging)")
    ap.add_argument("--publish", dest="publish", action="store_true", default=True,
                    help="broadcast full fit results (constants + processed data + fit + raw) on a "
                         "loopback port for other software to subscribe to (default: ON)")
    ap.add_argument("--no-publish", dest="publish", action="store_false",
                    help="disable the fit-result broadcast stream")
    ap.add_argument("--publish-port", type=int, default=_DEFAULT_PUBLISH_PORT,
                    help=f"port for the fit-result publisher (default {_DEFAULT_PUBLISH_PORT})")
    ap.add_argument("--replace", action="store_true",
                    help="before starting, hard-stop any OTHER online_fitter.run already running "
                         "(by command line + owner of --port); leaves all other Python scripts alone. "
                         "For the LabVIEW 'Reset Fitter' button.")
    ap.add_argument("--dpi-mode", default="unaware", choices=["system", "permonitor", "unaware"],
                    help="Windows multi-monitor DPI handling. 'unaware' (default): consistent 96-DPI "
                         "1x render on every monitor so plot axes line up and the window stays "
                         "clickable (Windows scales it, so it may look soft on a high-DPI monitor). "
                         "'system': one uniform scale, clickable, but fractional scaling (e.g. 125 "
                         "percent) misplaces the plot axes. 'permonitor': crisp per-monitor, but can "
                         "misplace clicks on fractional scaling.")
    ap.add_argument("--nmr-source-control-path", default=None,
                    help="Opt-in Linux source-control Unix socket path")
    ap.add_argument("--nmr-source-control-allowed-uid", type=int, default=None,
                    help="Trusted sequencer Linux UID for source-control IPC")
    ap.add_argument("--nmr-source-control-allowed-pid", type=int, default=None,
                    help="Trusted sequencer Linux PID for source-control IPC")

    a = ap.parse_args(argv)
    # All-or-none, opt-in, and separate from physical ssRF execution.
    control_path = a.nmr_source_control_path
    control_uid = a.nmr_source_control_allowed_uid
    control_pid = a.nmr_source_control_allowed_pid
    if any(value is not None for value in (control_path, control_uid, control_pid)):
        from pathlib import Path as _NMRControlPath
        if not a.nmr_protected:
            ap.error("NMR source-control IPC requires protected NMR mode")
        if (type(control_path) is not str or not control_path
                or not _NMRControlPath(control_path).is_absolute()):
            ap.error("NMR source-control IPC requires absolute socket path")
        if type(control_uid) is not int or control_uid < 0:
            ap.error("NMR source-control IPC requires trusted UID")
        if type(control_pid) is not int or control_pid <= 0:
            ap.error("NMR source-control IPC requires trusted PID")
        if control_path == a.pynmr_ipc_path:
            ap.error("NMR source-control and PyNMR event sockets must differ")
    if a.nmr_protected:
        if a.nmr_source is None:
            ap.error("--nmr-protected requires --nmr-source")
        if a.replace:
            ap.error("--replace is disabled in protected NMR mode")
        if len({a.port, a.pynmr_port, a.publish_port}) != 3:
            ap.error("NMR input and publisher ports must differ")
    elif a.nmr_source is not None:
        ap.error("--nmr-source requires --nmr-protected")
    # Protected PyNMR IPC is explicit and fail-closed.
    if a.pynmr_ipc_path is not None:
        from pathlib import Path as _IPCPath
        if not a.nmr_protected:
            ap.error("PyNMR IPC requires protected NMR mode")
        if not _IPCPath(a.pynmr_ipc_path).is_absolute():
            ap.error("PyNMR IPC socket path must be absolute")
        if not a.pynmr_ipc_allowed_uid:
            ap.error("PyNMR IPC requires explicit allowed UIDs")
        if any(uid < 0 for uid in a.pynmr_ipc_allowed_uid):
            ap.error("IPC UIDs must be nonnegative")
        if (
            a.pynmr_ipc_shared_gid is not None
            and a.pynmr_ipc_shared_gid < 0
        ):
            ap.error("IPC shared GID must be nonnegative")
    elif (
        a.pynmr_ipc_allowed_uid is not None
        or a.pynmr_ipc_shared_gid is not None
    ):
        ap.error("IPC credentials require --pynmr-ipc-path")

    _setup_logging()

    if a.replace and procctl is None:
        print("[online_fitter] --replace requested but procctl.py is missing from this copy; "
              "starting anyway without replacing a previous instance.", flush=True)
    if a.replace and procctl is not None:
        killed = procctl.terminate_previous(port=a.port, verbose=True)
        if killed:
            # give the OS a moment to release the just-killed process's listening socket before we
            # try to bind it; retry the bind for a few seconds rather than racing the port release.
            print(f"[online_fitter] replaced previous instance(s): {killed}", flush=True)

    pub = None
    if a.publish and P is None:
        print("[online_fitter] --publish requested but publisher.py is missing from this copy; "
              "starting without the export stream.", flush=True)
    elif a.publish:
        pub = P.Publisher(host=a.host, port=a.publish_port).start()
        print(f"[online_fitter] publishing fit results on {a.host}:{a.publish_port} "
              f"(subscribe for the fitted lineshape + components)", flush=True)

    eng = E.FitEngine(material=a.material, eq_buffer=a.eq_buffer)
    state = S.SharedState(eng, mode=a.mode, publisher=pub)
    # Bind with a short retry: after --replace the just-killed instance's listening socket can linger
    # for a beat, and TCP servers on the same port fail with "address in use" until it releases.
    if a.nmr_protected:
        from epics import PV
        from .protected_runtime import ProtectedNMRRuntime

        srv = ProtectedNMRRuntime(
            state,
            selected_source=a.nmr_source,
            nmr_source_control_path=a.nmr_source_control_path,
            nmr_source_control_allowed_uid=a.nmr_source_control_allowed_uid,
            nmr_source_control_allowed_pid=a.nmr_source_control_allowed_pid,
            pynmr_ipc_path=a.pynmr_ipc_path,
            pynmr_ipc_allowed_uids=(
                tuple(a.pynmr_ipc_allowed_uid)
                if a.pynmr_ipc_path is not None
                else None
            ),
            pynmr_ipc_shared_gid=a.pynmr_ipc_shared_gid,
            pv_factory=PV,
            labview_port=a.port,
            pynmr_port=a.pynmr_port,
            verbose=a.verbose,
        )
        srv.start()
        state.genesis_nmr_protected_runtime = srv
        print(
            "[online_fitter] protected dual NMR receivers on "
            f"127.0.0.1:{a.port} (LabVIEW) and "
            f"127.0.0.1:{a.pynmr_port} (PyNMR)",
            flush=True,
        )
        print(
            "[online_fitter] fitting remains blocked until "
            "trusted acquisition-event binding is implemented",
            flush=True,
        )
    else:
        deadline = time.time() + (6.0 if a.replace else 0.0)
        while True:
            try:
                srv = S.make_server(state, host=a.host, port=a.port, verbose=a.verbose)
                break
            except OSError as exc:
                if time.time() >= deadline:
                    print(f"[online_fitter] could not bind {a.host}:{a.port} -- {exc}\n"
                          f"  another process may still hold the port. Try: python -m online_fitter.stop "
                          f"--port {a.port}", flush=True)
                    raise
                time.sleep(0.3)
        S.serve_in_thread(srv)
        print(f"[online_fitter] loopback fit server on {a.host}:{a.port}  "
              f"(material={a.material}, mode={a.mode})", flush=True)

    if a.no_gui:
        print("[online_fitter] headless -- Ctrl+C to stop.", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nstopped.")
        finally:
            srv.shutdown()
        return

    # GUI (imported lazily so --no-gui works without a display / pyqtgraph).
    # Multi-monitor DPI: configure the process awareness BEFORE any Qt import, then the matching Qt
    # attributes before the QApplication exists. Mode is --dpi-mode (default 'system' = clickable on
    # every monitor). See _win_set_dpi_awareness for the trade-offs.
    _win_set_dpi_awareness(a.dpi_mode)
    from pyqtgraph.Qt import QtWidgets, QtCore
    from . import gui
    if QtWidgets.QApplication.instance() is None:
        _qt_set_dpi_attrs(a.dpi_mode, QtWidgets, QtCore)
    print(f"[online_fitter] DPI mode: {a.dpi_mode}", flush=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    startup_note = f"running: {__file__}  |  dpi-mode: {a.dpi_mode}"
    win = gui.NMRWindow(state, startup_note=startup_note)
    win.show()
    try:
        app.exec_()
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
