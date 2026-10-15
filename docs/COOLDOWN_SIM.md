# Simulated cool-downs (`cooldown_sim`)

Run the **real, unmodified Genesis stack** against a past cool-down or a
closed-loop simulated target, either:

* **interactively**, to rehearse a cool-down at the normal operator screens.
  Operator actions change what the simulated target does.
* **unattended (regression)**, ending in a PASS/FAIL report. Use this to check
  that a software change has not broken anything.

Every part of the live system runs unchanged:

* the device IOCs (through `master_ioc.py`), drivers, calibrations and
  interlocks;
* the target status, alarm summary and archiver;
* the online fitter;
* the Phoebus screens.

Only the instruments are replaced, by loopback emulators that speak each
instrument's own protocol.

> **Simulation never touches hardware or the live system.** The tool refuses to
> start on the production host or beside any running Genesis stack. It runs
> only on private loopback ports, and it writes only inside its own run
> directory.

## 1. One-time setup

| Need | How |
|---|---|
| Python env | `conda env create -f environment.yml`, `conda activate genesis-p1`, `pip install -r requirements-dev.txt` (Windows or Linux/WSL) |
| Past cool-downs | Set `GENESIS_REPLAY_DATA` to the folder of `*-Cooldown` directories, e.g. `.../Optimizied-McClellan-Fits/data`. Real data never goes in git. |
| Phoebus (for screens) | Java 21 plus a Phoebus release: unzip `phoebus-5.0.5-win.zip` (or `-linux.tar.gz`) from github.com/ControlSystemStudio/phoebus/releases into `~/phoebus/`. Set `PHOEBUS_BIN` if you put it elsewhere. |

Check a host before the first run:

    python -m cooldown_sim guard      # exit 0 = safe; exit 2 = live Genesis detected
    python -m cooldown_sim ports      # private replay ports, all free?
    python -m cooldown_sim cooldowns  # what past cool-downs are available

## 2. Interactive rehearsal

    python -m cooldown_sim run --mode interactive --legs cryo,nmr \
        --cooldown 2026-06 --start "2026-06-17 11:00" --speed 60
    python -m cooldown_sim phoebus --screens cryo,fitter     # in a second terminal

The session starts **paused** at the chosen moment. Then:

1. Open the screens.
2. Press **Run** on Sim Control.
3. Operate the target from the normal Cryo and NMR-fitter screens.

**Sim Control** (`UNH:GEN:SIM`, red SIMULATION banner) gives you:

* **Clock:** Run/Pause, speed 1x-600x, jump ±10/60 min, and seek to a time.
  Seeking re-initialises the target from the record.
* **Instrument faults** for each emulated instrument:
  * `refuse`: unplugged
  * `absent`: not installed
  * `silent`: answers nothing
  * `garbage`: unparseable replies
  * `nak`: the instrument refuses the request
* **Target model:** the true P and Q (and the single-temperature Q), lattice and
  spin temperatures, field, microwave drive, the fitter's P and its error.
  Inputs:
  * microwave on/off, frequency and power;
  * coldboard heat;
  * NMR noise and phase;
  * line offset;
  * Take baseline;
  * **QUENCH** / Clear quench.
* **Autopilot** for Microwave, ssRF and Fitter. The autopilot replays the
  operator actions logged in the record. Switching a leg to **Human** hands it
  to you at once.
* **Session:**
  * Logbook notes and Mark, stamped with sim and wall time.
  * **End session** stops everything and writes the report.

The same controls work from a terminal:

    python -m cooldown_sim ctl status
    python -m cooldown_sim ctl pause | play | speed 120 | jump -30 | seek 2026-06-17 18:00
    python -m cooldown_sim ctl fault ls224_fridge silent        # ... fault ls224_fridge none
    python -m cooldown_sim ctl model quench 1                     # coldboard_heat_W 2, mw_on 0, ...
    python -m cooldown_sim ctl override tpg262 OVC 2e-3          # force a recorded channel (recorded units)
    python -m cooldown_sim ctl override tpg262 OVC record        # ... back to the record
    python -m cooldown_sim ctl note "TE looks low"
    python -m cooldown_sim ctl stop

Every session also writes `after_action.md`, with the timeline of actions,
faults, notes and state changes. With the NMR leg it also writes
`after_action.html` (true P against fitted P). Read these after a rehearsal.

### Things worth rehearsing

