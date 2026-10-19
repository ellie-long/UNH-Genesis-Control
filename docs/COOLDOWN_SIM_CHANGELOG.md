# Simulated cool-down: changelog

What changed, newest first, one entry per change on branch `dev-sim`.
Each entry gives the commit, what changed, and why. The narrative and the
findings are in `COOLDOWN_SIM_PROGRESS.md`; questions for the lab are in
`COOLDOWN_SIM_OPEN_QUESTIONS.md`.

## 2026-10-19

* Merged Utsav's `main` (master archiver, dual-source NMR, restart recovery;
  11 commits). Clean merge; his master-archiver tests pinned the Cryo PV count
  (136, total 337), now 142 / 343 with the target-cup T1/T3 PVs (TI, RI,
  Status) dev-sim archives. All his tests pass in WSL (they need Linux:
  `fcntl`); sim suite 228 passed.

## 2026-10-17

* Hindcast `--truth mcclellan`: scores the model against the McClellan
  equilibrium line fits (`cooldown_sim/data/mcclellan.py`) instead of the
  record's online P (miscalibrated in 2024-09/2024-11). Irradiated d-butanol
  rate model, free-run MAE: 2025-12 3.3, 2026-06 3.1 pp (online-P scoring said
  6.9 / 4.8). 2024-09 switched to the rate model (25.6 -> 11.8 pp, bias -23 ->
  -0.4); 2024-11, 2025-02, 2025-11 keep their fitted DNP (better there).

## 2026-10-16

* NMR / ssRF switch meanings and Ells's sequencing rule recorded (NMR off before
  ssRF on; ssRF off before NMR on); question 59: the ssRF interlock logic treats
  NMR PORT2 as PyNMR live, against Ells's "SETP=3 = NMR OFF".


* Channel map: NMR switch 192.168.1.8 (was mis-copied as .18) and ssRF switch
  192.168.1.11, both confirmed (question 58 settled).


* Cold-board supply (`siglent_coldboard`, SPD1305X) now at 192.168.1.18 (was
  192.168.1.5; Ells): config, driver docstring and channel map updated.


* IONIVAC port fixed (question 48 settled): the shells' IONIVAC is on TXI040 :5006;
  :5005 is the portable pumping-station cart's IONIVAC, which Genesis had been
  reading on every branch. `configs/target_physical/ionivac` -> 5006 (dev-sim;
  the live system needs the same change).


* Confirmed (Ells): LS224 #2 at 192.168.1.13 (`ls224_magnet`) carries both the
  magnet (M1-M5) and the target thermometry (T1, T3); channel map updated.


* Vacuum rework (question 20): TPG gauge 2 (`OVC_PI`, bottom Roots gauge) is no
  longer fed from the record (never logged; reads invalid in the sim, live
  unchanged); the IONIVAC carries the shells' vacuum (cold-cathode history as
  fallback) and the quench's ASSUMED vacuum rise (`shells_pressure`,
  question 57). `vacuum-trip` is now a shells-vacuum loss on the IONIVAC:
  alarm expected (KNOWN: live limits 800/900 Torr, new known issue
  `ionivac-alarm-limits`), interlock must not act. Expectations can name a
  known issue (`known:`). Windows offset run: vacuum-trip PASS,
  operator-microwaves PASS, quench only the Windows fitter-timing check.


* Ells's Magic +/- constants (question 34 settled): sim materials (new `nd3`)
  and the Genesis supervisor's dopants, per dopant and material
  (`configured_magic(..., material)`, supervisor reads `CRYO:STATE:material`).
  gamma_d_over_2pi still unset, so Magic stays refused until question 36.
  operator-microwaves P-down step now at Magic - (138.51 GHz).


* Target dopant beside the material on the Cryo Safety screen's Target
  Operating State card: a new cryo record `UNH:GEN:CRYO:STATE:dopant` (None /
  Irradiated / TEMPO / Trityl, saved by name); the microwave supervisor
  follows it (`follow_dopant_pv`), so cryo screens still never write a
  microwave PV (the cryo GUI ownership guard). The Microwave screen's Dopant
  combo stays, for the microwave side.


