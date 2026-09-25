# LabVIEW ↔ Python wire protocol & VI specs

The Python online fitter listens on a **loopback-only** TCP socket
(`127.0.0.1:8777` by default). LabVIEW streams each baseline-subtracted NMR sweep to
it and reads back a compact P/Q result. This never touches the lab LAN, so the VME
crate and the networked RF switches are unaffected.

## Transport & framing

- TCP, `127.0.0.1` only. Default port **8777** (change with `run.py --port`).
- **Newline-delimited JSON (NDJSON):** one JSON object per message, terminated by a
  single `\n`. Send a request line, read one reply line.

## Client → server: `sweep` (one per NMR sweep in the VME Loop)

```json
{"type":"sweep","ts":"6/17/2026 9:31:27 PM",
 "centfreq_mhz":32.09,"span_mhz":0.40,"npts":451,
 "raw":[...],"baseline_sub":[...]}
```

| field | meaning |
|-------|---------|
| `ts` | LabVIEW timestamp string (optional; used for the strip-chart x-axis) |
| `centfreq_mhz` | NMR window **centre** frequency (MHz) |
| `span_mhz` | full frequency span of the sweep (MHz) |
| `npts` | number of amplitude points (length of both arrays) |
| `raw` | raw digitized sweep — shown as-is on the Python "Raw NMR" panel |
| `baseline_sub` | the Q-curve/pedestal **baseline-subtracted** line — Python cubic-flattens & fits this |

Frequency axis (Python reconstructs it exactly as the offline reader does):

```
freqs = centfreq_mhz + span_mhz * (i/npts - 0.5),   i = 0 .. npts-1     # MHz
```

The operator's fitting **mode** is NOT in this message — it is chosen in the Python
GUI. LabVIEW only streams sweeps.

## Server → client: reply

```json
{"ok":true,"fit_type":"equilibrium",
 "P_area":41.2,"Q_eq":12.9,"P_fit":40.8,"Q_fit":12.3,"r":1.73}
```

`P_*`/`Q_*` are percent, or `null` when not available (e.g. `P_area` before the first
Update Constants, since the area calibration `CC` is not yet known). On a bad message:
`{"ok":false,"error":"..."}`. A soft warning (e.g. ssRF before Update Constants) comes
back as `{"ok":true,...,"warning":"..."}`. The VME Loop may log the reply or ignore it.

Also supported: `{"type":"ping"}` → `{"ok":true,"pong":true,"status":{...}}`.

---

# Restarting the fitter (LabVIEW "Reset Fitter" button)

The DAQ machine runs many Python scripts at once, so every one is just `python.exe` in
Task Manager — you can't restart the fitter by killing `python.exe` broadly. Two safe ways
to stop **only** `online_fitter.run` (it hard-kills even a *hung* instance, and never touches
the other Python scripts):

**A. `--replace` on launch (recommended — one command, self-contained).** Point the Reset
button's launch at:
```
cmd /c python -m online_fitter.run --replace
```
On startup it finds any other `online_fitter.run` (by command line **and** whoever owns the
loopback port) and hard-stops it, then waits for the port to free and binds. So the Reset
button just: close the LabVIEW TCP connection → run this command → reopen the TCP connection
(after a ~2 s startup delay).

**B. A separate stop step.** If you'd rather stop then start as two actions:
```
cmd /c python -m online_fitter.stop        &&  cmd /c python -m online_fitter.run
```
`online_fitter.stop` kills only the fitter and returns; it is self-safe (it never kills its
own process). Add `--port 8779` to either command if you moved off the default 8777.

