# Simulated cool-down: changelog

What changed, newest first, one entry per change on branch `dev-sim`.
Each entry gives the commit, what changed, and why. The narrative and the
findings are in `COOLDOWN_SIM_PROGRESS.md`; questions for the lab are in
`COOLDOWN_SIM_OPEN_QUESTIONS.md`.

## 2026-10-15

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
