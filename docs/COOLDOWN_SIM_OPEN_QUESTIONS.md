# Simulated cool-down: open questions

Everything the sim work is waiting on from the lab. The checklist is the
index; details follow. Answers go into `cooldown_sim/channel_map.yaml` (then
regenerate `docs/COOLDOWN_SIM_CHANNEL_MAP.md` with
`python -m cooldown_sim channel-map-report --coverage`) or the model files
named in each item. Numbers are stable: answered items move to *Settled*.

## Checklist (all open items)

**Equipment and wiring**
- [ ] 3. Vacuum: should the interlock watch the IONIVAC, at what trip
      pressure? Is LabVIEW's `Roots Pressure` the top or bottom Roots
      manometer?
- [ ] 13. Manometer-Lo: the XCG gauge's full-scale range.
- [ ] 14. Serial gateway at 192.168.1.251: make and model (possibly a Pi).
- [ ] 15. Which target stick is in use now? Genesis's magnet calibration
      file (T1 on C2, T3 on C4) matches stick 2, which has no T2; stick 1 had
      T2 on C4 and T3 on C3. Is stick 3 (T3 on C3) real?
- [ ] 29. Add T1/T3 to Genesis (live change)? Needs Cernox (CCX) Chebyshev
      support in `target_ioc/instruments/ls224.py`, the stick's coefficients
      in `calibration/magnet_thermometry.csv`, and the channels in the
      `ls224_magnet` config. T1 is the target (lattice) temperature.
- [ ] 12. (Optional) cold-board voltage to Q-curve tune: MHz per volt, or
      the tune at 0 V and 19 V.

**Microwave and ssRF power**
- [ ] 9. SIG's calibration (reflected power); roughly when the manual supply
      replaced the analog-output drive. (UCA curve: settled, see below.)
- [ ] 10. (Parked) ssRF heat vs power at the coil.
- [ ] 28. Were LabVIEW's microwave Sweep (FM between two frequencies) or Hop
      ever used for DNP? The record has no on/off flag; span is always 0 and
      the time always 1 s. Genesis has only a one-pass stepped sweep.

**Target physics**
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
- [ ] 25. Shells-vacuum alarm (live fix): warn on a rapid rise and above
      the 1e-4 Torr range. How rapid counts as rapid?
- [ ] 18. When to schedule the live fixes (findings 1-8 plus the vacuum and
      F1 interlock rules).

**Sim decisions**
- [ ] 19. Microwave-drive fix: IN PROGRESS (vendor UCA curve for the current
      chain; history = full power; replay autopilot lowers the UCA after RF
      ON, as an operator does; re-fit).
- [ ] 20. Go ahead with the vacuum rework under 3 once the interlock gauge is
      chosen (IONIVAC as the shells' vacuum, cold-cathode history as
      fallback)?
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
   * Is LabVIEW's `Roots Pressure (Torr)` the top or the bottom manometer?

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

13. **XCG full-scale range.** Sets the valid range for `Manometer-Lo` and
    when the vapor-pressure readout should switch gauges.
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
    pressure ever gets above the 1e-4 Torr range. Still to set: what rate
    counts as rapid. Whether the vacuum interlock should also act on it is
    question 3.
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
