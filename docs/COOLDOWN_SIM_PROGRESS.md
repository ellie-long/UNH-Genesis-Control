# Simulated cool-downs: progress log

A running record of the `cooldown_sim` build: what is done, what was found,
what is assumed, and what is next. The newest entries are at the top of each
section. The how-to guide is in `docs/COOLDOWN_SIM.md`. Questions waiting for
lab answers are in `docs/COOLDOWN_SIM_OPEN_QUESTIONS.md`.

* **Where the work lives:** branch `dev-sim` in the worktree
  `GitHub/UNH-Genesis-Control-dev-sim`, created from `elong`. Nothing is pushed.
  Merging into `elong` is Ells's call.
* **Ground rule:** the build is purely additive. No existing Genesis file is
  changed. `git diff --name-status elong...dev-sim` shows only added (`A`)
  files.

## Status at a glance

| Phase | Item | Status |
|---|---|---|
| A | Foundations: guard, ports, clock, run dirs | done |
| A | Era-aware past-data readers | done |
| A | Channel map and wire conversions (+ review doc) | done |
| A | Cryo instrument emulators + driver conformance | done |
| A | Generated profiles + drift guard | done |
| A | Unattended cryo regression + validators + report | done |
| B1 | Sim Control IOC + Phoebus screen + `ctl` CLI + interactive mode | done |
| B2 | Closed-loop target model (thermal, DNP, quench, coldboard) | done |
| B3 | Live NMR source into the real online fitter | done |
| C1 | Microwave leg: physical MW IOCs on the real Vaunix shim + simulated brick, UCA emulator, MW observer + autopilot | done |
| C2 | ssRF leg: sim-active IOCs + simulators, blast-log burn autopilot through the real sequencer, burn heat/hole model | done |
| D1 | Synthetic (data-free) cool-downs (seeded generator in memory, faults) | done |
| D2 | Protected PyNMR mode: admission/protection rehearsal (Linux) | done |
| D3 | Scenario library, fault scenarios, after-action report | done |

**The planned build is complete.** What is left is Ells's call:

1. Answer the open questions in `docs/COOLDOWN_SIM_OPEN_QUESTIONS.md`: four
   channel-map guesses, and whether Q lags Q_B(P) on a spin-up from zero.
2. Try an interactive rehearsal (guide section 2) with the real screens in
   Phoebus.
3. Decide whether to push `dev-sim` and merge it into `elong`.
4. Schedule the live-code fixes listed under *Findings* below. Each is a
   separate change to the live system, deliberately not made here.

## Log

### 2026-10-15: Equipment inventory in the channel map
* `docs/COOLDOWN_SIM_CHANNEL_MAP.md` now opens with **Equipment Genesis talks
  to**: every instrument Genesis reads or drives, by its Genesis name, with:
  * make and model, role and link (address/port);
  * PV prefix, and what Genesis may do to it;
  * which channel-map rows feed it, and its sim stand-in;
  * what to check.
* It also lists the hardware with no IOC of its own (the serial gateway, the
  Raspberry Pi, the un-instrumented VDI chain) and the software-only IOCs.
* Source: the `equipment:` and `software_iocs:` sections of
  `cooldown_sim/channel_map.yaml`. A test fails if an IOC in the live IOC
  managers is missing from it.
* From Ells:
  * two Lake Shore 224s for the thermometry;
  * an SPD4306X (4-channel) that may be the cold-board / varactor supply
    (Genesis has no driver for it);
  * a newer single-channel Siglent for the UCA.
  * Open questions 6-7.

### 2026-10-15: Phase D3 (scenarios and after-action reports)
* `python -m cooldown_sim scenario list | NAME ... | all` runs scripted
  scenarios. `all` runs each one in its own process, then prints a summary.
  Linux-only and data-needing scenarios are reported SKIPPED where they cannot
  run.
