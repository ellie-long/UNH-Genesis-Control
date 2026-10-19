"""Offline-only tests for the Genesis dual NMR monitoring sidecar."""
import json
import socket
import threading
import time
import unittest

from scripts.genesis_nmr_dual_monitor import MonitorState, normalize, consume, Handler, ThreadingHTTPServer


def fixture(n=451):
    a = [float(i) / n for i in range(n)]
    return {"type": "fit", "ts": "test", "fit_type": "baseline", "material": "d-butanol",
            "npts": n, "freqs_mhz": [32 + 0.001 * i for i in range(n)],
            "raw": a, "data": a, "total_fit": a,
            "I_plus_primary": a, "I_minus_primary": a,
            "I_plus_secondary": a, "I_minus_secondary": a,
            "P_fit": None, "Q_fit": None}


class MonitorTests(unittest.TestCase):
    def test_expected_451_point_baseline_message(self):
        d = normalize(fixture())
        self.assertEqual(d["npts"], 451)
        self.assertIsNone(d["P_fit"])
        self.assertEqual(len(d["lines"]["total_fit"]), 451)

    def test_rejects_missing_or_inconsistent_arrays(self):
        d = fixture()
        d["raw"] = d["raw"][:-1]
        with self.assertRaises(ValueError):
            normalize(d)
        d = fixture()
        d["type"] = "sweep"
        with self.assertRaises(ValueError):
            normalize(d)

    def test_no_hardware_connection_mock_only(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        state = MonitorState()
        stop = threading.Event()
        thread = threading.Thread(target=consume, args=("labview", port, state, stop), daemon=True)
        thread.start()
        try:
            listener.settimeout(3)
            sock, _ = listener.accept()
            with sock:
                sock.sendall((json.dumps(fixture()) + "\n").encode())
                deadline = time.time() + 3
                while time.time() < deadline:
                    if state.snapshot()["labview"]["received"] >= 1:
                        break
                    time.sleep(.02)
                snap = state.snapshot()["labview"]
                self.assertEqual(snap["received"], 1)
                self.assertTrue(snap["fresh"])
                self.assertEqual(snap["spectrum"]["npts"], 451)
        finally:
            stop.set()
            listener.close()


if __name__ == "__main__":
    unittest.main()