* **NMR calibration.** Before **Update Constants**, the fitter reads about 12
  points low on real-amplitude lines. That is realistic: the preset constants
  do not match the DAQ gain. After calibration it tracks the truth to within
  about 0.1 pp. Calibrate on a steady, well-polarized line: Update Constants
  takes P from the line asymmetry, which is nearly flat at a few percent. On a
  ~6 % or still-changing line the constants come out tens of percent off,
  just as they would live.
* **Baselines.** Shift the line off-window (*Line offset* about 1 MHz), take
  the baseline, then shift it back. Taking a baseline with the line in the
  window subtracts the signal, and the fitter then reads about 0 %. That is the
  real mistake, reproduced.
* **Microwaves.** Switching microwaves off early relaxes P with
  T1 ≈ 3.2 h. Moving the frequency away from 28.025 GHz/T × B loses the
  enhancement.
* **Comm faults.** A `silent` gauge flickers COMM: each timed-out poll drops
  it, and the reconnect that follows succeeds, so it reads "Connected" again
  about 30 s later. Watch the channel alarms and LAST_OK instead (known issue
  `comm-flag-tracks-gateway`).
* **Quench.** The fridge and magnet warm, cell pressure rises to atmosphere,
  P collapses, and the safety interlock withholds `Vac_Permit`.

## 3. Unattended regression

    python -m cooldown_sim run --cooldown 2026-06 --start "2026-06-17 18:00" \
        --duration 60 --speed 60 --legs cryo,nmr
    # exit 0 = PASS, 1 = FAIL, 2 = refused by the guard, 3 = setup error

What the report checks (`logs/cooldown_sim/<run>/report.md`):

* every launched process stayed up, and every IOC heartbeat advanced;
* every device PV equals what its emulator sent, to wire precision, through the
  unchanged driver (calibration curves, units, status codes, NaN handling);
* every archived row is a value its PV actually took;
* **read-only proof**: no output write reached an instrument behind a
  `write_enabled: false` IOC;
* every PV the live configuration expects was served;
* the online fitter, after Update Constants (pressed by the fitter autopilot),
  matches the model's P within 1 pp.

Live-Genesis behaviours listed in `cooldown_sim/known_issues.yaml` are
reported as **KNOWN**, not FAIL. Each should get its own live fix.

Opt-in full-stack tests:

    GENESIS_COOLDOWN_SIM=1 GENESIS_REPLAY_DATA=... python -m pytest -q tests/cooldown_sim -m cooldown_sim

The fast tests run with the normal `python -m pytest -q`. They need no EPICS
network and no data.

### Without any past data

    python -m cooldown_sim run --source synthetic --legs cryo,mw,nmr --duration 60
    python -m cooldown_sim run --mode interactive --source synthetic --legs cryo,mw,nmr

The synthetic day is seeded (`--seed N`) and designed:

* microwaves ON at 1:00 at the DNP optimum;
* ssRF burns logged at 4:00;
* microwaves OFF at 5:00.

Add `--faults data_gap,glitches` for record defects. Because the record *is*
the model, the report requires model and record to agree.

### Scripted scenarios

    python -m cooldown_sim scenario list
    python -m cooldown_sim scenario vacuum-trip            # one
    python -m cooldown_sim scenario all                     # each in its own process; summary at the end

A scenario (`cooldown_sim/scenarios/<name>.yaml`) is a regression run with:

* **timed actions** (sim minutes after the start): model inputs, instrument
  faults, channel overrides, autopilot hand-overs, operator caputs, notes.
  They go through the same control channel and restricted operator CA as
  Sim Control and the autopilot.
* **expectations**, added to the report as `scenario:` checks. An expectation
  can test a PV value or alarm severity over a window (`eventually`, or
  `always`), a model truth (`true_P`, ...), or another check's verdict.
  Because the probe records changes only, the value a PV held when the window
  opened counts as a sample.
* optional replay-only **profile overrides**. `limits_replay.yaml` is a
  SIM-ONLY interlock limits file with `enforce: true`, so trips can be
  exercised. Its numbers are **not** recommendations for the cryostat.

