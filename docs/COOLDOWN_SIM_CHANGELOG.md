# Simulated cool-down: changelog

What changed, newest first, one entry per change on branch `dev-sim`.
Each entry gives the commit, what changed, and why. The narrative and the
findings are in `COOLDOWN_SIM_PROGRESS.md`; questions for the lab are in
`COOLDOWN_SIM_OPEN_QUESTIONS.md`.

## 2026-10-16

* Channel map: LabVIEW's "Roots Pressure (Torr)" is the top Roots gauge
  (TPG 262 gauge 1), now high confidence; question 3's second half settled.

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