**C. Pure LabVIEW / no Python change** (if you don't want to update the Python side). Run this
in a System Exec before the launch — it kills only the python that OWNS the fitter port:
```
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8777 -State Listen | ForEach-Object { $p=Get-Process -Id $_.OwningProcess; if ($p.ProcessName -like 'python*') { Stop-Process -Id $p.Id -Force } }"
```
(Options A/B are preferred — they also catch a fitter that crashed *without* releasing the
port, or was started but failed to bind.)

Either way: **hard kill by PID**, so it recovers a frozen instance that has stopped answering
its socket — which a cooperative "please quit" message could not.

---

# Fit-result publish stream (for other software)

Separately from the LabVIEW request/reply above, the fitter can **broadcast** each fitted
sweep to other programs (MATLAB, another Python tool, LabVIEW, C#, …). Start it with:
```
python -m online_fitter.run --publish            # default publish port 8778
python -m online_fitter.run --publish --publish-port 8779
```
Any number of subscribers connect a TCP client to `127.0.0.1:<publish port>` and read
**newline-delimited JSON** — one line per fitted sweep. Each line carries four things, in this
order:

1. **good fit constants** — `constants` (CC, CC_DAQ, and the lineshape constants); `constants_good`
   is `true` once calibrated (after Update Constants or an import), `false` while on preset seeds.
2. **processed NMR data** — `data` (the cubic-subtracted line).
3. **processed NMR fit** — `total_fit` plus the four components I+/I- for the primary and secondary
   bonds. For single-bond **ND3** the two secondaries are streamed as **all zeros**.
4. **raw NMR** — `raw` (the sweep exactly as received from LabVIEW).

```json
{"type":"fit","ts":"6/17/2026 9:31:27 PM","fit_type":"equilibrium","mode":"equilibrium",
 "material":"d-butanol","model":"double","npts":451,
 "freqs_mhz":[...],                       // frequency axis (MHz), shared by every array below
 "P_area":41.2,"P_fit":40.8,"Q_eq":12.9,"Q_fit":12.3,"r":1.73,"phase_deg":2.1,"red_chisq":1.1,
 "constants_good":true,                   // 1. good fit constants
 "constants":{"source":"fit","material":"d-butanol","model":"double",
              "CC":7.85,"CC_DAQ":732.8,"red_chisq":1.1,
              "omegD":32.09,"omegQ1":...,"omegQ2":...,"A1":...,"eta1":...,"eta2":...,
              "K":...,"scale":...,"phase":...,"r":1.73},
 "data":[...],                            // 2. processed (cubic-subtracted) NMR data
 "total_fit":[...],                       // 3. processed NMR fit: total ...
 "I_plus_primary":[...],  "I_minus_primary":[...],       //    ... + primary components
 "I_plus_secondary":[...], "I_minus_secondary":[...],    //    ... + secondary (0s for ND3)
 "raw":[...]}                             // 4. raw NMR
```
All arrays share the `freqs_mhz` axis. `constants` is `null` before the first sweep sets a window;
non-finite samples (e.g. component arrays in Baseline / No-NMR mode) are JSON `null`. Lines are
CRLF-terminated (LabVIEW CRLF-read friendly).

- **Loopback only** (`127.0.0.1`) — same machine as the fitter. It never binds the LAN.
- **Non-blocking / drop-slow:** a subscriber that can't keep up (or disconnects) is dropped;
  it can never stall the fitter or the acquisition. Publishing is a no-op when nobody is
  subscribed.
- Reference consumer: `python -m online_fitter.subscribe_example` (also the code to copy
  when writing a consumer in another language).

> If the consuming software runs on a **different machine**, this loopback publisher won't
> reach it — that would require binding a LAN interface (which reintroduces the VME-crate /
> RF-switch LAN concern) or a file/relay hop. Decide per deployment.

---

# LabVIEW VI specs (to be built in LabVIEW 2018)

Two new VIs. **Do not modify** `VMEClient.vi`, `VMEParser.vi`, `VMEQCorrect.vi`,
`VMEFit.vi`, or the RF-switch VIs.

## 1. `NMRPythonFit.vi` — the send subVI (lives in the VME Loop)

Drop this **inside the "VME Loop"** of `Cooldown_Experimental_2024.vi`, fed by
**branching** the wire that already drives the **"NMR Graph + Fit"** plot. It runs
alongside the existing `VMEFit` path (non-invasive).

Inputs: the raw sweep array, the baseline-subtracted sweep array, centre freq (MHz),
span (MHz), timestamp string, plus a connection refnum (opened once before the loop).

Block-diagram outline:

1. **Before the loop:** `TCP Open Connection` → `127.0.0.1`, port `8777`. Keep the
   connection refnum in a shift register (open once, not per iteration).
2. **Each iteration:**
   - Build the JSON string for the `sweep` message above (LabVIEW 2018 has
     *Flatten To JSON*, or concatenate strings). Arrays go in as JSON numeric arrays.
   - Append a line feed (`\n`).
   - `TCP Write` the string. Use a short timeout (e.g. 100 ms).
   - `TCP Read` one line up to `\n` (mode: *CRLF* or read until LF) for the reply.
     Optionally parse P/Q for the LabVIEW logbook. **Ignore/label read timeouts** so a
     stalled Python side never backs up acquisition.
   - Route TCP errors to a non-fatal indicator (clear the error so the VME Loop keeps
     running if Python is closed).
3. **After the loop:** `TCP Close Connection`.

Keep the whole branch on a short timeout / non-blocking path: the NMR acquisition must
never wait on Python.

## 2. `NMRReplay.vi` — standalone pre-cooldown test harness

A small standalone VI (NOT in the cooldown program) that replays a previous
cooldown to validate the whole LabVIEW→Python path before a real run.

1. In Python, pre-flatten a previous cooldown once:
   `python -m online_fitter.make_replay_file --limit 5000 -o replay_2026-06-17.ndjson`
   Each line of that file **is** a ready-to-send `sweep` message.
2. `NMRReplay.vi`: open the `.ndjson` file, then loop over its lines. For each line,
   call the **same `NMRPythonFit.vi` send subVI** (or inline the same TCP Write/Read),
   with a delay (e.g. 50–200 ms) to emulate the live sweep rate. Read/label the reply.
3. Start the Python side first (`python -m online_fitter.run`), pick the material and
   **Equilibrium** mode, run `NMRReplay.vi`, click **Update Constants** in the Python
   GUI once the spin-up shows signal, and watch the strip chart populate.

Because `NMRReplay.vi` drives the exact same send subVI the VME Loop uses, a clean
replay means the live path is ready.