| Scenario | Legs | What it exercises |
|---|---|---|
| `nominal` | cryo, mw, nmr | data-free spin-up through the physical RF chain; fitter calibrated by autopilot, tracks truth |
| `operator-deviation` | cryo, mw, nmr | the operator takes the microwaves and turns RF off early; P relaxes instead of spinning up |
| `quench` | cryo, mw, nmr | quench with microwaves on: fridge warms, cell pressure, vacuum trip, P collapses, recovery |
| `vacuum-trip` | cryo | OVC past the enforced trip: latch, coldboard held off, latch survives recovery, operator reset |
| `comm-loss` | cryo | LS224 goes silent, then the cell gauge is unplugged: alarms, COMM, recovery |
| `data-gap` | cryo | 10-minute record gap: readings go invalid once the hold expires, then recover |
| `glitchy-nmr` | cryo, nmr | 20x NMR noise, then a 30° phase error; the fitter keeps running |
| `ssrf-burns` | cryo, ssrf | three logged burns through the real sequencer (Linux/WSL only) |
| `protected-nmr` | cryo, ssrf, nmr | protected PyNMR mode refuses unattested spectra (Linux/WSL only) |
| `real-2026-06` | cryo, mw, nmr | the real 2026-06-17 record with microwaves on at 10:41 (needs `GENESIS_REPLAY_DATA`) |

Scenarios that need Linux or past data are reported SKIPPED where they cannot
run.

## 4. What the target model knows, and what it assumes

The model is **hybrid**. The record supplies every channel's real trajectory.
The model adds only the response to what *you do differently from history*, so
repeating history reproduces the record exactly. Parameters live in
`cooldown_sim/model/model_params.yaml`, each with its basis. Regenerate them
with `python -m cooldown_sim fit-params`.

| Quantity | Source |
|---|---|
| DNP optimum at 28.025 GHz/T × B | calibrated: best P at 138.4 GHz / 4.94 T and 142.3 GHz / 5.08 T |
| P∞ = 49.8 %, τ_up = 2450 s, T1_P = 11 600 s | Spin-1-Rates-Fit (Dec 2025 d-butanol) |
| Q follows Q_B(P) = 2 − √(4 − 3P²), heals in 4 min | **assumed**; keeps TE-start spin-ups on the Boltzmann curve |
| DNP lobe width / negative lobe | **assumed**; the record shows only small positive P below the optimum |
| Microwave heat 62 mW at full drive | **measured**: logbook 2024-12-17, Roots-flow differential |
| Boil-off 59.5 mW/slm | Keith (PSTP 2024); Spin-1-Rates-Fit dec15 thermal audit |
| Cup (T1/T2/T3) ~10 K/W; bath ~2 K/W | cup partly fitted (T3); bath **assumed** |
| Magnet thermometers M2-M4 | fitted from 13 isolated microwave transitions |
| Coldboard heat, quench | **assumed**, for exercising interlocks. Varactor-bias heating from the cold-board supply is **neglected** (none noticed; Ells, 2026-10-15): 0 W unless injected as a test fault |
| NMR line, noise, drift | ported from the McClellan test-cooldown generator (real 2026-06 amplitudes) |

**Fidelity.** Over real days, the model tracks recorded spin-ups and plateaus
to within a few points. It cannot reproduce deliberate fast depolarisations,
such as AFP, anneals or burns not in the blast log.

## 5. Outputs (`logs/cooldown_sim/<UTC>_<mode>_<scenario>/`)

| File | What |
|---|---|
| `report.md` / `report.json` | verdicts |
| `after_action.md` / `.html` | the session timeline: actions, faults, notes, interlock and RF state changes; truth against fitter P |
| `events.csv` | every clock, fault, model, autopilot, note and baseline event |
| `probe.csv` | every watched PV update |
| `served.csv` | every value an emulator sent |
| `nmr.csv` | every sweep: truth against the fitter |
| `channel_map_check.html` | each recorded column overlaid on what Genesis published (for checking the mapping) |
| `archive/` | the archiver's CSVs, alarm log and snapshots |
| `profiles/` | the generated loopback profiles, plus `rewrites.json` (every key changed from the live profiles) |

## 6. Platforms and what is not built yet

| Leg | Windows | Linux/WSL |
|---|---|---|
| Cryo, target model, Sim Control, NMR (LabVIEW-protocol path), Phoebus | yes | yes |
| ssRF devices, simulators, interlock | yes | yes |
| ssRF sequencer + burn replay | skipped (POSIX-only imports) | yes |
| protected PyNMR mode (`--nmr-protected`, needs `requirements-replay.txt`) | skipped | yes: rehearses that unattested spectra are refused |
| Microwave leg (physical MW IOCs, real shim on a simulated brick) | yes | yes |

Open questions about the channel mapping are in
`docs/COOLDOWN_SIM_OPEN_QUESTIONS.md`.
