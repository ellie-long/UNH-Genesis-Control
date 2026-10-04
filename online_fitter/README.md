# online_fitter — live deuteron-NMR fitter for Cooldown_Experimental_2024.vi

Receives baseline-subtracted NMR sweeps from LabVIEW over a **loopback-only** socket,
fits them with the (vendored) McClellan Dulya spin-1 routines by an operator-chosen
mode, and shows a live pyqtgraph window: the raw DAQ line, the cubic-subtracted line
with the total fit and per-nucleus I+/I− components, and a P & Q vs time strip chart.

## Install

The canonical Genesis physical ssRF workflow runs the fitter through
`python -m online_fitter.epics_run`. This EPICS runtime is headless and uses
the shared Genesis `genesis-p1` environment.

From the repository root, activate the environment and install the canonical
Genesis runtime requirements:

    conda activate genesis-p1
    python -m pip install -r requirements.txt

The standalone `python -m online_fitter.run` graphical interface is optional
and is not part of the validated Genesis control-computer runtime. That GUI
additionally requires `pyqtgraph` and a compatible Qt Python binding.

The `online_fitter.procctl` helper and `--replace` option are retained for the
historical Windows/LabVIEW standalone workflow. The canonical Linux/WSL
physical Genesis launcher does not force-kill an unidentified previous fitter.
A conflicting port owner therefore causes startup to fail instead of being
terminated without established ownership.

## Run (normal use)

```
python -m online_fitter.run                  # server on 127.0.0.1:8777 + live GUI
python -m online_fitter.run --material ND3
python -m online_fitter.run --port 8777
```

In the window: pick the **Material**, pick a **Mode**
(Baseline / Equilibrium spin-up / ssRF-holeburn / Frequency-hopping / No NMR), and
during an equilibrium spin-up click **Update Constants** once there is good signal.
That runs a full open fit over the recent equilibrium sweeps to refresh every
lineshape constant and the area calibration `CC`, then rapid fitting resumes. The fit
runs in the background (the GUI stays live); if it lands on a bad line and grinds, the
button becomes **Stop Update Constants** — clicking it aborts cleanly (constants left
unchanged) so you can restart on a better window.

**Colors are red/green-colorblind-friendly.** The fit panel avoids green entirely:
**I+ is blue, I− is red, the total fit is magenta**, and the data points are light grey
(primary bonds solid, secondary bonds dashed). The strip chart likewise uses only
blue/cyan/orange/red — no red-vs-green pairing anywhere.

The P & Q strip chart has two controls: **auto-scale** (default on — uncheck to set the
axes manually; right-click the strip → set range, or drag/zoom, without live updates
snapping the view back) and **Clear P/Q** (discard the strip history, e.g. at the start of
a run or before Update Constants). The strip redraws at ~4 Hz so manual axis edits aren't
fighting the live updates; the spectra panels stay at 20 Hz.

Point LabVIEW's `NMRPythonFit.vi` (see `PROTOCOL.md`) at the same port.

## Modes → routine (all vendored McClellan fits)

| Mode | Routine | Reports |
|------|---------|---------|
| Equilibrium spin-up | `fit_signal_fast` (r + phase) | P_fit=ratio_P(r), Q_fit; P_area=CC·area, Q_eq |
| Update Constants | `fit_signal` (full open fit) | refreshes all constants + `CC`; builds the ssRF reference |
| ssRF / holeburn | `holeburn.run` (area method) | P_fit/Q_fit from the hole-burnt area method |
| Frequency-hopping | `fit_freqhop` (rN, rP) | two-spin-temperature P/Q |
| Baseline | display only | shows the flattened line, no P/Q |
| No NMR | idle | strip chart shows a gap |

