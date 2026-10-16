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

1. Answer the open questions in `docs/COOLDOWN_SIM_OPEN_QUESTIONS.md`: the
   vacuum interlock's gauge, the UCA
   power curve (the model's microwave drive), the field during a quench, and
   whether Q lags Q_B(P) on a spin-up from zero.
2. Try an interactive rehearsal (guide section 2) with the real screens in
   Phoebus.
3. Decide whether to push `dev-sim` and merge it into `elong`.
4. Schedule the live-code fixes listed under *Findings* below. Each is a
   separate change to the live system, deliberately not made here.

## Log

### 2026-10-16: Roots gauge and shells-vacuum alarm limits
* LabVIEW only read the top Roots gauge, so "Roots Pressure (Torr)" is TPG 262
  gauge 1 (question 3, second half, settled; the sim already assumed it).
* **Finding (live system): the shells-vacuum alarm needs a temperature-
  dependent limit.** Ells: the IONIVAC is the vacuum of the fridge's shells
  and very important to track. Alarm above 1e-4 Torr while the bottom of the
  magnet is above 300 K, above 1e-5 Torr once it is below 80 K. Genesis's
  `ION:Ion_PI` has static HIGH/HIHI 800/900 Torr, and static limits cannot
  follow the magnet temperature: this needs a small logic step (pick the limit
  from `M5_TI`, then alarm). Also `Ion_PI` has PREC 4, so a widget not in
  exponential format shows 1e-5 Torr as 0.0000. Open: 80-300 K, M5 as the
  sensor, severity (question 39); rise rate (25).
* Manometer-Lo is an XCG-HT-FB-1, 10 Torr full scale (question 13 settled).
  The record confirms the old DAQ's 5 Torr Hi-to-Lo crossover. Found: a VP
  temperature of exactly 0.650 K is a Manometer-Lo dropout (it read 0); up to
  ~100 rows per cool-down, which the model took as a 0.65 K bath. Fixed.
* Question 10: Ells has ssRF heat data from the 2026-06 prep to analyze.

### 2026-10-16: pre-demo screen check
* All four demo screens rendered offscreen against a 2026-06-17 replay: Sim
  Control 74/74 PVs, NMR fitter 38/38, Panopticon 72/73 (SSRF:SEQ:STATE is
  POSIX-only), Microwave 52/56 (the IOC-manager heartbeats; the sim runs no IOC
  manager). Magic + set the supervisor to 138.4204 GHz = 28.025 x 4.93918 T.
* Fixed: the Microwave screen is all `pva://`, and the sim's Phoebus ini only
  configured CA, so it showed disconnected; the ini now names every IOC's PVA
  server. Sim Control's End session / QUENCH confirmations were never shown
  (`confirm_dialog` is not a Phoebus property; fixed on `dev-visualizer`).
* Seen on the live NMR fitter screen (a Genesis file, not changed here): its x
  axis is fixed at 32.25-33.15 MHz, while 2026-06's line sits at 32.08-32.48,
  so the left of the signal is cut off; the incoming-NMR trace draws a line back
  through the zero-padded tail of the waveform; a label overlaps; `grid_color`
  is not an xyplot property.

### 2026-10-15: Panopticon checked against the 2026-06-17 23:00 screenshot
* Built on `dev-visualizer`; replay values match the screenshot (see the
  changelog). Flags from that work, for the sim:
  * **Online fitter at 600x:** P_FIT/Q_FIT stuck at 0 (fits running, chi2
    frozen) for hours of sim time, after swinging -62..+36 %; fine at 10x.
    To investigate (sweep pacing or a fitter state problem at high speed).
  * **UCA at 23:00:** the sim shows 0 V (logbook timeline: power on since
    14:57); the LabVIEW screenshot shows "5.0" in the DAQ's UCA box, but that
    control was not the real UCA then (manual supply), and P was 50 %, so the
    sim is right. "RF permissive: Blocked" with RF ON is Genesis's meaning:
    permission to turn RF ON needs the UCA at 5 V.
  * Target material reads "Empty" and the dopant "UNCONFIGURED": the sim
    could set Genesis's `CRYO:STATE:material` / `MW:SUP:Dopant` from the
    replayed cool-down's material (to do).
  * `OVC_PI` (bottom Roots manometer) NaN: the vacuum rework (question 20).

### 2026-10-15: NMR window on Sim Control; drive fix merged; first live demo
* Sim Control: **NMR centre** and **NMR span** inputs (MHz; 0 = follow the
  record) and readbacks of the window in use; the window logic moved into
  the target model (`nmr_window`). The input panel is now three columns and
  also shows the UCA voltage input. Asked for by Ells while watching a run.
* Merged the drive fix (`ab300e9`): UCA-based drive, replay autopilot follows
  the UCA procedure. Its RF-flip re-fit was NOT merged: Ells says RF goes on
  at UCA 5 V and power goes to 0 V by hand later, recorded only in the
  logbooks, so RF flips are not power steps. A logbook-based UCA timeline and
  re-fit are being built separately; current thermal parameters kept.
* `quench` scenario: quench moved to 18 min (was 15) so the fitter calibrates
  first; passes in WSL.
* First visible sessions for Ells: Windows (Phoebus already installed), then
  WSL with Java 21 + GTK3 + Linux Phoebus 5.0.5 (WSLg).

### 2026-10-15: T1/T3 read by Genesis (dev-sim)
* **Genesis change on dev-sim (Ells's choice; elong untouched):**
  `target_ioc/instruments/ls224.py` evaluates Lake Shore Cernox Chebyshev
  curves (four ranges, resistance boundaries); `calibration/magnet_thermometry.csv`
  now uses the lab's full column layout and carries stick 2's T1 (C2) and T3
  (C4); `ls224_magnet` reads T1/T3; the archiver and viewer filters include
  them. M1-M5 and F1-F11 unchanged.
* **Found:** the lab's calibration list has T1's range-2 ZU/ZL transposed.
  Corrected in the CSV; the loader also swaps a reversed pair (with a
  warning). Verified: boundaries continuous within 0.3-7 mK, T falls
  monotonically 0.26-330 K. Question 30: did LabVIEW use the bad values?
* Sim: T1/T3 are `ls224_magnet` channels fed from the record; the emulator
  inverts the Cernox curves. Follow-up after the drive-fix merge: T1/T3
  readings should follow the model's cup temperature (now they follow the
  record plus the quench warm-up).
* Tests: 170 passed, 8 skipped (cooldown_sim); 45 passed (LS224/archiver
  tests elsewhere). Phoebus screens not yet updated for T1/T3.

### 2026-10-15: Target thermometer calibrations
* The lab has Cernox (CCX) calibrations for the target thermometers of each
  target-stick version (Ells). Genesis's Lake Shore driver supports only the
  CCS/CCCS equations and treats CCX rows as placeholders, so T1/T3 cannot be
  read yet (question 29). Genesis's calibration file matches stick 2 (no T2);
  which stick is in use is question 15.

### 2026-10-15: Record instruments Genesis lacks
* Listed every record column with no Genesis IOC. Added to the channel map's
  "In the past record, not in Genesis": lab thermocouples, microwave
  sweep/hop settings, EPR waveform generator, lock-in (new aliases in
  `data/aliases.py`). Priorities from Ells: magnet supply, then AMI level
  meters; SIG readout waits for a voltage-reading device on order;
  thermocouples low; EPR generator and lock-in extremely low.
* Genesis microwave sweeping: the supervisor has a one-pass stepped software
  sweep; the Vaunix IOC exposes hardware repeat/triangle sweep settings but
  does not authorize starting one; no hop. Whether LabVIEW's Sweep/Hop were
  ever used is question 28.

### 2026-10-15: UCA power curve; drive fix started
* Vendor test data for the current microwave chain (after its 2025-10
  repair) give the UCA curve and output vs frequency (question 9). The
  older (2018) calibration has a very different shape, so only the current
  curve is used. Absolute scale stays the measured 62 mW at the helium,
  which already includes the waveguide and mirror losses.
* Drive fix (question 19) under way on a side branch: history = RF on/off
  (saturated at Vaunix >= -5 dB, full power); live = also the UCA read-back
  through the vendor curve; the replay autopilot lowers the UCA after RF ON
  and restores 5 V before RF OFF; then a re-fit.
* **Finding (live system): an unplugged UCA cable means full power.** The
  chain's UCA input defaults to full power when unconnected, but the RF
  permissive checks only the SPD1305X's 5 V read-back (question 27).

### 2026-10-15: Answers on saturation, shells alarm, instrument priority
* Microwave chain saturated from about -5 dB on the Vaunix, so the
  setpoint does not lower the drive in 2022 to 2025-07 (question 24 settled;
  supports the drive fix, question 19). Newer power data are coming (9).
* **Finding (live system): no shells-vacuum alarm.** The IONIVAC alarms at
  800 / 900 Torr. Ells: warn on a rapid rise, particularly above the 1e-4 Torr
  range (question 25: rate still to set).
* Instrument priority for Genesis: magnet power supply, then the AMI level
  meters, then the backing-pump thermocouples (question 26 settled).
* The AFP and ssRF lines are identical, so the LZY-22+ is in the ssRF line
  (question 10, still parked).

### 2026-10-15: UCA history, SIG, upcoming DAQ instruments
* Ells:
  * the `UCA (V)` column has not been the real UCA for years (analog-output
    drive had problems; a manual DC supply drove it; the SPD1305X took over in
    the last few weeks). Question 9 rewritten: the record has only RF on/off,
    with a proposed drive fix;
  * SIG is the microwave chain's power monitor (lower voltage = more power).
    Added to the channel map as record-only (`sig_v` alias);
  * the KJL-SPARC speaks a TPG-style serial protocol;
  * the two AMI helium-level meters and the flow meters are needed on the
    Genesis DAQ soon. When they get IOCs, the sim will need emulators and
    channel-map rows for them;
  * the NMR RF source (SMT03) and the VME crate are being replaced by the JLab
    NMR, so they are not worth modelling.

### 2026-10-15: Cold-cathode gauge identified
* Ells: the cold-cathode gauge was an older back-up to the IONIVAC (shells'
  vacuum). The device that read it is broken and disconnected, which is why
  its column is empty from 2025-11. Channel map and question 3 updated; for
  the vacuum rework, its history is a fallback for the IONIVAC's.

### 2026-10-15: TPG 262 is on the Roots pump; Manometer-Lo head
* Ells: both TPG 262 gauges are manometers on the Roots pump (gauge 1 `Pump`
  = top, gauge 2 `OVC` = bottom). The IONIVAC is the shells' insulating
  vacuum. Manometer-Lo is a KJLC XCG carbon capacitance manometer, cabled to
  the KJL-SPARC by an RJ45-style (non-Ethernet) cable.
* **Finding (live system): the vacuum interlock watches a Roots-line gauge.**
  `limits_unh.yaml` trips at `OVC_PI` >= 1e-3 mbar, but that gauge reads
  roughly 0.1-30 Torr while pumping, so with enforcement on it would trip
  constantly. Nothing in the interlock watches the IONIVAC (the real
  insulating vacuum). Live fix, not made here.
* **Sim (not yet changed):** `tpg262.OVC` is still fed from the cold-cathode
  column, and the model's quench vacuum rise, the sim limits and the
  `vacuum-trip` scenario act on it. These should move to the IONIVAC
  together, once Ells decides what the live interlock should watch (open
  question 3). Notes in `channel_map.yaml` mark the mapping as wrong; the
  cold-cathode gauge is now listed as record-only.

### 2026-10-15: The lab's Raspberry Pis
* Ells: the KJL-SPARC (Manometer-Lo) is on the USB of the Pi labelled
  192.168.1.24. Per `docs/SSRF_PHYSICAL_COMMISSIONING.md` that is the
  `keysight` Pi, which runs the Genesis Keysight WaveGen IOC. So one Pi
  serves both, and a future Manometer-Lo IOC would naturally live there.
* The second Pi (no IP label) handles the microwaves: the robot, possibly
  the Vaunix. Genesis configs put both the Vaunix and PCL602 shims on
  `mmWaveRobot.local`, so it is both.
* Channel map: new `(keysight Pi)` row, the Keysight and mmWaveRobot rows
  annotated, the Manometer-Lo note updated; map regenerated.

### 2026-10-15: Instrument IPs handled in the lab
* Ells: the lab is fixing the post-outage IPs (static addresses, then reboot
  the equipment). Question 6 closed here; nothing to track in the sim.

### 2026-10-15: Manometer-Lo readout
* Ells: the KJL-SPARC controller for Manometer-Lo is connected by USB to a
  Raspberry Pi. Recorded in `channel_map.yaml` (map regenerated). Still to
  find out: which Pi, and how the reading reaches LabVIEW. Genesis has no IOC
  for it.

### 2026-10-15: Cold-board supply is an SPD1305X
* Ells: the varactors are powered by an SPD1305X with a sticker reading IP
  192.168.1.5, the `siglent_coldboard` IOC's address. The existing
  single-channel driver fits, so the feared SPD4306X gap does not exist.
* Sim: the coldboard emulator now reports `SPD1305X` (`stacks.yaml`);
  `channel_map.yaml` updated and the map regenerated.
* Question 6 now covers checking the IPs after a lab power outage.

### 2026-10-15: UCA supply confirmed SPD1305X
* Ells confirmed the UCA supply is an SPD1305X: it matches the live IOC and
  the sim's emulator. `mw_spd1305x` is now high confidence; question 6 is
  down to the cold-board supply.

### 2026-10-15: Equipment answers; microwave drive and F1 findings
* Ells, recorded in `channel_map.yaml` (map regenerated):
  * IONIVAC = the vacuum of the shells between fridge and magnet spaces;
  * `MMwaves Power (dB)` = the Vaunix setpoint, default 10 dBm, and the UCA
    sets the actual power;
  * serial gateway possibly a Raspberry Pi (uncertain);
  * T1-T3 probably on the magnet LS224 (`magnet_thermometry.csv` agrees: T1
    on C2, T3 on C4; T2 not listed). Genesis reads only M1-M5 there.
* **Finding (model): microwave drive is built on the wrong column.** See open
  question 9. Not fixed yet: it needs the UCA curve, then a re-fit
  (`fit-params`).
* **Finding (live system): no protection against over-flooding the fridge.**
  Ells: F1 reaching 4.5 K means the fridge is over-flooded and liquid helium,
  not gas, may go into the Roots pumps. (While polarizing F1 normally reads
  about 100-280 K; F11, about 1.0-2.6 K, is the cold sensor.) In Genesis:
  * the interlock rule on `F1_TI` uses the right number (4.5 K) the wrong
    way round. `judge_band` trips only when a value is >= the limit, so it
    would trip on every normal F1 reading and latch at once with enforcement
    on, and never on an actual over-flood. The interlock has no low-trip
    rule at all;
  * the rule acts on the cold board, which has nothing to do with flooding
    (and `pid_coldboard`, cited in the file's comment, exists nowhere in
    `configs/`);
  * F1's own alarm limits (`ls224_fridge`: LOW 0.05 K, LOLO 0 K) never fire
    on an over-flood either;
  * Genesis has no fill-valve actuator, so the realistic protection is an
    operator alarm: F1_TI LOW/LOLO near 5 K / 4.5 K (values Ells's call), and
    F1 taken out of the cold-board rule.

  Live fix, not made here.
* New private folders `ref_technotes/` and `ref_equipment/` (Ells's reference
  material). Each has its own `.gitignore` of `*`, and both are in the
  repository's `.git/info/exclude`, so they can never be committed or pushed.

### 2026-10-15: No NMR during a quench (open question 5, new question 8)
* Ells: in a quench P and Q cannot be measured. The field crashes, the Larmor
  frequency runs away from nominal and the signal leaves the NMR window.
* Gap found: the sim's `quench` is thermal only and holds B at the recorded
  field, so the line stays on the window and the fitter keeps reporting P.
  The `quench` scenario even expects that (`fitter keeps fitting through the
  recovery`). Recorded as open question 8, with the numbers needed (field-dump
  time, re-ramp rate, starting P after the re-ramp). No model change yet.
* Question 5's core (does Q lag Q_B(P) as P rebuilds after the field returns)
  is still open.

### 2026-10-15: Coldboard supply has no record (open question 4)
* Ells: before Genesis the cold board ran from an external DC supply, never
  recorded. It is set between 0 and 19 V. It biases the varactors, so it moves
  the NMR Q-curve's tune (higher voltage, higher frequency).
* `channel_map.yaml`: `siglent_coldboard.Coldboard` is now high confidence,
  with the role and history in its notes. The equipment row's role is now
  "varactor bias". `COOLDOWN_SIM_CHANNEL_MAP.md` regenerated (coverage
  unchanged).
* Not yet modelled: the NMR source's Q-curve stays put whatever the coldboard
  voltage is.

### 2026-10-15: Coldboard heating neglected (open question 7)
* Ells: no noticeable heating from the varactor bias voltages and currents,
  so the model ignores it for now.
* The model already defaulted `coldboard_heat_W` to 0 W, so behaviour is
  unchanged. The decision is now recorded where a later change would start:
  * the coldboard `basis` in `fit_params.py` / `model_params.yaml`;
  * the `target.py` docstring;
  * the model table in `docs/COOLDOWN_SIM.md`.
* To add it later: drive `coldboard_heat_W` from the supply's V x I
  read-backs.
* Question 7 moved to *Settled*. The 120 W heater-style interlock rule is still
  a question for the live system, tied to question 6.

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

* The IONIVAC (the shells' vacuum) alarms only at 800 / 900 Torr, so a loss
  of the shells' vacuum raises no alarm. Ells: warn on a rapid rise and above
  the 1e-4 Torr range (open question 25).
* The vacuum interlock's "OVC" (`OVC_PI`, TPG 262 gauge 2) is the bottom
  manometer on the Roots pump, not an insulating vacuum, so its 1e-3 mbar
  trip would fire constantly with enforcement on. The shells' insulating
  vacuum is on the IONIVAC, which the interlock does not watch.
* The shipped placeholder interlock trips the cold board when F1 ≥ 4.5 K.
  The 4.5 K is right but the direction is wrong: F1 at or below 4.5 K means
  the fridge is over-flooded and liquid helium may reach the Roots pumps
  (Ells). F1 normally sits at about 100-300 K, so with enforcement on the
  rule latches permanently, and nothing in Genesis (interlock or F1's alarm
  limits) catches an over-flood. See the 2026-10-15 log entry.
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
| Varactor-bias heating from the cold-board supply | **neglected** (none noticed; Ells 2026-10-15) |
| Microwave drive | **wrong input**: built on the Vaunix setpoint, not the UCA (open question 9) |
| Field collapse in a quench | **not modelled**: B held at the record, so the NMR line wrongly stays on the window (open question 8) |

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
