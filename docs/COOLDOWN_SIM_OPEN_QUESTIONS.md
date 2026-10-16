# Simulated cool-down: open questions

Everything the sim work is waiting on from the lab. The checklist is the
index; details follow. Answers go into `cooldown_sim/channel_map.yaml` (then
regenerate `docs/COOLDOWN_SIM_CHANNEL_MAP.md` with
`python -m cooldown_sim channel-map-report --coverage`) or the model files
named in each item. Numbers are stable: answered items move to *Settled*.

## Checklist (all open items)

**Equipment and wiring**
- [ ] 3. Vacuum: beyond the IONIVAC alarm (limits now set, see 25), should
      the interlock also ACT on the IONIVAC (trip something: what?), or is
      the alarm enough? (LabVIEW's `Roots Pressure` = the top gauge: settled.)
- [ ] 14. Serial gateway at 192.168.1.251: make and model (possibly a Pi).
- [ ] 12. (Optional) cold-board voltage to Q-curve tune: MHz per volt, or
      the tune at 0 V and 19 V.

**Microwave and ssRF power**
- [ ] 9. SIG's calibration (reflected power); roughly when the manual supply
      replaced the analog-output drive. (UCA curve: settled, see below.)
- [ ] 10. (Parked) ssRF heat vs power at the coil. **REMINDER for Ells:**
      send the ssRF heat data taken in the prep for the 2026-06 cool-down,
      once you have analyzed it (2026-10-16).
- [ ] 28. Were LabVIEW's microwave Sweep (FM between two frequencies) or Hop
      ever used for DNP? The record has no on/off flag; span is always 0 and
      the time always 1 s. Genesis has only a one-pass stepped sweep.

**Target physics**
- [ ] 33. Which target material (and radical) was in each cool-down, and
      when were targets swapped? The fitted per-cool-down P_max and spin-up
      times differ a lot (e.g. 2024-09 reached only ~9 % where 2025-12 reached
      ~50 %); knowing the materials would show whether that is the material,
      the microwave power, or the NMR calibration.
- [ ] 34. Magic - : what constant did the LabVIEW DAQ use for "Magic -" (and is
      Magic + exactly 28.025 GHz/T)? The sim uses 28.077 GHz/T for Magic -
      (the P-down peak of the measured lineshape).
- [ ] 35. Slow microwave sweeps on irradiated d-butanol for an alpha/beta fit:
      candidates in the record are 2024-09-17 11:36-16:31 (f/B 27.87-28.09),
      2025-11-20 22:17-23:17 and 2025-02-13 19:40-20:39 (narrow). Which
      material was in 2024-09? (The rates fit hasn't been run on these.)
- [ ] 36. Genesis Magic +/- (live): the microwave supervisor ALREADY has
      Magic +/- (`SUP:MagicPlus`/`MagicMinus`), disabled because its dopant
      list is empty (`dopants: []` -> Dopant "UNCONFIGURED"). It computes
      f = CentralFrequency / gamma_d_over_2pi x magic factor per dopant, i.e.
      field x factor, with the field from the NMR centre frequency (no magnet
      IOC needed). But `CentralFrequency` is declared in GHz and defaults to
      140 GHz (the OUTPUT band's middle), which only fits the formula if it
      is meant to be the NMR centre in MHz. Which is intended? To certify:
      per dopant, gamma_d/2pi = 6.53566 MHz/T and the +/- factors (sim
      values: irradiated d-butanol 28.025 / 28.077, TEMPO d-butanol
      28.052 / 28.108, trityl d-propanediol 28.026 / 28.041 GHz/T).
- [ ] 37. 2020-12-10/11 (TEMPO d-butanol at the beta peak, f/B 28.0505,
      1.05 K): the record's area P reaches ~35 % within an hour, but the
      TEMPO rate fit (stage 5, fitted to that night's lineshape-derived P)
      saturates near 25 % at the same conditions. Does the LabVIEW area P read
      high against the lineshape P there (calibration), or is the fit's drive
      low?
- [ ] 38. Panopticon (dev-visualizer): the heat-exchanger plates are drawn from
      the fridge blueprints -- 5 upper plates (1.94" spacers) and 10 lower
      plates (0.75" spacers), from the 2011 JLab Hall A g2p refrigerator
      assembly (lower-stack spacer drawing revised 2015). Do these drawings
      match the fridge as installed at UNH?
- [ ] 5. Does Q lag Q_B(P) as P rebuilds after a quench or anneal?
- [ ] 8. Quench field: dump time and level, re-ramp delay and rate, starting
      P after the re-ramp.

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
- [ ] 25. Shells-vacuum alarm (live fix): limits set (bottom of the magnet
      > 300 K: alarm above 1e-4 Torr; < 80 K: above 1e-5 Torr). Still open:
      how rapid a rise counts as rapid?
- [ ] 40. (Low priority) LabVIEW's vapor-pressure temperature in its "Lo"
      range (Manometer-Lo, below ~0.86 Torr) reads ~1.6 mK colder than
      Ells's `t90_torr_to_k` (ITS-90 above 1.25 K, Donnelly & Barenghi below),
      nearly constant from 0.1 to 0.9 Torr (2025-12, 2026-06); Med, Hi and VHi
      match it to < 0.1 mK. A constant fraction like this looks like a ~1 %
      pressure scale or an older temperature scale in LabVIEW's Lo branch.
      Known? (Only matters if Genesis's VP readout must reproduce LabVIEW.)
      Ells (2026-10-16): trust her offline code over the online readout; the
      sim now computes the bath T90 from the manometers with it.
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
- [ ] 20. Go ahead with the vacuum rework under 3 once the interlock gauge is
      chosen (IONIVAC as the shells' vacuum, cold-cathode history as
      fallback)?
- [ ] 30. Did LabVIEW read T1 with the transposed range-2 limits (stick 2)?
      If so, recorded T1 between about 3 and 19 K is wrong; at 1 K it is
      unaffected (range 1).
- [ ] 22. Try an interactive rehearsal (guide section 2) in Phoebus.
- [ ] 23. Push `dev-sim` and merge into `elong`? Before any push: squash
      `642e0f3` into `d6a9045` (an early wording of question 10).

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

14. **Serial gateway.** The box at 192.168.1.251 carrying the four RS-232
    instruments (TCP ports 5004/5005/5007/5011). Ells thinks it may be a
    Raspberry Pi; it is neither the `keysight` Pi nor `mmWaveRobot.local`.
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
  * Genesis has no IOC for the KJL-SPARC.
* No fridge thermal time constants exist. The closed-loop thermal model fits
  them from past microwave on/off steps (`python -m cooldown_sim fit-params`).
* Microwave heat: 62 mW at full drive, measured as a Roots-flow differential
  (logbook 2024-12-17). Boil-off is 59.5 mW/slm. The cup thermometry (T1-T3) is
  far more sensitive to microwaves than the bath.
