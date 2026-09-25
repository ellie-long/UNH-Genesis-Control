"""Wire protocol between LabVIEW (Cooldown_Experimental_2024.vi's VME Loop) and the
Python online fitter.

Transport: a loopback-only TCP stream (127.0.0.1). Framing: line-delimited JSON --
one JSON object per line, each terminated by ``\\r\\n`` (CRLF) so LabVIEW's TCP Read
"CRLF" mode reads a whole message directly. Incoming lines are ``.strip()``-ed, so a
bare ``\\n`` terminator is accepted too. JSON is used (rather than a packed binary
form) because LabVIEW 2018 can build/parse a JSON string trivially and the payloads
are tiny (~512 floats, a few kB, at ~1 Hz).

Client -> server (one per NMR sweep), a "sweep" message::

    {"type": "sweep",
     "ts": "6/17/2026 12:00:04 AM",   # optional, LabVIEW timestamp string
     "centfreq_mhz": 32.09,           # NMR window CENTRE frequency (MHz)
     "span_mhz": 0.40,                # full frequency span of the sweep (MHz)
     "npts": 512,                     # number of amplitude points
     "raw": [...],                    # raw digitized sweep (npts floats)
     "baseline_sub": [...]}           # Q-curve/pedestal baseline-subtracted line (npts floats)

The frequency axis is reconstructed EXACTLY as the McClellan reader does
(``cooldown_io.Cooldown.freq_axis``)::

    freqs = centfreq + span * (arange(npts)/npts - 0.5)      # MHz

Server -> client (reply to a sweep), a compact result the VME Loop may log::

    {"ok": true, "fit_type": "equilibrium",
     "P_area": 41.2, "Q_eq": 12.9, "P_fit": 40.8, "Q_fit": 12.3, "r": 1.73}

    {"ok": false, "error": "..."}    # on any failure (client should not crash)

The operator's fitting MODE is NOT part of the sweep message: it is chosen in the
Python GUI. LabVIEW only streams sweeps; it never needs to know the mode.
"""
import json

import numpy as np

PROTOCOL_VERSION = 1
DEFAULT_HOST = "127.0.0.1"      # loopback ONLY -- never the LAN (VME crate / RF switches live there)
DEFAULT_PORT = 8777            # distinct from VME crate / RF-switch ports
EOL = "\r\n"                   # CRLF line terminator (LabVIEW TCP Read "CRLF" mode reads a full line)


def freq_axis(centfreq_mhz, span_mhz, npts):
    """Frequency axis (MHz) for a sweep, identical to cooldown_io.freq_axis.

    Args:
        centfreq_mhz: NMR window centre frequency (MHz).
        span_mhz: full frequency span of the sweep (MHz).
        npts: number of amplitude samples.
    Returns:
        np.ndarray of npts frequencies (MHz), ascending.
    """
    n = int(npts)
    return float(centfreq_mhz) + float(span_mhz) * (np.arange(n) / n - 0.5)


def encode_sweep(centfreq_mhz, span_mhz, raw, baseline_sub, ts=None):
    """Build a client->server 'sweep' NDJSON line (bytes, newline-terminated).

    ``raw`` and ``baseline_sub`` are sequences of the SAME length (npts) sharing one
    frequency axis. Used by the Python mock client; the LabVIEW VI builds the same JSON.
    """
    raw = np.asarray(raw, float)
    base = np.asarray(baseline_sub, float)
    if raw.shape != base.shape:
        raise ValueError(f"raw ({raw.shape}) and baseline_sub ({base.shape}) length mismatch")
    msg = {
        "type": "sweep",
        "centfreq_mhz": float(centfreq_mhz),
        "span_mhz": float(span_mhz),
        "npts": int(raw.size),
        "raw": [round(float(x), 8) for x in raw],
        "baseline_sub": [round(float(x), 8) for x in base],
    }
    if ts is not None:
        msg["ts"] = str(ts)
    return (json.dumps(msg) + EOL).encode("utf-8")


def decode_message(line):
    """Parse one NDJSON line (str or bytes, with/without trailing newline) into a dict."""
    if isinstance(line, (bytes, bytearray)):
        line = line.decode("utf-8")
    return json.loads(line)


def encode_reply(obj):
    """Serialize a server->client reply dict to a CRLF-terminated JSON line (bytes)."""
    return (json.dumps(obj) + EOL).encode("utf-8")


def sweep_arrays(msg):
    """From a decoded 'sweep' message return (freqs, raw, baseline_sub) as float arrays.

    Reconstructs the freq axis from the header so the fitter sees exactly the same
    axis the offline pipeline would.
    """
    raw = np.asarray(msg["raw"], float)
    base = np.asarray(msg["baseline_sub"], float)
    npts = int(msg.get("npts", raw.size))
    freqs = freq_axis(msg["centfreq_mhz"], msg["span_mhz"], npts)
    return freqs, raw, base
