# Simulated cool-down: open questions

Everything the sim work is waiting on from the lab. The checklist is the
index; details follow. Answers go into `cooldown_sim/channel_map.yaml` (then
regenerate `docs/COOLDOWN_SIM_CHANNEL_MAP.md` with
`python -m cooldown_sim channel-map-report --coverage`) or the model files
named in each item. Numbers are stable: answered items move to *Settled*.

## Checklist (all open items)

**Equipment and wiring**
- [ ] 49. Models, and Genesis priority: (a) `Magnet_IPS` on the TXI017
      (TCP 5001, 9600 8N2) -- the Oxford IPS120-10? (b) `Magnet_LHE_Prob`
      (TCP 23) -- which level meter (AMI model)? (c) Should the portable
      pumping station's IONIVAC (:5005) get its own Genesis IOC (e.g. a
      second ionivac device, "cart vacuum")? (d) `left_Roots_Pump` (5012) and
      `Separator_Pump` (5013): what are they, and do they belong in Genesis?
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
  * Genesis has no IOC for the KJL-SPARC.
* No fridge thermal time constants exist. The closed-loop thermal model fits
  them from past microwave on/off steps (`python -m cooldown_sim fit-params`).
* Microwave heat: 62 mW at full drive, measured as a Roots-flow differential
  (logbook 2024-12-17). Boil-off is 59.5 mW/slm. The cup thermometry (T1-T3) is
  far more sensitive to microwaves than the bath.