* Genesis (Ells's request): Target material drop-down on the Cryo Safety
  screen with Empty / ND3 / d-butanol / d-propanediol / NH3 (state table:
  the two new materials copy ND3's entries; selection saved and restored by
  NAME so the reordered menu restores correctly). Dopant drop-down on the
  Microwave screen with None / Irradiated / TEMPO / Trityl (supervisor dopant
  list; no Magic constants yet). The read-only guard test now allows the
  material combo only; tests added.


* Irradiated d-butanol now uses its own MEASURED microwave lineshape, from
  the 2024-09-18 slow sweep (beta peak 28.023 GHz/T, x_opt 28.021 -> 28.023;
  alpha on its plateau at the sweep end, extended to 28.060 as an assumption).
  Hindcast: 2025-12 MAE 7.05 -> 6.89, 2026-06 4.77 -> 4.82 pp. Magic - for
  the material 28.077 -> 28.0405 (2024-09 logbook, on the measured plateau).
  `spin1.py`: per-material lineshapes and separate low/high tapers.
  operator-microwaves scenario: P-down step at Magic - (138.49 GHz).
  `scenario --port-offset` added (run beside a live session). Questions 51-53.


* Serial servers mapped (question 45 settled): every TXI040 and TXI017 port.
  Finding: Genesis's IONIVAC connects to :5005, which the lab technote assigns
  to a "Pumping Station"; the Ionivac is on :5006 (question 48). New 49, 50.


* Question 46 settled: 2024-11, 2025-02, 2025-11 were irradiated d-butanol;
  schedule relabelled.


* Question 3 settled: the IONIVAC alarm is enough (no interlock action).
  Question 20 rephrased: the sim vacuum rework is unblocked, awaiting go-ahead.


* Questions file: checklist now holds only what is still open; answered
  parts moved to Settled (5, 35, 40, and the answered halves of 3, 10, 25,
  34). 8 rephrased; new 46 (was the 2024-11 / 2025 d-butanol irradiated?).
* Material schedule for every cool-down from the technote; `dnp: fitted`
  labels a material without switching the model (the Dec 2025 rates and the
  trityl block hindcast worse elsewhere, 2026-10-16 trial).


* Questions 33 (materials per cool-down, from the cool-downs technote) and 38
  (fridge built from the JLab g2p design) settled; 35: 2024-09 rates fit
  started, 2025-02-13 passed; 37 low priority; 34: what Magic +/- mean,
  numbers to come. 41 settled: the TPG 262 is always in mbar.

* Question 28 settled: Sweep/Hop extremely rarely used (very low priority); the
  slow sweep (set df per set time) is mid priority -- Genesis has it as the
  supervisor's step-per-dwell software sweep (gaps noted).
* Channel map: the serial gateways are DIE WU serial device servers, a TXI040
  8-port at 192.168.1.251 (7 devices; Genesis's 4 RS-232 instruments) and a
  TXI017 4-port at 192.168.1.250 (2 devices, none in Genesis). Question 14
  settled; new question 45 (the 5 unidentified devices).
* Channel map: LabVIEW's "Roots Pressure (Torr)" is the top Roots gauge
  (TPG 262 gauge 1), now high confidence; question 3's second half settled.
* Shells-vacuum (IONIVAC) alarm limits recorded: 1e-4 Torr with the magnet
  bottom > 300 K, 1e-5 Torr below 80 K (questions 25, new 39; channel map).
* XCG Manometer-Lo range recorded (10 Torr F.S.; question 13 settled); the
  5 Torr Hi/Lo crossover confirmed in the record. Target model: a LabVIEW
  vapor-pressure temperature of exactly 0.650 K (Manometer-Lo dropout) is no
  longer taken as the bath temperature (falls back to T1); test added.
* Question 10 (ssRF heat): reminder that Ells has data from the 2026-06 prep
  to analyze and send.
* Checked Ells's 4He `t90_torr_to_k` against the record: Med/Hi/VHi agree to
  < 0.1 mK; LabVIEW's Lo range is ~1.6 mK colder (question 40). Ells: trust
  the offline code over the online readout. So `physics.he4_t90_K` /
  `he4_p90_torr` port it (ITS-90 above 1.25 K, Donnelly-Barenghi below, no
  scipy), the model's bath temperature is now T90 from the manometers
  (Manometer-Hi at >= 5 Torr, else Manometer-Lo; LabVIEW's vp_temp only as a
  fallback), and the simulated cell pressure scales with the exact T90 curve
  instead of the ln P = a - b/T approximation. Tests added.

* Phoebus ini also names each IOC's PVA server
  (`org.phoebus.pv.pva/epics_pva_name_servers`), so `pva://` screens such as
  Microwave connect in a sim session instead of showing disconnected.

## 2026-10-15

* Branch `dev-visualizer` `0f90686`: Panopticon insert -- heat exchanger as two
  stacks from the fridge blueprints (5 upper plates, 10 lower, spacing ratio
  2.6), the separator as a large can between them with F5 just above and F6
  just below, M1-M5 on the dewar's right side at their LabVIEW depths (M1
  ~36", M2 31", M3 17", M4 13", M5 3.5").
* `21ab5c2` Trityl d-propanediol values fitted to the 2020-09 and 2020-12
  records together (P_max 39 %, tau_up 1565 s, positive optimum ~28.033 GHz/T,
  narrow lobes, deep negative lobe). Hindcast 2020-09 MAE 9.35 pp (median
  3.66), 2020-12 9.29 (median 2.41).
* Branch `dev-visualizer` `e2afc74`: Panopticon centre redrawn after the lab's
  fridge thermometry diagram (head and pump-out line, baffled column,
  separator, nose and target cup, sensor markers with leader lines to
  readouts, magnet cryostat around it); log-axis trends fixed.
* `3091ac7` Fitter autopilot presses Update Constants only once the target is
  polarized (true P >= 10 %) as well as after 40 sweeps -- at high speed from
  a cold start it had calibrated on a near-zero line and the fitted P stuck at
  0 (found by the Panopticon check). Scenario library 11/11 PASS in WSL.
* Branch `dev-visualizer` (`128271f`, `4eb198a`; worktree
  `../UNH-Genesis-Control-dev-visualizer`): **Panopticon** overview screen
  (`gui/genesis_panopticon.bob`, generator `tools/make_panopticon.py`,
  `python -m cooldown_sim phoebus --screens panopticon`), modelled on the
  LabVIEW overview. 73 live PVs, 31 "not in Genesis yet" placeholders.
  Replayed to 2026-06-17 23:00 it matches the LabVIEW screenshot within the
  record's own noise (F, T, M thermometry, IONIVAC, manometer Hi, Roots,
  microwaves, fringe field, P/Q). Not merged into dev-sim.
* `ac09735` **ssRF burn effect calibrated** to the 2026-06 blast logs against
  the record's P: a resonant burn removes 0.12 x (1 - exp(-0.14 x scale x s))
  of P (and that fraction of Q), scale = 10^((dBm+5)/10) -- e.g. ~7 % of P for
  a 1 s +3 dBm burn (was ~2.7 %). The hindcast now applies the logged burns
  through the same function as the sim's ssRF leg. 2026-06 hindcast: MAE
  5.66 -> 4.77 pp, median 2.55 -> 2.14, bias ~0. Scenario library: 11/11 PASS
  in WSL (before this change).
* `15d4f3c` **Trityl d-propanediol** (2020-09; 2020-12 until 12-09): its own
  phenomenological DNP values (narrow line: P-up below f/B ~ 28.034, P-down
  above), read off the trityl scan in Spin-1-Rates-Fit. Hindcast: 2020-09
  MAE 15.7 -> 10.3 pp (median 13.7 -> 4.5), 2020-12 14.6 -> 10.7 (median
  4.8 -> 3.0). Materials may now carry a `phenomenological` block.
* `ee43e6f` The target model loads the replayed cool-down's fitted DNP values
  itself (they were applied only when passed in explicitly).
* Panopticon overview screen: being built on branch `dev-visualizer`
  (worktree `../UNH-Genesis-Control-dev-visualizer`), modelled on the LabVIEW
  overview screen.
* `94ad496` **Magic + / Magic -** buttons on Sim Control (and `ctl magic +|-`):
  the DAQ's quick tune, setting the microwave frequency to the best P-up /
  P-down frequency at the present field through Genesis's supervisor
  (`SUP:OutputFrequency`), as an operator; pressing one hands the microwaves
  to Human. Magic + = 28.025 GHz/T x B (the LabVIEW DAQ's); Magic - = 28.077
  (measured lineshape) until the DAQ's constant is known.
* `efc4481` Scenario `operator-microwaves`: microwaves run
  by hand through Genesis on the real 2026-06 record -- RF ON at UCA 5 V (no
  pumping), UCA 0 V (P to ~37 % in 42 min), UCA 5 V (P holds), P-down side (P
  reverses to -28 %), Q on the Boltzmann curve throughout. Per-cool-down DNP
  fits written (`cooldown_dnp.yaml`; degenerate fits dropped).
* `bbd688e` **Spin-1 rate-equation DNP** from Spin-1-Rates-Fit
  (`spin1fit`, imported from the checkout next to this repo): P, Q and the
  electron polarization from fitted rates per material
  (`spin1_materials.yaml`: irradiated d-butanol = Dec 2025 P&Q fit, TEMPO
  d-butanol = 2020 stage 5), frequency dependence from the measured TEMPO
  alpha/beta lineshape (Ells: good enough for d-butanol for now), every
  microwave rate scaled by the delivered power (UCA). Thermal targets use the
  bath temperature, as in the fits. Used for cool-downs with a known material
  (2025-12, 2026-06, 2020-12 from 12-10); others keep the phenomenological
  model with fitted values. Hindcast median error: 2025-12 3.4 -> 2.2 pp,
  2026-06 3.6 -> 2.6 pp. HTML hindcast report (`hindcast --html`).
* `9627b44` **Hindcast** (`python -m cooldown_sim hindcast`): runs the target
  model straight through a past cool-down (no IOCs) and scores its P against
  the record's online P, free-running and re-anchored every 6 h. Record P is
  despiked first (isolated DAQ glitches). **Per-cool-down DNP values**
  (`cooldown_sim/model/cooldown_dnp.yaml`, `python -m cooldown_sim fit-dnp`),
  used by hindcasts and by real-data sim runs: P_max, spin-up and
  relaxation times, optimum frequency offset, lobe width, and the negative
  lobe's side and depth (new options). **Field from the supply current**
  (x 0.10274 T/A, exactly what the DAQ used) where the record has no field
  column (2020-21). **Fast polarization loss at low field** (assumed 2 min
  below 0.5 T, e.g. after a ramp-down).
* `6e466e3` UCA timeline: no microwave power in the 2024-09-18 morning RF
  period (the chain was not responding to the DAQ).
* `13f86ce` Docs: the logbook UCA history and the UCA-step thermal fit.
* `5f2b03a` Synthetic day lowers the UCA 5 min after RF ON, so synthetic
  replays follow the same procedure as real ones.
* `76aab22` **Re-fit** of the thermal response at logbook UCA steps (24 steps:
  5 from 2021-09 record UCA steps, 19 logbook-stated). T1 +0.137 K/drive and
  T3 +0.048 K/drive fitted; magnet M2-M4 no longer respond to microwaves
  (not significant); cup gain 10.2 -> 2.21 K/W, from T1.
* `3713e21` Microwave autopilot follows the history UCA timeline: RF ON at
  5 V, then each logged setting, back to 5 V before RF OFF.
* `15064bf` History UCA from the cool-down logbooks
  (`cooldown_sim/model/uca_logbook.yaml`, `uca_history.py`): RF goes on at
  UCA 5 V and power goes to 0 V by hand later, so RF flips are not power
  steps. Fallback: full power 5 min after RF ON where nothing is logged.
  2020-09 to 2021-09 use the record's UCA column (it was real then).
* `04f5541` Sim Control: **NMR centre / span** inputs (0 = follow the record)
  and readbacks; the UCA voltage input is on the screen.
* `206ec7b` `quench` scenario: quench at 18 min so the fitter calibrates
  first (was failing intermittently).
* `ab300e9` Microwave drive from the **UCA voltage** (vendor curve for the
  repaired chain) instead of the Vaunix setpoint; chain saturated from
  -5 dB; replay autopilot follows the UCA procedure.
* `c1d938b` **T1/T3 read by Genesis** (dev-sim only): Cernox Chebyshev curves
  in the LS224 driver; stick 2 calibrations; `ls224_magnet` reads T1 (C2)
  and T3 (C4); archived. T1's range-2 limits were transposed in the lab's
  list: corrected, and the loader swaps a reversed pair.
* `5cf7c92` Coldboard supply emulated as the SPD1305X it is.
* `684382e` Coldboard varactor heating neglected in the model.
* Channel map and docs: Q1-Q7, Q24-Q26 answers (manometers, TPG 262 on the
  Roots pump, IONIVAC = shells, cold-cathode back-up, Pis, Siglent supplies,
  instrument priorities, missing instruments).
