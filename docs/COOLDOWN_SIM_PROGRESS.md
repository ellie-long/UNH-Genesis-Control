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
| C1 | Microwave leg (UCA emulator, MW IOCs, MW autopilot) | next |
| C2 | ssRF leg (simulators, burn model, blast-log autopilot; Linux) | planned |
| D1 | Synthetic (data-free) cool-downs + committed fixture | planned |
| D2 | Protected PyNMR leg (Linux) | planned |
| D3 | Scenario library, fault scenarios, after-action report | planned |

## Log

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
| Cup/fridge microwave thermal response | **assumed** (the record does not constrain it) |
| Q healing to Q_B(P), DNP lobe shape, coldboard, quench | **assumed** |

**Fidelity:** the model follows recorded spin-ups and plateaus to within a few
points. It does not reproduce deliberate fast depolarisations (AFP, anneals,
unlogged burns).

## Test status (latest)

* `cooldown_sim` tests: 126 pass on WSL. 123 pass on Windows with 3 opt-in
  skips by default. Opt-in full-stack runs pass on both.
* The existing suite gives the same results as before (aside from the two
  flaky tests).
