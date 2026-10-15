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
| D1 | Synthetic (data-free) cool-downs + committed fixture | next |
| D2 | Protected PyNMR leg (Linux) | planned |
| D3 | Scenario library, fault scenarios, after-action report | planned |

## Log

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
   gateway shows COMM = Connected again one poll later. Only the channel
   alarms and `LAST_OK` reveal it.
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

Also observed:

* The shipped placeholder interlock limits treat F1 as a cold sensor
  (trip at ≥ 4.5 K). In the record F1 sits at about 200-300 K, so with
  enforcement on the cold-board interlock would latch permanently.
* OVC (ColdCathode) and UCA voltage have no recorded data since 2025-11.
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

* `cooldown_sim` tests: **143 pass on WSL**, including 5 full-stack runs
  (cryo, guard, cryo+NMR, cryo+MW, cryo+ssRF).
* On Windows, 137 fast tests pass. The 4 full-stack runs pass and ssRF is
  skipped (POSIX-only sequencer).
* The existing suite gives the same results as before (aside from the two
  flaky tests).
