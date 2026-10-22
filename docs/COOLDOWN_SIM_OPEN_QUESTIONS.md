# Simulated cool-down: open questions

Everything the sim work is waiting on from the lab. The checklist is the
index; details follow. Answers go into `cooldown_sim/channel_map.yaml` (then
regenerate `docs/COOLDOWN_SIM_CHANNEL_MAP.md` with
`python -m cooldown_sim channel-map-report --coverage`) or the model files
named in each item. Numbers are stable: answered items move to *Settled*.

## Checklist (all open items)

**Equipment and wiring**
- [ ] 64. **A live port for the trend-history service (rephrased 2026-10-22).**
      The Panopticon's trends come from a new Genesis service, the trend
      history (`UNH:GEN:HIST`, built on dev-visualizer). In the simulator it
      runs on sim ports. To run it on the real DAQ it must be added to Utsav's
      IOC manager (`configs/target_physical/ioc_manager/settings.yaml`) with
      its own Channel Access / PV Access server port pair, like every other
      IOC, and the Phoebus settings must list that port. The pair first
      suggested (5123/5223) has since been given to the flowmeters, and
      5124-5129 are taken too (flowmeters, LHe levels, Manometer-Lo, the cart
      IONIVAC). The question: may it have **5130/5230** (free everywhere
      today), and should I add the entry (an edit to a Genesis file, so with
      your and Utsav's OK)? Until then the Panopticon's trends work in the
      simulator only.
- [ ] 69. **Manometer-Lo reads line fragments (for Utsav, live):** in the
      2026-10-20 sample 12 of 35 raw lines were fragments (`NGE`, `=RANGE`,
      `GE`, `E` ...): after `discard_input()` the next `read_until()` returns the
      tail of a half-received line. Harmless for `VAC=RANGE`, but a fragment of
      a number (e.g. the tail of `VAC=1.234E-01`) would parse to a wrong
      pressure after the 4-character strip. Suggest dropping the first line
      after discarding, and reporting `RANGE` as over-range, not PARSE_ERR.
- [ ] 70. **LS455 gaussmeter (live):** in the sample `Fringe_IDN` and the unit
      are empty, the field reads 0 and COMM flips between 0 and 1. Is the
      gaussmeter powered/connected?
- [ ] 71. **Recording disk space (24/7, for Utsav):** the 2026-10-20 sample
      scales to ~2 GB/day (cryo only; ~3.5-4 GB/day estimated with microwaves,
      ssRF and the fitter live), i.e. ~0.7-1.35 TB/year recording around the
      clock. The master recorder stops when < 2 GB is free
      (`archive/master_service.py`, ~0.5-1 day of margin) and nothing compresses
      or offloads old days (the zip compresses ~10x). Ells (2026-10-21): NO data
      may ever be pruned or deleted -- all data is important. So the options are
      only: (a) LOSSLESS compression of each completed UTC day (verified before
      the uncompressed copy is replaced), (b) a free-disk PV with an early alarm
      (e.g. 50 GB), (c) adding storage / COPYING completed days to lab storage
      (backup, never a move-and-delete). Which? And how much free space does
      the DAQ (DESKTOP-M34HHNQ) have?
- [ ] 73. **Start-up zeros recorded as valid data (for Utsav, live):** the
      master archive's first F1-F11 samples are 0 K with quality `valid`; the
      per-PV CSVs log Pump_PI 0 at 09:12 and Cell_PI 0 in the early sessions.
      A gauge that has not produced a reading should be NaN / `missing`, not a
      measured zero (same pattern as the sim's known issue
      never-connected-reads-zero for the IONIVAC).
      The Genesis -> LabVIEW converter passes these zeros through for now.
- [ ] 81. **Converter, when is pyNMR "calibrated"?** Without the Genesis
      fitter recorded, the converter treats a finite non-zero pyNMR `cc` as
      calibrated (writes P %), else the area. But pyNMR always carries a `cc`
      from its channel config. Does pyNMR record whether a TE was taken, or is
      the Genesis fitter's `CALIBRATED` (Preset = not calibrated) the rule?
- [ ] 87. **Magnet ramp interlock details (follows 85):** (a) when a
      condition trips during a ramp, should Genesis HOLD at the present
      field, or run the magnet DOWN to zero (and at what rate)? Is it the
      same for every condition (e.g. hold for a vacuum alarm, run down for
      low LHe)? (b) Thresholds (magnet LHe settled, see Settled): which magnet thermometers, and how warm, count as "warming" (e.g. M4 /
      M5 above some K)? Is a shells-vacuum *alarm* (the IONIVAC limits of
      question 3: 1e-4 Torr warm, 1e-5 Torr cold) the trigger, or a trip
      level? (c) In persistent mode, does any of this apply (the supply is
      at zero then)?
- [ ] 88. **LS455 port shared with the student's magnet IOC?** The student's
      draft magnet IOC (reviewed 2026-10-22, kept local in `magnet_student/`) reads
      the fringe-field gaussmeter itself at 192.168.1.251:5007 every second.
      That serial-server port takes one connection, and Genesis's `ls455` IOC
      uses it too. Was the student's IOC running on 2026-10-20? If so, it
      would explain question 70 (Genesis's LS455 reading nothing).
- [ ] 89. **Fridge LHe volume conversion, step at 10.71":** below 10.71" the
      conversion counts 8.8 + h inches of the Ø2.25" tube; from 10.71" a fixed
      19.1". 8.8 + 10.71 = 19.51, so the volume steps DOWN by ~0.027 L (1.2705 L
      at 10.70" -> 1.2445 L at 10.71"). Should it be 19.51 (or the boundary
      10.3")? Also exactly 15.71" matched no branch in the original (the sim
      uses the neighbours' equal value). `cooldown_sim/conversions.py`
      `fridge_lhe_liters` keeps it as written until you say.
      Checked against the inner-shell drawings (2026-10-22, lab drawing set,
      12 sheets): (a) Ø2.25" = the aluminium tube's bore (2.250 ID, 2.500 OD,
      **19.100" long**) and its flange's bore (2.250); Ø5.78" = the 5.000"
      stainless tube's bore (5.782). Both match. So 19.1 is the tube's length;
      the aluminium part's Ø2.25 interior is ~0.4" longer (the tube seats 0.2"
      into a 0.6" flange with the same bore), ~19.5" = 8.8 + 10.71. That points
      to 19.51 being right for the third and fourth branches. (b) Between that
      flange and the Ø5.782 tube, two stainless rings have a Ø3.000 bore
      (~0.9" stacked, less overlaps, about 0.1 L), which no branch includes.
      (c) **Ø7.26" does not match:** the 28.2" stainless tube above is Ø7.380
      ID (7.500 OD); 7.250 is only the counterbore in the ring it seats in.
      Ø7.38 gives 3.3 % more volume per inch above 15.71" (0.701 vs 0.678 L/in;
      +0.10 L at 20"). Use 7.38?
      Re-checked 2026-10-22 with the drawings turned to the fridge's real
      orientation (they are printed sideways): the paper's horizontal is the
      fridge's vertical; the aluminium tail (Ø2.25) is the bottom, the
      stainless cross with the CF ports the top, so up the fridge is Ø2.25,
      then two Ø3.000 rings, Ø5.782, Ø7.380, the same order as the equation.
      Lengths along the paper are heights. The aluminium part is 19.950 long
      overall and its end cap 0.450 thick, so its Ø2.25 interior is 19.50
      (if the cap's 0.2 step is a spigot into the tube) or 19.70 (if it is a
      recess the tube sits in); 8.8 + 10.71 = 19.51 fits the first. The
      joints (which steps are spigots, which recesses) are read from the
      section views and could each be off by 0.2. The diameters do not
      depend on any of this.
- [ ] 90. **Source of the banded magnet ramp rates:** the student's IOC ramps
      at 3 / 2 / 1 A/min below 20 / 40 / 65 A, with no source given; the shift
      handbook and AMI give only a single 3 A/min (3.02) limit. The records
      show exactly these bands from 2021-09 on (medians 3.0 / 2.0 / 1.0 A/min),
      3 A/min at every current in 2020, and about half rate above 40 A in
      2022-05 and 2024-08/09. Where do the bands come from (the LabVIEW magnet
      program, a quench-safety rule?), and should Genesis keep them?
      The IPS120-10 itself can hold rate limits by current band (test menu
      Sup 05 breakpoints, Sup 08 / 09 rates; the status word then shows "sweep
      limiting"). Are 3 / 2 / 1 A/min programmed in the supply? Someone can
      read them on the front panel (Test 07). If so they are a cap that does
      not depend on Genesis.
- [ ] 91. **"Cryogenics running" before magnet control (Ells 2026-10-22:
      required; the level-meter checks most important):** which cryogenics
      IOCs must be up for the magnet to move: the magnet LHe meter
      (`lhe_magnet`) and magnet thermometry (`ls224_magnet`) surely; also the
      fridge LHe meter, the shells' IONIVAC, the safety interlock, the alarm
      summary? How recent must the LHe reading be (e.g. updated within 60 s)?
      And with the magnet persistent at field when the cryo IOCs stop (a
      restart), should Genesis only block new commands, or alarm too?
- [ ] 75. **Converter, `NMR Status`:** filled with pyNMR's free-text event
      `label` (default "None"). The old values were Baseline / Polarization /
      TE / Tuning / Scanning, and the McClellan reader sorts sweeps by them.
      Should pyNMR labels be mapped onto those words, and how?
- [ ] 76. **Converter, `VME Mode`, `NMR Domain`, `IF Attenuation`:** written as
      NOT_IN_GENESIS. Does pyNMR have equivalents (in its channel / settings
      record) that should fill them?
- [ ] 77. **Converter, microwave settings:** `mmWave Freq Setting 1` is taken
      from `MW:SUP:OutputFrequency` (the output-frequency command), which is
      an assumption. `Setting 2`, `mmWaves Span` and `Sweep/Hop Time` are
      NOT_IN_GENESIS. Should they come from the supervisor's software sweep
      (`SweepStart` / `SweepStop` / `SweepDwell`)?
- [ ] 79. **Converter, stale readings:** used by default, because a steady
      value stops updating and turns `stale` after 60 s (`Pump_Unit`, a 0 G
      fringe field). `--strict` uses `valid` only. Is the default right?
- [ ] 80. **Converter, NMR sweep:** each row carries pyNMR's raw `phase` sweep
      (512 points, against the old 451), once per sweep. Is `phase` the right
      signal? The McClellan reader refuses mixed sweep widths across files, so
      old and Genesis-era days cannot be stitched into one session as is.
- [ ] 66. **LS224 RDGST? range bits:** Utsav's driver accepts RDGST? bits 16 /
      32 (outside the 224's own internal curve) as "OK; 224 TEMP RANGE",
      because it converts SRDG? ohms itself, and his test data say the live
      T1 / T3 answer 32 at room temperature. Which curve is assigned inside
      each 224 to C2 / C4 (and to the F / M inputs), so that the emulator can
      raise 16 / 32 where the real units do? The emulator can serve them
      (tested), but answers 000 by default.
      Live 2026-10-20: T1 (C4, 39.92 ohm) and T3 (C2, 47.77 ohm) both read
      ~298 K with status `OK; 224 TEMP RANGE` at room temperature, i.e. bits
      16/32 set -- confirms Utsav's C4/C2 mapping and his curves.
- [ ] 50. (Housekeeping) TXI040 port 8 is vacant but set to TCP 5011, the
      same as port 5 (the TPG 262 that Genesis uses); give it a free port
      (e.g. 5014) before anything is plugged in. TXI017 port 2 uses TCP 23
      (the telnet port) -- intended, or a default to change?
- [ ] 12. (Optional) cold-board voltage to Q-curve tune: MHz per volt, or
      the tune at 0 V and 19 V.

**Microwave and ssRF power**
- [ ] 9. SIG's calibration (reflected power); roughly when the manual supply
      replaced the analog-output drive.
- [ ] 10. **REMINDER for Ells:** send the ssRF heat data (heat vs power at
      the coil) taken in the prep for the 2026-06 cool-down, once analyzed.

**Target physics**
- [ ] 51. Which spin-up is `fit_asym_pq.json` (the irradiated d-butanol rate
      set) from? Its JSON records B 5.077 T and 0.997 K (the Dec 2025
      cool-down), but its data file is 2026-06-18 (4.939 T). Also, its input
      is an equilibrium-ratio export whose Q is Q_B(P) by construction, so
      k_delta sits at its bound and the Boltzmann pinning may be an artifact;
      the 2024-09-18 freq-hop Q runs 0.3-1.1 % above Q_B while P grows.
      Refit with k_delta free on independent Q?
- [ ] 52. Irradiated d-butanol steady state: with these rates the sim
      plateaus at ~48.6 % at 4.85 T / 1.08 K, ~5 pp above 2024-09's measured
      43.7 %. Fix with a small P-down admixture at the optimum (A/B ~0.05,
      the 2024-09 spin-up fit), retune leakage, or leave it?
- [ ] 53. (Run request) A two-pass (up and down) slow microwave sweep out to
      f/B >= 28.06 GHz/T on irradiated d-butanol would locate the alpha
      (P-down) maximum, which the 2024-09-18 sweep (ending at 28.045, still on
      the alpha plateau) could not.
- [ ] 36. **The last thing blocking Magic +/- in Genesis:** the supervisor's
      `CentralFrequency` is declared in GHz (default 140, the output band's
      middle), but its Magic formula needs the NMR centre in MHz (field =
      CentralFrequency / 6.53566 MHz/T). Which is intended? Your constants
      are in (Settled, 34); `gamma_d_over_2pi` is left unset until this is
      answered, so Magic is still refused. (Details below.)
- [ ] 47. Panopticon magnet neck: drawn to scale, the AMI drawing puts all
      five neck baffles (31.6, 33.5, 36.4, 40.4, 45.4") inside the magnet's
      LHe neck, which runs from the belly top (31.5") to the top flange
      (51.5"), so none falls outside the magnet space. Which baffles did you
      mean as lying outside it? (The fridge's own plates are its
      heat-exchanger stacks, inside the insert.)
- [ ] 54. Trend-history service (`UNH:GEN:HIST`, dev-visualizer 3bf8c3d; the
      Panopticon's trends now come from it, full on open): (a) deploy it live
      (an IOC-manager entry, suggested CA/PVA 5123/5223, plus the Phoebus ini
      address list)? (b) window and sampling OK (6 h at 12 s; shells vacuum
      2 days at 120 s), and which PVs? (c) add the fitter's P/Q PVs to the
      archiver, so the polarization history survives a restart (now it fills
      from service start)? (d) `max_hold` 900 s: a reading that stays exactly
      constant is not re-archived, so after a restart a long steady stretch
      shows as a gap -- longer?
- [ ] 55. Panopticon fridge (dev-visualizer 8fbcada): on the magnet's inch
      scale, the fridge blueprints' vertical layout puts the lower HX stack
      35-42" below the top flange, i.e. inside the 4" nose (impossible), so
      the stacks and separator keep their heights from the UNH thermometry
      diagram. Are the blueprints the UNH fridge as built, or the JLab g2p
      design it was based on (with different lengths at UNH)?
- [ ] 56. Panopticon drawing details: (a) the blueprint's refrigerator top
      flange is 10.000" in diameter, but the drawing's two dark bars are
      wider (lower/HX-dewar flange 13.3", head plate 12.1"); which bar is the
      10" flange? (b) The middle HX plates are 6.9" across vs the upper
      6.375" -- really wider? (c) With the cup centred on the magnet, the 8"
      column steps to the nose at 19.4" (AMI drawing: 18.5") and the nose
      ends at 3.3" (AMI: 1.5"); OK, or lengthen? (d) The magnet's widths are
      still on the old 15 px/in scale (true scale would be ~12 % narrower);
      redraw them to scale?
- [ ] 44. Panopticon plot toolbars (zoom/pan/axis limits) take ~30 px from
      each plot; the small pressure and shells plots are tight. Keep them, or
      hide them and rely on Ctrl+drag zoom / Shift+drag pan?
- [ ] 8. After a magnet quench, what does the field do, so the sim's quench
      can follow it? (a) How fast does the field fall, and to what (all the
      way to 0 T)? (b) How long until the magnet is ramped back up, and at
      what rate (the handbook's 3 A/min)? (c) After the re-ramp, does the
      polarization start again from thermal equilibrium (near 0), or is
      some of it left?
- [ ] 37. (Low priority) 2020-12-10/11 TEMPO d-butanol: the record's area P
      reaches ~35 % within an hour, but the TEMPO rate fit saturates near
      25 % at the same conditions. Calibration, or the fit's drive?

**Live-system fixes (Genesis itself; listed in COOLDOWN_SIM_PROGRESS.md)**
- [ ] 16. Over-flood alarm: F1_TI LOW/LOLO values (about 5 K / 4.5 K?), and
      take F1 out of the cold-board interlock rule.
- [ ] 17. Cold-board interlock: what, if anything, should it protect now that
      the supply is varactor bias (the 120 W heater-style rule)?
- [ ] 27. UCA cable check (live fix): the AMC's UCA input defaults to FULL
      power when nothing is connected, but Genesis's RF permissive checks
      only the SPD1305X's own 5 V read-back. An unplugged or broken UCA cable
      would pass the check and give full power. Can it be detected (e.g. SIG
      response, a sense line, or a procedural check)?
- [ ] 25. Shells-vacuum alarm: how rapid a pressure rise counts as "rapid"
      (e.g. a factor of 10 within some minutes)?
- [ ] 39. Shells-vacuum alarm between 80 and 300 K: which limit applies
      (1e-4 until the magnet bottom is below 80 K? stepped or log-interpolated
      in between?). And is "the bottom of the magnet" M5 (`M5_TI`, LS224
      magnet)? Should the alarm be a warning (MINOR) or MAJOR at those limits?
- [ ] 18. When to schedule the live fixes (findings 1-8 plus the vacuum and
      F1 interlock rules).

**Sim decisions**
- [ ] 31. Review the logbook UCA timeline's 18 ambiguous entries. They are
      listed, with the logbook text, in the private file
      `ref_technotes/uca_logbook_review.md` (section "Ambiguous - needs
      Ells"). Examples: 2026-06-16 21:01-21:27 (record says RF off, logbook has
      the UCA at 3.66 then 2.9 V and P rises); 2024-08 (record never shows RF
      ON); partial settings during alignment/EPR tests.
- [ ] 32. The 62 mW microwave heat (2024-12-17 UCA toggles): the first toggle
      shows ~0.85 slm of extra Roots flow, the later 4-6 min toggles only
      0.22-0.51 slm. Was the boil-off still settling, or is 62 mW high?
- [ ] 57. Quench and the shells vacuum: does the IONIVAC (shells' vacuum)
      actually rise during a magnet quench, and roughly how far / how fast?
      The sim's quench scenario assumes a rise toward 1e-3 Torr (it used to
      put it on TPG gauge 2); it is an assumption for exercising alarms, not
      data.
- [ ] 30. Did LabVIEW read T1 with the transposed range-2 limits (stick 2)?
      If so, recorded T1 between about 3 and 19 K is wrong; at 1 K it is
      unaffected (range 1). (Genesis is unaffected since Utsav's ccce63e: the
      ODS rows 2-T1 / 2-T3 have ZL < ZU in every band, and his loader rejects
      a transposed pair rather than swapping it.)
- [ ] 22. Try an interactive rehearsal (guide section 2) in Phoebus.
- [ ] 23. Merge into `elong` (and push `dev-sim` itself)? Your call.

## Notes (findings, not questions; Ells 2026-10-22)

- 60. **Fridge He Level zero:** the record's `Fridge He Level (")` is
      NEGATIVE most of the time the fridge runs (2026-06-17: -9 to -26 in;
      2022-05 down to -87, where `Fridge He Level Raw` is negative too) and
      ~5-14 in when it is not pumped. An AMI 1700 reports a non-negative
      level, and Genesis's `lhe_fridge` has scale 1.0, offset 0.0, so Genesis
      will not show the numbers LabVIEW logged. What zero/offset (or other
      instrument) produced the record? The sim replays it as recorded.
      Live 2026-10-20 (warm, empty fridge): the AMI 1700 itself reads
      `Fridge_LI` -18.7 in, so the negative values come from the meter's own
      zero/probe setup, not a LabVIEW offset. Still: what should 'empty' read?
- 61. **Flowmeter record units and labels:** (a) are the record's
      `Roots Flow L/R` and `Seperator Flow (SLM)` corrected SLM (the 28/19
      in Utsav's profiles already applied)? The sim assumes yes, so Genesis
      `_SLM` = record (the logbook's ~1 slm per 60 mW supports He SLM).
      (b) The 2020 single `Roots Flow (SLM)`: the left meter (as aliased), or
      a total? (c) `Roots Flow L` is empty from 2025-11 on: meter broken?
      (d) Separator flow is ~50 slm while running in 2026-06 but ~0.05 slm in
      2020-22: same quantity? (e) Excursions such as -84 / +166 slm (R,
      2026-06) and -308 slm (separator, 2021-09): real or DAQ artifacts? And
      the repeated 105.49 maximum on L: a saturation?
      Live 2026-10-20 (warm, no flow): left +1.36 slm, right -0.42 slm,
      separator -0.17 slm -- the meters carry zero offsets, which explains
      small negative readings.
      Ells 2026-10-22: (d) separator ~50 vs ~0.05 slm: uncertain; (c) the
      left meter was broken and replaced (HFM-D-301B, 120 slm); (e) the 105.49
      maximum is most likely saturation, not certain. Zero offsets: after the
      cool-down (see below).
- 62. **Manometer-Lo protocol and record:** (a) Utsav's `manometer_low`
      driver reads an unprompted line stream from `tools/pi_gateway.py`
      (drop 4 characters, take the first number, Torr), but you said the
      KJL-SPARC speaks a TPG-style serial protocol. Which is it, and what
      does one line look like? (The emulator streams `Pr: 1.2345E-01`, a
      guess.) (b) The record's Manometer-Lo reads exactly 0 whenever
      Manometer-Hi is above ~9-10 Torr: over-range reported as 0, or a valve?
      (c) Between 1 and 9 Torr, Manometer-Lo reads 1.1-1.6x Manometer-Hi in
      every era; which gauge is right there?
      Live 2026-10-20: the KJL-SPARC line is `VAC=<value>` (the 4-character
      prefix the driver strips); at atmosphere it sends `VAC=RANGE` (over the
      10 Torr range), which the driver reports as PARSE_ERR. See 69 for a
      fragment bug.

## After the cool-down (Ells 2026-10-22: not yet)

- Flowmeter zero offsets: with no flow (warm, 2026-10-20) the meters read
  left +1.36, right -0.42, separator -0.17 slm. Normal for these Hastings
  meters, and can the zero be adjusted?

## Details

### Channel map

3. **Vacuum: what should the interlock watch?** Ells (2026-10-15): both
   TPG 262 gauges are manometers on the Roots pump (gauge 1 `Pump` = top,
   gauge 2 `OVC` = bottom), and the IONIVAC measures the shells' insulating
   vacuum. So Genesis's "OVC" is a Roots-line gauge (see Findings). Before the
   sim's vacuum handling is reworked:
   * Should the vacuum interlock watch the IONIVAC (`ION:Ion_PI`) instead,
     and at what trip pressure?
   * ~~Is LabVIEW's `Roots Pressure (Torr)` the top or the bottom manometer?~~
     Ells (2026-10-16): the top one; the old LabVIEW DAQ only read the top
     Roots gauge, so the record has no bottom-gauge column.

### Microwave power

9. **Microwave power for the model.** Ells (2026-10-15): `MMwaves Power (dB)`
   is the Vaunix setpoint (default 10 dBm, rarely changed) and the UCA sets
   the actual power. But the `UCA (V)` column is not the real UCA for years:
   an analog-output drive had problems, an external manual DC supply drove
   the UCA instead, and the SPD1305X took over only in the last few weeks.
   So the record holds no usable measure of microwave power, only RF on/off.
   * The model's drive today is `RF_on * 10**((P_dB - 10)/10)`, built on the
     Vaunix setpoint. It reads 2022 to 2025-07 (Vaunix at -3 to -5 dB) as
     about 4 % power, and two of the 13 fitted thermal transitions (2022-05)
     carry drive steps of 0.05 for that reason.
   * Proposed fix: history drive = RF on/off only (full power assumed); live
     drive from the emulated SPD1305X's UCA voltage through a UCA curve
     (assumed until known); then re-fit (`fit-params`).
   * Needed: the UCA's power-vs-voltage curve (0 V = full, 5 V = none). The
     microwave chain's SIG output measures power (lower voltage = more
     power); once calibrated it could measure the curve directly.
   * Roughly when did the manual supply take over from the analog output?
   * Ells (2026-10-15): newer microwave power data are coming. The chain
     saturates from about -5 dB on the Vaunix, so the -3 to -5 dB setpoints
     of 2022 to 2025-07 still gave full power: the setpoint can be ignored
     except well below -5 dB (the record's -40 dB entries).

### ssRF power (come back to later)

10. **ssRF heat vs power at the coil.** The ssRF line has a Mini-Circuits
    LZY-22+ amplifier (~44 dB gain, P1dB ~+43 dBm), so a generator setting of
    -5 dBm should deliver watts to the coil, well below compression. The
    AFP and ssRF lines are identical (Ells, 2026-10-15), so the amplifier is
    in the ssRF line too. The sim's
    burn heat is the measured 140 mW at -5 dBm (2.25 slm boil-off, logbook
    2024-12-18), roughly an order of magnitude less. Which is it:
    * most of the RF power is dissipated warm (cables, reflection) and never
      reaches the helium;
    * the burn was short against the ~15 s Roots-flow response, so the
      boil-off caught only part of it;
    * the line was different in December 2024?

    Ells (2026-10-16): there are measurements, probably taken in the prep for
    the 2026-06 cool-down; she will analyze them and send the result. Waiting
    on that data, not on a decision.

    The measured heat stays right for boil-off and temperatures. The power at
    the coil (B1) is what should set the hole-burning rate, which the sim
    currently assumes. Ells has the details in a private lab note; keep them
    out of this repository.

### Target model (currently **assumed**)

5. **Q during a spin-up from zero.** After a quench or anneal, does the
   tensor polarization Q follow Q_B(P) = 2 - sqrt(4 - 3P^2) as P rebuilds, or
   does it lag? The model re-heals Q towards Q_B(P) with an assumed 240 s
   time. During the `quench` scenario's re-spin-up, Q then sits about 25 %
   below Q_B(P). The equal-spin-temperature fitter reads P from the line, so it
   reads about 13 % low there. A spin-up from near zero P with fitted P and Q in
   the past data (or the Spin-1-Rates-Fit results) would settle this; the value
   lives in `cooldown_sim/model/fit_params.py` (`Q_heal_tau_s`).
   * Ells (2026-10-15): during a quench P and Q cannot be measured at all.
     The magnet field crashes, so the Larmor frequency moves rapidly away
     from nominal and the NMR signal leaves the window. Whether Q lags once
     the field is back and P rebuilds is still open.
8. **Field during a quench.** The sim's `quench` holds the field at the
   recorded value, so the line stays on the window and the fitter keeps
   reporting P throughout (wrong, per Ells under question 5). The NMR source
   already drops the line when B moves (signal at the Larmor frequency for the
   model's B, none below 0.5 T), so a quench that collapses B would lose the
   line, as on the real system. To model it:
   * How fast does the field fall in a quench (dump time constant), and to
     roughly what?
   * How is it brought back: roughly how long after a quench before ramping
     starts? (The ramp-rate limit is in Ells's lab notes.)
   * After the re-ramp, does the target start from P = 0 (or thermal
     equilibrium) at full field?

### Smaller items

15. **Target stick and T2.** Thermometer names carry the stick version in
    the lab's calibration list (1-T#, 2-T#). Genesis's calibration file
    matches stick 2 (T1 C2, T3 C4, no T2).
12. **Cold-board tune.** Higher cold-board voltage moves the Q-curve's tune
    to higher frequency. With MHz per volt the sim's NMR source could move
    the Q-curve, and rehearse retuning without a fresh baseline.

### Added while porting lab documents (2026-10-15)

25. **Shells-vacuum alarm.** Genesis's IONIVAC record alarms at 800 / 900 Torr
    (HIGH / HIHI), which never fires on a vacuum loss in the shells. Ells
    (2026-10-15): it should warn on a rapid rise, and particularly if the
    pressure ever gets above the 1e-4 Torr range. Ells (2026-10-16): the
    IONIVAC is the vacuum of the fridge's shells and very important to track;
    the alarm limit depends on how cold the magnet is: with the thermometry
    at the bottom of the magnet above 300 K, alarm above 1e-4 Torr; below
    80 K, alarm above 1e-5 Torr. A static HIGH/HIHI cannot do this: Genesis
    needs logic that picks the limit from the magnet-bottom temperature.
    Still to set: what rate counts as rapid; 80-300 K (question 39). Whether
    the vacuum interlock should also act on it is question 3.
29. **Cernox (CCX) support for T1/T3.** Lake Shore's Chebyshev form, per
    range: Z = log10(R), k = ((Z - ZL) - (ZU - Z)) / (ZU - ZL),
    T = sum_i A_i cos(i arccos k). The lab's calibration rows hold four ranges
    (ZU, ZL, up to 9 coefficients each) followed by three resistance
    boundaries in ohms (R above the first uses range 1, and so on). Checked
    against a Lake Shore calibration certificate: the evaluation reproduces
    the certificate's fitted temperatures from 0.37 K to 300 K. The sim's
    LS224 emulator would also need the inverse (T to R, by root-finding).

### Live-system fixes

16. **Over-flood.** F1 at or below 4.5 K means an over-flooded fridge with
    liquid helium heading for the Roots pumps. Genesis has no protection: the
    interlock compares F1 >= 4.5 K (inverted), acts on the cold board, and
    F1's alarm limits are 0.05 K / 0 K. Proposed: F1_TI LOW/LOLO near
    5 K / 4.5 K. Which values?
17. **Cold-board rule.** The interlock's cold-board rule (F1 temperature and
    a 120 W power limit) was written for a heater. The supply biases
    varactors at 0-19 V. Keep a rule, change it, or drop it?

## Settled

* Magnet needs the cryogenics running (Ells, 2026-10-22): the magnet
  subsystem must not command the supply unless the cryogenics subsystem is
  running; the magnet LHe level-meter checks are the most important part.
  Missing or stale cryo data blocks the magnet. HOLD is always allowed.
  Details: question 91; plan: `docs/COOLDOWN_SIM_MAGNET_PREP.md`.

* Flowmeters (question 49 d; Ells with a lab expert, 2026-10-22):
  separator = Hastings **HFM-D-300B, 40 slm**; left Roots = Hastings
  **HFM-D-301B, 120 slm** (it replaced the broken earlier meter, which is why
  the record's left flow is empty from 2025-11); right Roots = Hastings
  **HFM-D-300B, 40 slm**. The 28/19 factor applies to the **right Roots only**;
  the separator and left-Roots meters have none. **For Utsav:**
  `configs/target_physical/flowmeter_left` and `flowmeter_sep` both set
  `correction: 1.4736842105`, so Genesis reports those two flows 47 % high;
  they should be 1.0 (`flowmeter_pi` keeps 28/19). The sim is unaffected: its
  emulators divide the record by whatever factor the profile holds.

* LHe level meters (question 49 b): Ells (2026-10-22), the magnet's is an
  AMI Model 135 and the fridge's an AMI Model 1700, as Utsav's `lhe_magnet` /
  `lhe_fridge` drivers assume.

* TPG 262 units (questions 65, 68): Ells (2026-10-22), the readings must
  be in Torr: if the controller ever reports another unit, Genesis sets it to
  Torr (`UNI,1`). For Utsav: `target_ioc/instruments/tpg262.py` today only
  reads the unit (`UNI`). The alarm limits and the vacuum interlock's trip are
  then in Torr. The sim's emulator now answers Torr (`UNI` = 1, as live) and
  accepts `UNI,<n>`; the Panopticon's history service follows `Pump_Unit`.

* Vapour-pressure temperature (question 63): Ells (2026-10-22), yes, Genesis
  should use her conversion (the lab's offline `t90_torr_to_k`, in the sim as
  `model/physics.py`), which covers below 1.25 K, instead of
  `genesis_common/its90_helium.py` (NaN below 0.8606 Torr). For Utsav.

* Manometer-Hi vapour-pressure temperature (question 78): Ells (2026-10-22),
  yes, Genesis should publish it (e.g. `Cell_VapT`), as LabVIEW did. For Utsav.
  The Genesis -> LabVIEW converter computes it itself meanwhile.

* Pumping-cart IONIVAC (question 67): Ells (2026-10-22), record it (Utsav's
  archiver already lists `UNH:GEN:CRYO:ION:PUMP:*`), give it its own separate
  screen with a plot, and keep it off the Panopticon.

* Field equation (question 86): Ells (2026-10-22), measured by NMR on the
  target material, so it includes every field around the magnet (Earth's as
  an offset): **B = 0.1027405 x I + 0.00003497875 T**. The McClellan reader
  and the sim's synthetic cool-downs already used it; the sim's target model
  now does too (`model/target.py` `field_from_current`) where a record logs
  only the current. The DAQ's own field column used 0.10274113 T/A, no
  offset; AMI's catalogue coil constant is 1.031 kG/A.

* Magnet LHe threshold for ramps (question 87, part b): Ells (2026-10-22),
  20" blocks a ramp for most users; an **Expert** option allows ramps down to
  16" (AMI's minimum operating level, "for safe magnet operation").

* LHe level -> volume (Ells 2026-10-22): magnet space and fridge, piecewise in
  height, now `cooldown_sim/conversions.py` `magnet_lhe_liters` /
  `magnet_lhe_inches` and `fridge_lhe_liters` (tested against her code
  verbatim). Magnet: 20.0 L at 16", 31.6 L at 20", 59.0 L at a full 31".
  Fridge: 0.57 L at 0", 6.30 L at 20" (one quirk, question 89).

* Magnet limits (question 84): from the AMI magnet specification (system
  14109, tested 2013-01-23; Ells 2026-10-22 pointed to it): rated operating
  current 67.9 A for the rated 70 kG (7 T) central field, not warranted above
  it (tested to 71 kG at 4.2 K); maximum charge rate 0.0504 A/s = 3.02 A/min;
  38 H, 2.0 V charging voltage. The student's draft ramps at 3 / 2 / 1 A/min
  below 20 / 40 / 65 A, and the records show those bands from 2021-09 on (the
  source is question 90). The draft's software limit is < 65 A.

* Magnet ramps from Genesis (question 85): Ells (2026-10-22), (a) Genesis
  both shows AND commands the supply (target field, ramp) from the start;
  (b) yes, Genesis refuses to start a ramp, and stops one already running,
  when something is wrong: magnet LHe below the 20" alarm, magnet
  thermometers warming, a quench, a shells-vacuum alarm. What "stop" does
  and the exact thresholds: question 87.

* Magnet supply model (question 82): Ells (2026-10-22), yes, the supply on
  `Magnet_IPS` (TCP 5001, 9600 8N2) is an Oxford IPS120-10.

* Persistent mode (question 83): Ells (2026-10-22), the magnet is run
  persistent sometimes, not always. The records never show it (they log the
  supply current equal to the magnet current), so the emulator must support
  both: driven, and persistent (switch heater off, magnet current held while
  the supply's output goes to zero).

* `Area/Polarization` in the converter (question 74): Ells (2026-10-21),
  before any TE / calibration constant it is the area sum(V * df); after a
  TE and/or a calibration constant it is the vector polarization P. The
  converter follows the old DAQ's own format: a bare number is the area
  (sum of pyNMR's processed `fitsub` sweep x the frequency step, MHz), a
  value with `%` is P (`SSRF:FIT:P_AREA` once `SSRF:FIT:CALIBRATED` reads
  Calibrated; "Preset" constants count as not calibrated). The Genesis
  fitter does not publish the area itself. Follow-up: question 81.

* Over-pressure on 2026-10-20 (question 72): Ells (2026-10-21), yes -- an
  over-pressure test, 15:12-15:21 local: both Roots gauges, the OVC gauge and
  the cell MKS 902B read ~890-959 Torr (~1.25 atm) together, back to ~760 Torr
  by 15:34. A real event, and a useful cross-check that the three gauge IOCs
  agree (per-PV cryo CSVs in sample_data/).

* T1 / T3 slots (Ells 2026-10-21): keep Utsav's ccce63e, operator-confirmed:
  T1 = C4 (ODS row 2-T1), T3 = C2 (row 2-T3), all LS224 curves from
  `calibration/Thermo_Calibrations.ods` (the CSVs are historical only). His
  C4 curve gives 297 K at the live ~40 ohm room-temperature reading; the
  earlier dev-sim mapping (T1 = C2) gave 328 K. The sim follows the profile.

* Pumping cart IONIVAC (question 49c): Utsav's ccce63e adds the IOC
  `ionivac_pump` (TXI040 :5005, `UNH:GEN:CRYO:ION:PUMP`, live CA 5129); the
  shells' `ionivac` stays on :5006. Emulated in the sim (replay CA 6110,
  emulator 16006), absent in replays (question 67).

* NMR switch meaning in the ssRF interlock (question 59): fixed by Utsav's
  `feature/ssrf-pynmr-only` (merged into dev-sim 2026-10-19): NMR mask 0 = on,
  mask 3 = off; ssRF the same; the interlock reaches SSRF_READY and grants its
  permits. In the sim all three replayed burns now complete with RF on (the
  ssrf-burns scenario expects PASS; known issue
  ssrf-interlock-permits-withheld retired).

* Recording location (Ells, 2026-10-19): keep Utsav's design. Genesis
  recordings (master archiver, subsystem PV CSVs, ssRF events and sessions)
  live outside git under `$GENESIS_DATA_ROOT` or `UNH-Genesis-Control/local_data/`
  (a flat clone uses the sibling `<checkout>-local_data/`), created
  automatically on first start (`archive/data_paths.py`,
  `docs/operations/MASTER_ARCHIVER_OPERATOR.md`). No in-repo data folder.

* Push (question 23, part): Ells (2026-10-16), push `dev-visualizer` (it
  carries all of dev-sim) so others can run the Panopticon; quick start in
  `docs/PANOPTICON_QUICK_START.md` (dev-visualizer). The planned squash of the
  LZY-22+ commit with the question-10 note was done first (one commit,
  identical files).

* NMR / ssRF switches (Ells, 2026-10-16): NMR switch 192.168.1.8: /SETP=0 =
  NMR ON, /SETP=3 = NMR OFF. ssRF switch 192.168.1.11: /SETP=0 = ssRF ON,
  /SETP=3 = ssRF OFF. Sequencing rule: the NMR must always be OFF before the
  ssRF is switched ON, and the ssRF must always be OFF before the NMR may be
  switched ON. (The interlock's stated principles agree; its NMR-port logic
  is question 59.)

* Switch and supply IPs (question 58): Ells (2026-10-16), the NMR switch is
  192.168.1.8 and the ssRF switch 192.168.1.11 (as configured); the cold-board
  supply is now 192.168.1.18. The channel map's 192.168.1.18 for the NMR
  switch was a transcription error, now fixed.

* IONIVAC port (question 48): Ells (2026-10-16), there are TWO IONIVACs. The
  shells' one (the important one) is on TXI040 :5006; :5005 is the IONIVAC
  on the portable pumping station (a cart with roughing and turbo pumps,
  moved around the lab). Genesis's IONIVAC IOC connected to :5005 on every
  branch, so live Genesis has been showing the cart's vacuum as the shells'.
  Fixed on dev-sim (`configs/target_physical/ionivac/settings.yaml` -> 5006);
  the live system needs the same one-line change.

* Magic +/- constants (question 34): Ells (2026-10-16), GHz/T:
  TEMPO d-butanol 28.0527 / 28.0919; trityl d-propanediol 28.0316 / 28.0468;
  irradiated d-butanol 28.0236 / 28.0438; irradiated ND3 28.0245 / 28.0605
  (ND3 needs a slow sweep for better numbers). In the sim
  (`spin1_materials.yaml`, new `nd3` material) and the Genesis supervisor's
  dopant list, per dopant AND material (the supervisor reads the cryo
  material when Magic is pressed). Irradiated d-butanol's agree with the
  2024-09 slow sweep: + on the measured beta peak (28.023 +- 0.001), - on
  the alpha plateau (28.039-28.045).

* Sim vacuum rework (question 20): Ells (2026-10-16), go ahead. Do NOT feed
  TPG gauge 2 (`OVC_PI`, the bottom Roots gauge) from the record (LabVIEW
  never logged it), but keep it in the live datastream for new data; the
  `vacuum-trip` scenario should primarily be about the shells' vacuum
  measured by the IONIVAC.

* Serial device server ports (question 45): from the lab technote 2026-TN-02
  (TXI040, 192.168.1.251) and the TXI017 port setup pages Ells sent
  (192.168.1.250), 2026-10-16:
  * TXI040: 1 Manometer_HI :5004, 2 Pumping Station :5005, 3 Ionivac :5006,
    4 Fringe Field :5007 (19200 7O1), 5 ROOTS (TPG 262) :5011,
    6 left_Roots_Pump :5012, 7 Separator_Pump :5013, 8 vacant.
  * TXI017: 1 Magnet_IPS :5001 (9600 8N2), 2 Magnet_LHE_Prob :23 (9600 8N1).
  Follow-ups: the IONIVAC port mismatch (48), models and priority (49),
  port housekeeping (50).

* d-butanol type (question 46): Ells (2026-10-16), yes, the d-butanol in
  2024-11, 2025-02 and 2025-11 was irradiated (as in 2024-09 and 2025-12
  onward). The schedule labels them irradiated d-butanol (Magic +/- of that
  material); they keep their own fitted DNP until scored against the
  McClellan-fitted P.

* Vacuum interlock (question 3): Ells (2026-10-16), the IONIVAC alarm is
  enough; the interlock need not act on the shells' vacuum. So the live
  interlock's vacuum rule (`OVC_PI` >= 1e-3 mbar on TPG gauge 2, a Roots-line
  manometer) has no job left; it should go, with the temperature-dependent
  IONIVAC alarm (questions 25, 39) in its place.

* Q after a quench (question 5): Ells (2026-10-16), no -- Q does not lag; it
  follows the Boltzmann curve Q_B(P) as P rebuilds (as the June 2026 P&Q data
  showed).
* LabVIEW's Lo-range VP temperature (question 40): Ells (2026-10-16), trust
  her offline `t90_torr_to_k` over LabVIEW's online readout (which reads
  ~1.6 mK colder below ~0.86 Torr). The sim computes the bath T90 from the
  manometers with it.
* 2024-09 slow sweep (question 35): Ells (2026-10-16), run the rates fit on
  2024-09 (NIST-irradiated d-butanol); pass on the 2025-02-13 file. Done the
  same day: the 09-17 window was not a sweep (readback glitches, no P/Q
  fits); the real slow sweep is 2024-09-18 10:16-11:56 (up, 3 MHz / 2 min,
  f/B 28.015-28.045, B 4.850 T), fitted on the McClellan freq-hop P. Beta
  peak 28.023 +- 0.001 GHz/T (FWHM ~0.011, half TEMPO's; 0.03 below TEMPO's);
  crossover ~28.031; alpha still on its plateau at 28.045 (no maximum);
  drive amplitude within ~10 % of the rate set. The sim now uses this
  measured lineshape for irradiated d-butanol (x_opt 28.023): hindcast
  2025-12 MAE 7.05 -> 6.89, 2026-06 4.77 -> 4.82 pp. Follow-ups 51-53.
* Magic +/- meaning (question 34, part): Ells (2026-10-16), Magic +/- are the
  mmwave frequency / field ratios (GHz/T) at which each material polarizes
  well positive / negative; the DAQ multiplies them by the present field and
  sets the mmwave output. Ideally they sit at the beta (+) and alpha (-)
  maxima of the rate equations; the sim's values follow that rule. Numbers
  to come (checklist 34).
* Shells-vacuum alarm limits (questions 3 and 25, part): Ells (2026-10-16),
  the IONIVAC is the vacuum of the fridge's shells and very important to
  track: alarm above 1e-4 Torr while the bottom of the magnet is above 300 K,
  above 1e-5 Torr once it is below 80 K. LabVIEW's `Roots Pressure` is the
  top Roots gauge (LabVIEW never read the bottom one).
* ssRF heat (question 10, part): Ells has measurements from the prep for the
  2026-06 cool-down; she will analyze and send them (checklist 10).

* Panopticon magnet neck and M1 (questions 42, 43): Ells (2026-10-16), draw
  the neck baffles to scale and match the drawings as closely as possible
  (some baffles may lie outside the magnet space); M1 sits on the highest
  magnet baffle. Sent to the Panopticon work on dev-visualizer.

* TPG 262 units (question 41): Ells (2026-10-16), yes, the TPG 262 is always
  set to mbar, so the Panopticon's x0.750062 conversion to Torr stands.

* Fridge blueprints vs the installed fridge (question 38): Ells (2026-10-16),
  they should match very closely; the UNH fridge was built from the JLab g2p
  fridge design. The Panopticon's heat-exchanger plates (5 upper, 10 lower)
  stand as drawn.

* Materials per cool-down (question 33): Ells pointed to the cool-downs
  technote (Optimizied-McClellan-Fits docs/cooldowns; dissertation section
  5.6 is the authority):
  * trityl-doped d-propanediol: 2020-09, 2021-09, 2022-05, 2024-08 (08/29-30;
    08/27-28 was TEMPO d-butanol with no deuteron signal);
  * 2020-12: d-propanediol 12/06-12/09 (not in the data files), then
    TEMPO-doped d-butanol 12/10-12/11;
  * d-butanol: 2024-09 (NIST-irradiated), 2024-11 (11/15, after an ND3
    attempt 11/13-14 that did not polarize), 2025-02, 2025-11, 2025-12,
    2026-06, 2026-07;
  * deuterated ammonia (ND3): 2024-12 (first ammonia polarization, ~9 %);
  * 2025-07: unknown, and no DNP was achieved (max online P 0.5 %).
  The premise was wrong for 2024-09: it reached 43.9 % (validated against the
  dissertation). Its online P column was poorly calibrated then (median
  ~+32 % offset from equilibrium, also 2024-11), so the hindcast's "truth"
  there is suspect. Also from the technote: 2020-09 frequency-hopped (09-13
  ~00-03, 139.000 / 139.065 GHz at 1 Hz), and on 2024-11-15 the microwaves
  maxed out, forcing an off-tune run.

* Microwave Sweep / Hop (question 28): Ells (2026-10-16), used only extremely
  rarely; extremely low priority for Genesis. The "slow sweep" (frequency
  moved a set df in a set time) was extremely useful and is MID priority for
  Genesis. Genesis already has it in essence: the microwave supervisor's
  software sweep (`SUP:SweepStart/Stop/Step/Dwell`, on the Microwave screen)
  steps `Step` GHz every `Dwell` s, one pass. Gaps against a LabVIEW-style
  slow sweep: the dwell starts after each frequency command completes, so
  the real rate is a little slower than Step/Dwell; one pass only (no
  repeat or return); entered as step and dwell, not as a rate. See
  COOLDOWN_SIM_PROGRESS.md.

* Serial gateways (question 14): Ells (2026-10-16), 192.168.1.251 is a DIE WU
  TXI040 8-port RS-232/422/485 serial device server with 7 devices plugged in
  (Genesis uses 4: MKS 902B :5004, IONIVAC :5005, LS455 :5007, TPG 262 :5011).
  192.168.1.250 is a DIE WU TXI017 4-port server with 2 devices, none used by
  Genesis. Not a Raspberry Pi. What the other 5 devices are: question 45.

* XCG range (question 13): Ells (2026-10-16), the Manometer-Lo head is an
  XCG-HT-FB-1: 10 Torr full scale, Torr units, 0.5 % of reading class;
  resolution 0.005 Torr (0.05 % F.S.). The maker gives no lower limit; it has
  worked for LHe down to at least 0.9 K (~0.04 Torr). The old DAQ crossed from
  Manometer-Hi to Manometer-Lo at 5 Torr, and the record confirms it (LabVIEW
  ranges Lo/Med use Manometer-Lo, Hi/VHi Manometer-Hi; the Med/Hi switch is at
  Manometer-Hi = 5 Torr). A vapor-pressure temperature of exactly 0.650 K is a
  dropout (Manometer-Lo read 0); the model now skips it. Lowest genuine
  reading in the record: 0.053 Torr, ~0.92 K.

* Q during spin-ups (question 5, mostly): the June 2026 P&Q data put Q on the
  Boltzmann curve to < 0.006 % (Spin-1-Rates-Fit technote: fast d-d flip-flop,
  k_delta >= 0.005 /s). The spin-1 rate model reproduces it; the
  phenomenological fallback's assumed 240 s Q-healing remains only where the
  rate model is not used.
* Materials known from Spin-1-Rates-Fit: 2020-09 and 2020-12 (to 12-09)
  trityl-doped d-propanediol; 2020-12-10/11 TEMPO-doped d-butanol; 2025-12
  and 2026-06 irradiated d-butanol (question 33 still open for the others).

* Microwave drive (question 19): done. History UCA from the logbooks; live
  UCA from the SPD1305X through the vendor curve; replay follows the
  procedure; re-fit at logbook UCA steps (see the changelog).

* T1/T3 in Genesis (question 29; Ells chose dev-sim, 2026-10-15): the LS224
  driver now evaluates Cernox (CCX) Chebyshev curves; the magnet calibration
  file carries stick 2's T1 (C2) and T3 (C4) curves; `ls224_magnet` reads
  them and the archiver records them. On dev-sim only (elong untouched).
  The lab's list had T1's range-2 ZU/ZL transposed (a 3-19 K jump of up to
  14 K); corrected here, and the loader now swaps any reversed pair with a
  warning. Question 30 asks whether LabVIEW used the transposed values.

* Target stick (question 15; Ells, 2026-10-15): stick 2 is believed to be in
  (T1 on C2, T3 on C4, both Cernox; no T2), matching Genesis's magnet
  calibration file. Stick 3's all-zero entry is probably a placeholder.

* Thermometer types (Ells, 2026-10-15): CCS = ceramic carbon sensor, CCCS =
  calibrated CCS, CCX = calibrated Cernox. The lab has Cernox calibrations
  for the target thermometers of each stick version (question 29).

* UCA power curve (question 9; Ells, 2026-10-15): vendor test data for the
  chain after its 2025-10 repair give output vs UCA voltage at 138/140/142 GHz
  (about 100 % to 2 V, 74 % at 2.5 V, 26-36 % at 3 V, a few % at 3.5 V, zero
  above about 3.8 V) and maximum output vs frequency (about 250-335 mW). The
  curve's shape changed with the repairs (the 2018 calibration fell much
  earlier), so only the current curve is used, for live runs; history has no
  usable UCA and is treated as full power. The measured 62 mW reaching the
  helium at full drive already includes the ~6 ft overmodal waveguide and
  mirror between the source and the target.

* Private lab documents (question 21; Ells, 2026-10-15): the documents stay
  private, but facts from them may be made public as part of the code where
  the code needs them (constants, parameters, limits), paraphrased.

* Microwave chain saturation (question 24; Ells, 2026-10-15): saturated from
  about -5 dB on the Vaunix, so 2022 to 2025-07 (-3 to -5 dB) ran at full
  power. The model should not scale drive by the setpoint there.
* Missing instruments (question 26; Ells, 2026-10-15): Genesis has no IOC for
  the magnet power supply, the AMI helium-level meters or the backing-pump
  thermocouples. Priority: the magnet power supply first ("no magnet, no
  cool-down"), then the AMI level meters; the thermocouples are low priority.
  When each gets an IOC, the sim needs an emulator and channel-map rows; the
  magnet supply would also let the sim rehearse field steps (baselines),
  ramps and quench response.

* The cold-cathode gauge (`ColdCathode.Pres`, logged 2021 to 2025-07) was an
  older back-up to the IONIVAC on the shells' vacuum. The device that read it
  is broken and disconnected, hence the empty column from 2025-11 (Ells,
  2026-10-15). In the vacuum rework its history can back up the IONIVAC's.

* TPG 262 (question 2): both gauges are manometers on the Roots pump; gauge 1
  (`Pump`) is the top one, gauge 2 (`OVC`) the bottom one (Ells, 2026-10-15).
* Manometer-Lo is a KJLC XCG carbon capacitance manometer. A black RJ45-style
  cable (power and signals, not Ethernet) joins it to the KJL-SPARC. The
  KJL-SPARC's micro-USB output goes to a USB-A port on the `keysight` Pi
  (192.168.1.24).

* Instrument IPs after the lab power outage (question 6): the lab is setting
  them static and rebooting the equipment (Ells, 2026-10-15). Not tracked
  here; the channel map keeps the configured addresses.

* The cold-board varactors are powered by a Siglent **SPD1305X** (single
  channel), with a sticker reading IP 192.168.1.5 (Ells, 2026-10-15). That is
  the `siglent_coldboard` IOC's address, and its SPD1000X-family driver fits.
  So Genesis has no 4-channel gap: the SPD4306X is not the cold-board supply.
  The sim's coldboard emulator now reports SPD1305X (was SPD1168X).

* The UCA supply is a Siglent SPD1305X (Ells, 2026-10-15), matching the live
  UCA IOC's `*IDN?` check and the sim's emulator.

* Coldboard supply has no past-record column (question 4; Ells, 2026-10-15).
  * Before Genesis the cold board ran from an external DC supply, and its
    output was never recorded.
  * In use it is set between 0 and 19 V. It biases the cold-board varactors,
    so changing it moves the NMR Q-curve's tune: higher voltage, higher
    frequency.
  * The sim still emulates the supply as present with output OFF, and its NMR
    source does not yet move the Q-curve with this voltage.

* The `UCA (V)` column has not been the real UCA for years (analog-output
  drive had problems; a manual DC supply drove it; the SPD1305X took over in
  the last few weeks). SIG is the microwave chain's power monitor (lower
  voltage = more power). The KJL-SPARC speaks a TPG-style serial protocol.
  The AMI level meters and the flow meters are wanted on the Genesis DAQ
  soon; the SMT03 and VME crate are being retired for the JLab NMR (Ells,
  2026-10-15).

* Coldboard load (question 7; Ells, 2026-10-15): no noticeable heating has
  been seen from the varactor bias voltages and currents.
  * **The model neglects it.** `coldboard_heat_W` stays 0 W unless a scenario
    or Sim Control injects heat as a test fault. This is noted in
    `cooldown_sim/model/fit_params.py` (and `model_params.yaml`) and the
    `target.py` docstring.
  * **To add it later:** set `coldboard_heat_W` from the supply's V x I
    read-backs. You would need which channels drive the varactors and at
    roughly what voltages and currents.
  * Still open for the live system: the interlock treats the supply as a
    heater (trip at 120 W): question 17.

* MKS 902B `Cell` = `Manometer-Hi (Torr)`, and the 2020 single-range
  `Manometer (Torr)` (Ells, 2026-10-15).
  * The 902B struggled with helium near 1 K, so a lower-range manometer was
    added from 2021: `Manometer-Lo`, on a Kurt J. Lesker KJL-SPARC
    controller.
  * Genesis had no IOC for the KJL-SPARC until Utsav's `manometer_low`
    (merged into dev-sim 2026-10-19; see question 62).
* No fridge thermal time constants exist. The closed-loop thermal model fits
  them from past microwave on/off steps (`python -m cooldown_sim fit-params`).
* Microwave heat: 62 mW at full drive, measured as a Roots-flow differential
  (logbook 2024-12-17). Boil-off is 59.5 mW/slm. The cup thermometry (T1-T3) is
  far more sensitive to microwaves than the bath.
