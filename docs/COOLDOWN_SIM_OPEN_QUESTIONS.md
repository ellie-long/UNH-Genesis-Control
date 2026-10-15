# Simulated cool-down: open questions

Deferred questions for the lab, to be answered before the channel map is
treated as authoritative. Answers go into `cooldown_sim/channel_map.yaml`
(then regenerate `docs/COOLDOWN_SIM_CHANNEL_MAP.md` with
`python -m cooldown_sim channel-map-report --coverage`).

## Channel map (currently marked **guess**)

3. **Vacuum: what should the interlock watch?** Ells (2026-10-15): both
   TPG 262 gauges are manometers on the Roots pump (gauge 1 `Pump` = top,
   gauge 2 `OVC` = bottom), and the IONIVAC measures the shells' insulating
   vacuum. So Genesis's "OVC" is a Roots-line gauge (see Findings). Before the
   sim's vacuum handling is reworked:
   * Should the vacuum interlock watch the IONIVAC (`ION:Ion_PI`) instead,
     and at what trip pressure?
   * Is LabVIEW's `Roots Pressure (Torr)` the top or the bottom manometer?
   * What was the cold-cathode gauge (`ColdCathode.Pres`, logged 2021 to
     2025-07), and does it still exist?

## Microwave power

9. **UCA voltage to microwave power.** Ells (2026-10-15): `MMwaves Power (dB)`
   is the Vaunix setpoint (default 10 dBm, rarely changed); the UCA sets the
   actual power. The model gets this wrong:
   * Its drive is `RF_on * 10**((P_dB - 10)/10)`, built on the Vaunix setpoint.
     The UCA is never used.
   * The record has the Vaunix at -3 to -5 dB from 2022 to 2025-07, so the
     model reads those cool-downs as about 4 % power. Two of the 13 fitted
     thermal transitions (2022-05) carry drive steps of 0.05 for that reason.
   * The UCA is often between 0 and 5 V with RF on (64 % of RF-on rows in
     2022-05, 93 % in 2024-12), so intermediate power is real.
   * The UCA column is empty from 2025-11 on.

   Needed: the UCA's power-vs-voltage curve (0 V = full, 5 V = none; linear
   in volts, in dB, or a datasheet curve?). Does the Vaunix setpoint matter
   at all, given the multiplier chain? When the UCA was not recorded
   (2025-11 on), was it normally at 0 V while polarizing?

## Target model (currently **assumed**)

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
   * How is it brought back: re-ramp rate or time to full field, and roughly
     how long after a quench before ramping starts?
   * After the re-ramp, does the target start from P = 0 (or thermal
     equilibrium) at full field?

## Settled

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
    heater (trip at 120 W). Whether that rule suits varactor bias depends on
    question 6.

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