`P_area` is `null` until the first **Update Constants** (the area `CC` isn't known yet);
`P_fit` works immediately from the material preset.

### First-guess fit parameters (seeding)

Following McClellan's `preset_to_p0`: the **Larmor frequency `omegD` is seeded from the
window centre** (the sweep header's centre freq — data-driven, because `omegD` moves with
field), **`r` from 1.5**, and **every other constant** (ω_Q, A, η, K, scale, phase) from the
selected material's **historical preset**. This applies both to the initial fast fits (before
any calibration) and to the Update Constants full fit (which then floats them all). Seeding
`omegD` from the data — not the preset — is what makes the first fits work at a new field:
verified, the first-attempt fast fit gives P≈21% vs the offline 23–24% with **no** Update
Constants yet (previously it railed because the preset's field-specific `omegD` was wrong).
Run **Update Constants** to refine everything for the current field/tune.

### ssRF / hole-burnt: r_eq comes from the line's Larmor region

The hole-burnt fit reconstructs the burnt signal relative to an equilibrium reference —
its lineshape + amplitude (`scale`) from the last Update Constants, and the equilibrium
asymmetry `r_eq`. The RF manipulation deliberately never crosses the Larmor frequency ω_D
(crossing causes AFP-type effects), so **the ω_D region of the burnt line itself stays at
equilibrium**. The engine therefore reads `r_eq` from that region of the *current* line
(median per-bin `r` within `|f − ω_D| ≤ Larmor ±kHz`) rather than importing it from a
separate equilibrium line — so `r_eq` tracks drift automatically and is more accurate.
Verified: on a matched reference the Larmor `r_eq` gives P=43.2% (offline 43.1%), vs 44.4%
using the imported reference `r_eq`.

The **Larmor ±kHz** control (default 10 kHz) sets the half-width; **0 disables it** (fall
back to the reference `r_eq`).

**What still needs a matched reference:** `scale`/shape. The per-bin `r` inverts on a grid
capped at `r≈3.5`, so if the reference `scale` is far too weak (wrong tune/mode, or Update
Constants run on an early low-P sweep) the inversion rails and P is untrustworthy. Guard:
run **Update Constants during the equilibrium spin-up the manipulation starts from** (same
tune/mode) before switching to ssRF, and re-run it when tune/mode/field changes. The GUI
shows **⚠ stale reference?** on the fit panel when the reconstruction under-reaches the data
peak or the Larmor `r_eq` rails.

## Test without LabVIEW / hardware

Uses the copied `test_data/2026-06-Cooldown/data_record_2026-06-17.csv` (gitignored).

```
# terminal 1: start the fitter (server + GUI)
python -m online_fitter.run --mode equilibrium

# terminal 2: stream a previous cooldown as if live
python -m online_fitter.mock_labview_client --rate 20 --start 10300 --limit 10000 --polarization-only
```

Then in the GUI click **Update Constants** during the stream. Verified end-to-end: after
calibrating on the high-P region, the equilibrium fit reproduces the file's live area-P
to a few percent (e.g. P_fit≈48.6%, P_area≈47.7% vs the DAQ's live 44.3%).

For the LabVIEW-path pre-cooldown gate, pre-flatten the day and drive `NMRReplay.vi`:

```
python -m online_fitter.make_replay_file --limit 5000 -o replay_2026-06-17.ndjson
```

## Layout

| file | role |
|------|------|
| `protocol.py` | NDJSON wire format + freq-axis reconstruction |
| `engine.py` | fit orchestration (set_material, update_constants, fit_line routing) |
| `server.py` | loopback-only TCP server → result queue |
| `gui.py` | pyqtgraph window (3 panels + mode/material/Update Constants controls) |
| `run.py` | entry point wiring engine + server + GUI |
| `replay_source.py` | reads `data_record_*.csv`, emulates VMEQCorrect baseline subtraction |
| `mock_labview_client.py` | pure-Python stream-as-if-live test client |
| `make_replay_file.py` | pre-flatten a cooldown → compact replay file for `NMRReplay.vi` |
| `vendored/` | verbatim copy of the McClellan fitter (`VENDORED_FROM.md`) — do not edit |

## Note on absolute calibration

The engine faithfully runs the McClellan `fit_signal`/`fit_signal_fast` (verified bit-for-bit:
`update_constants` matches `fit_signal` exactly). Absolute P depends on which sweeps you
average for **Update Constants** — the full free fit's `scale`/`r` are mildly degenerate at
moderate P, so calibrate on a clean, representative high-P equilibrium plateau (as the
offline pipeline does with its golden window). Re-run Update Constants whenever the
amplifier setup or field changes.