* A scenario (`cooldown_sim/scenarios/*.yaml`) is a regression run plus:
  * **timed actions:** model inputs, faults, channel overrides, autopilot
    hand-overs, operator caputs and notes. They go through the same control
    channel and restricted operator CA as Sim Control.
  * **expectations:** PV value or severity over a window, model truth
    (including the fitter's error against truth), or another check's verdict.
    A failed action fails the report; nothing is silently skipped.
  * **waivers:** an explicit, reasoned FAIL→INFO for a general check that the
    scenario deliberately drives outside its premise. Used once (`quench`).
* The ten scenarios: nominal, operator-deviation, quench, vacuum-trip,
  comm-loss, data-gap, glitchy-nmr, ssrf-burns, protected-nmr, real-2026-06.
  `limits_replay.yaml` holds SIM-ONLY interlock limits with enforcement on, so
  trips can be exercised. These are **not** recommendations for the cryostat.
* Every session now writes `after_action.md`: a timeline of actions, faults,
  notes and interlock/RF/fitter state changes. With the NMR leg it also writes
  `after_action.html` (truth P against fitter P).
* `ctl override INSTRUMENT CHANNEL VALUE|record` forces a recorded channel
  during a rehearsal.
* Validator fixes:
  * PV expectations count the value held as the window opens (the probe
    records changes only).
  * Archive rows written after the probe stopped are unverifiable rather than
    mismatches.
  * A YAML trap: a bare `on:` key reads as boolean True, so the loader turns it
    back into "on".
* Behaviour the scenarios surfaced:
  * **Q lag (model assumption, open question 5).** After a quench, the model's
    Q re-heals towards Q_B(P) with an assumed 240 s time and lags during the
    re-spin-up. The equal-spin-temperature fitter then reads P about 13 % low.
  * **Weak-line calibration.** Update Constants on a line at about 6 % P (or
    one still changing) mis-calibrates by tens of percent, as it would live:
    P comes from the line asymmetry, which is nearly flat there.
  * **The enforced interlock re-sends `Coldboard_Mode=0` every cycle while
    tripped,** and logs "driven off" each time. The coldboard IOC is read-only,
    so nothing reaches the supply (read-only proof PASS). Live, an "enforced"
    trip therefore cannot actually turn the coldboard off.
  * **A silent gauge flickers COMM** (drop on timeout, back to Connected on
    the reconnect about 30 s later). This refines `comm-flag-tracks-gateway`.
* **Results:** all ten scenarios PASS on WSL. On Windows, nine PASS and
  `protected-nmr` is SKIPPED (Linux only). `ssrf-burns` on Windows passes with
  the burn replay SKIPPED.

### 2026-10-15: Phase D2 (protected PyNMR mode)
* `--nmr-protected` runs the unchanged fitter in protected PyNMR mode:
  `--nmr-protected --nmr-source pynmr`, with the NMR-switch read-back from the
  ssRF leg.
* The simulated DAQ streams spectra to the PyNMR port.
* Protected fitting is only possible for an armed, PINNED PyNMR producer. The
  pin is hardcoded to `/home/utsav/jlab_pynmr` and a specific interpreter, so
  a simulation must not, and cannot, impersonate it.
* What is rehearsed is therefore **the protection itself**. Verified on WSL:
  of 40 unattested spectra, 0 were fitted (`NMR_PROTECTED=1`,
  `NMR_SOURCE_RBV=pynmr`).
* Full protected fitting is reported KNOWN (`pynmr-producer-paths-hardcoded`).
  Also noted: protected mode needs pyepics, which `requirements.txt` omits.
  Added `requirements-replay.txt`.

### 2026-10-15: Phase D1 (synthetic cool-downs)
* `--source synthetic` runs a seeded, designed day with no data files at all:
  * cold and steady at 4.939 T;
  * microwaves ON at the DNP optimum at 1:00, spin-up;
  * three logged ssRF burns at 4:00;
  * microwaves OFF at 5:00, relaxation; end at 7:00.
  * Channels wander with the McClellan generator's real-2026-06 amplitudes.
  * Optional faults: `--faults data_gap,glitches`.
* Chose an in-memory seeded generator over a committed fixture: deterministic,
  no data in git, nothing to keep in sync.
* Data-free full stack (cryo+MW+NMR), with no `GENESIS_REPLAY_DATA`: PASS.
  * The model reproduces the designed record to 0.03 pp.
  * The calibrated fitter matches truth to 0.27 pp.
  * RF ON passed the physical microwave interlocks.
* New report check `model vs record P`: PASS/FAIL on synthetic data; on real
  data it is information (model fidelity).

### 2026-10-15: Phase C2 (ssRF leg)
* The ssRF sim-active IOCs (SMC100A, WaveGen, both switches, interlock,
  sequencer) run against the existing ssRF simulators.
* The ssRF autopilot replays every successful burn in the cool-down's
  `blast_log` through the **unchanged sequencer**:
  * the recipe is derived from the logged burn;
  * an 8192-point FM waveform is staged;
  * then READY -> ARM -> START (BURN ONCE).
  * It writes only the recipe and burn-workflow PVs. Manual RF, ABORT and
    DISARM are excluded.
* **Finding: today's Genesis cannot complete an ssRF burn.**
  * The interlock deliberately withholds every permit "pending RF-isolation
    validation" (commit 0051c98).
  * Verified on WSL: all 4 burns of 2026-06-17 09:21-09:25 were staged,
    readied, armed and started, then ended `CLEANUP_FAILED` at
    `SSRF_CONNECT_PERMIT`.
  * After a failed burn cleanup the sequencer returns to IDLE and can re-arm.
  * After a failed *manual* safe-off (ABORT/DISARM/RF_OFF) it locks until the
    IOC is restarted, because safe-off itself needs a connect permit.
  * Reported as KNOWN (`ssrf-interlock-permits-withheld`,
    `ssrf-safe-off-lockup`).
* Burn physics engages only while the SMC100A RF is really ON:
  * heat 140 mW at -5 dBm, one decade per 10 dB (logbook 2024-12-18);
  * a resonant tensor hole that heals back to the Boltzmann curve (depth and
    rate ASSUMED).
* The sequencer is POSIX-only. On native Windows the ssRF devices,
  simulators and interlock still run, and burn replay is reported SKIPPED.
* **Also found:** the live archiver stamps rows with its own processing time,
  which can lag a PV change by seconds under load. Recorded as
  `archiver-stamps-processing-time`.
* Fixes made along the way:
  * the microwave-only leg now enables the target model;
  * re-validation works across Windows/WSL paths;
  * the probe skips IOCs not launched on the platform.

### 2026-10-15: Phase C1 (microwave leg) and the heat model
* **Ells's input (applied):** the cup thermometers T1/T2/T3 are far more microwave-sensitive
  than the bath, and Roots-flow boil-off measures cold heat at ~60 mW/slm.
  * The model now bookkeeps heat in **watts**:
    * microwaves deposit **62 mW at full drive**, MEASURED in the 2024-12-17 logbook
      (deliberate UCA toggle, 1 slm differential);
    * the conversion is 59.5 mW/slm (Keith, PSTP 2024, as in Spin-1-Rates-Fit
      `holeburn/dec15_thermal.py`);
    * the cup responds at ~10 K/W, fitted from T3 (T3 newly added to the fit);
    * the bath responds at ~2 K/W (assumed);
    * the extra boil-off flow is shown on Sim Control.
  * Logbook values queued for the ssRF leg: burns at -5 dBm deposit ~140 mW
    (2.25 slm), with hole recovery in 8-13 min.
* **C1: microwave leg.**
  * The microwave_sim supervisor fakes all of its safety read-backs, so the
    replay uses the **physical** supervisor, Vaunix IOC and UCA IOC, unchanged
    except for endpoints.
  * The Vaunix IOC talks to the **real Raspberry-Pi shim code**
    (`tools/vaunix_shim`: LabBrick, protocol, control lease, watchdog) running
    on loopback with only the vendor `.so` replaced by a Python simulation.
  * The simulated brick reports hardware (not test) mode, as every emulator
    presents itself as the instrument; its model name is `LMS-SIM-COOLDOWN`.
  * The UCA supply starts at 5 V on, as the operator sets it before microwave
    work.
  * The model **observes the real RF read-backs**: if the interlocks refuse RF
    ON, the target does not polarize.
  * The microwave autopilot replays logged RF on/off, frequency and power
    through operator PVs only.
  * Verified on 2026-06-17 10:41: RFEnable passed the physical interlocks, and
    RF ON was confirmed at 138.418 GHz, +10 dBm, within 2 s.

### 2026-10-15: Phase B complete (B1-B3)
* **B3 (`1e9391e`): live NMR into the real fitter.**
  * The NMR source synthesizes each sweep from the model's P and Q with the
    two-temperature lineshape.
  * It feeds the unchanged `online_fitter.epics_run` on replay ports.
  * Before Update Constants the fitter reads about 12.6 pp low (realistic
    preset bias). After it, the fitter tracks truth to a median of
    **0.08 pp in P** and 0.09 pp in Q.
  * Baselines taken with the line on-window subtract the signal, the real
    operator mistake.
  * A fitter autopilot calibrates during regressions.
* **B2 (`23fd520`): closed-loop target model.**
  * The record supplies the background; the model adds the operator's
    deviation from history.
  * P and Q are separate state variables.
  * The units/limits review was applied: physical-region projection after
    burns, NaN guards, overflow-safe thermal equilibrium, and fast P collapse in
    a quench.
* **B1 (`4446040`): interactive mode.**
  * Sim Control IOC `UNH:GEN:SIM` and the screen.
  * Phoebus 5.0.5 installed in `C:\Users\longe\phoebus`. Its settings file pins
    both of its CA clients (JCA and Jackie) to replay ports. Verified by its
    TCP connections to every replay IOC.

### 2026-10-14/15: Phase A complete
* All 12 real cryo IOCs run against emulators on Windows and WSL.
* Every device PV matches what the emulators served to wire precision; the
  archive is consistent; the read-only proof passes.

## Findings about the live Genesis system (fix later, separately)

Tracked in `cooldown_sim/known_issues.yaml` and reported as KNOWN, not FAIL:

1. **`conn-pv-not-served`:** 21 `_Conn` PVs that `pv_map` and the archiver
   expect are never served, so `Alarm_Worst` stays INVALID.
2. **`comm-flag-tracks-gateway`:** a silent or garbage gauge behind the serial
   gateway flickers COMM. Each timed-out poll drops it, and the reconnect
   succeeds, so it reads Connected again about 30 s later. Only the channel
   alarms and `LAST_OK` reveal the fault reliably.
3. **`stale-watchdog-on-constant-reading`:** a legitimately constant reading
   drops `Data_Permit`.
4. **`vac-permit-not-consumed`:** nothing in ssRF/MW reads
   `CRYO:SAFE:Vac_Permit`, although the docs say the sequencer does.
5. **`ssrf-interlock-permits-withheld`:** the ssRF interlock never grants
   connect/RF permits (deliberate, pending RF-isolation validation), so no
   burn can turn RF on.
6. **`ssrf-safe-off-lockup`:** ABORT/DISARM/RF_OFF safe-off needs an NMR
   connect permit and fails. The sequencer then refuses everything until the
   IOC is restarted.
7. **`archiver-stamps-processing-time`:** archive rows carry the archiver's
   processing time, not the PV timestamp. Under load this lags by seconds.
8. **`pynmr-producer-paths-hardcoded`:** the protected-mode PyNMR producer pin
   is hardcoded to `/home/utsav` paths, so protected fitting can arm on one
   account only. pyepics is also missing from `requirements.txt`.

Also observed:

* The shipped placeholder interlock limits treat F1 as a cold sensor
  (trip at ≥ 4.5 K). In the record F1 sits at about 200-300 K, so with
  enforcement on the cold-board interlock would latch permanently.
* OVC (ColdCathode) and UCA voltage have no recorded data since 2025-11.
* With enforcement on, the safety interlock's only actuator is
  `COLDBOARD:Coldboard_Mode`. The coldboard IOC is `write_enabled: false`, so
  an enforced trip is logged as "driven off" but changes nothing at the
  supply. It also re-sends the write and logs it on every cycle while
  tripped.
* On native Windows the existing test suite has 4 collection errors (POSIX-only
  `pwd`/`fcntl` imports) and 30 failures. These are pre-existing.
* Two existing tests are intermittently flaky even when run alone, unrelated
  to this work:
  * `test_unbound_event_ticket_cannot_fit_arbitrary_tcp_packet`
  * `tools/pcl602_shim/...test_second_client_cannot_control_or_stop_owner`

## Model: fitted vs assumed (details in `cooldown_sim/model/model_params.yaml`)

| Quantity | Status |
|---|---|
| DNP optimum at the free-electron frequency (28.025 GHz/T × B) | calibrated against the record |
| P_max 49.8 %, tau_up 2450 s, T1_P 11 600 s | from Spin-1-Rates-Fit |
| Magnet thermometer gains M2-M4 | fitted (13 isolated microwave transitions) |
| Microwave heat at full drive 62 mW | MEASURED (logbook 2024-12-17, Roots-flow differential) |
| Boil-off 59.5 mW/slm, flow response ~15 s | Keith PSTP 2024 / Spin-1-Rates-Fit dec15 audit |
| Cup (T1/T2/T3) response ~10 K/W | partly fitted (T3 step responses) |
| Bath response ~2 K/W, fridge F-sensors none | assumed / not identifiable |
| Q healing to Q_B(P), DNP lobe shape, coldboard, quench | **assumed** |

**Fidelity:** the model follows recorded spin-ups and plateaus to within a few
points. It does not reproduce deliberate fast depolarisations (AFP, anneals,
unlogged burns).

## Test status (latest)

* `cooldown_sim` fast tests: **167 pass on Windows** (8 skipped: full-stack and
  POSIX-only), and the same on WSL inside the full suite.
* Scenario library: 10/10 PASS on WSL; 9 PASS + 1 SKIPPED on Windows.
* The existing suite gives the same results as before on both platforms:
  * Windows: 4 collection errors and 30 failures, all pre-existing.
  * WSL: under load, more of the timing-sensitive
    `test_microwave_rf_operation` tests fail (their timeouts are 80-120 ms). Run
    alone, only the baseline's `test_calibration_not_a_gate` fails.
  * Plus the two known flaky tests.
