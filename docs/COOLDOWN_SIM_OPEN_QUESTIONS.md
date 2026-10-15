# Simulated cool-down: open questions

Deferred questions for the lab, to be answered before the channel map is
treated as authoritative. Answers go into `cooldown_sim/channel_map.yaml`
(then regenerate `docs/COOLDOWN_SIM_CHANNEL_MAP.md` with
`python -m cooldown_sim channel-map-report --coverage`).

## Channel map (currently marked **guess**)

2. **TPG 262 gauge 1 (`Pump`)**: is this the gauge logged as
   `Roots Pressure (Torr)`?
3. **TPG 262 gauge 2 (`OVC`)**: is this the cold-cathode gauge logged as
   `ColdCathode.Pres (Torr)`? That column is empty since 2025-11. Is the OVC
   gauge now the IONIVAC?
4. **Coldboard supply**: confirm no past record column exists. It is currently
   emulated as present with output OFF.

## Siglent supplies (Ells, 2026-10-15)

What the lab has, per Ells:
* two Lake Shore 224 temperature monitors for the thermometry (Genesis
  `ls224_fridge` and `ls224_magnet`);
* a Siglent **SPD4306X** (4-channel) that may drive the varactor diodes on
  the cold board;
* a newer **single-channel** Siglent (one +/- output) for the microwave UCA.

How Genesis is configured today:
* UCA: `microwave_ioc/spd1305x`, single channel. It refuses to connect
  unless `*IDN?` contains `SPD1305X`.
* Coldboard: `target_ioc/instruments/spd1000x`, written for a single-channel
  SPD1000X (SPD1168X/SPD1305X), with one channel (`Coldboard`) limited to
  30 V and 5 A. The interlock treats it as the one supply putting energy into
  the insert (trip at 120 W).
* Nothing in Genesis supports the SPD4306X or any SPD4000X model.
* The sim emulates the UCA as an SPD1305X and the coldboard as an SPD1168X.

6. **Models.**
   * Is the new single-channel UCA supply an SPD1305X? If not, the live UCA
     IOC refuses it.
   * If the cold board is powered by the SPD4306X, Genesis has no driver for
     it. The current coldboard IOC would at best reach one channel, through
     commands written for a different model. Neither this nor the 4-channel
     PV layout has been checked against the SPD4306X.
   * Ells thinks the SPD4306X is the supply behind the `siglent_coldboard`
     IOC. That driver is a port of Farooq's `spd1000x_lan_gui.py`, so the
     coldboard may have been a single-channel SPD1000X then and swapped since.
     A read-only check on the lab host settles it:
     `caget UNH:GEN:CRYO:COLDBOARD:Coldboard_IDN`.
7. **Coldboard load.** If the cold board's supply biases varactors, it is
   not a heater. Then the "coldboard heat" model input (assumed to heat the
   bath) and the 120 W interlock power limit would both need rethinking.
   Which channels are used, and at roughly what voltages and currents?

## Target model (currently **assumed**)

5. **Q during a spin-up from zero.** After a quench or anneal, does the
   tensor polarization Q follow Q_B(P) = 2 - sqrt(4 - 3P^2) as P rebuilds, or
   does it lag? The model re-heals Q towards Q_B(P) with an assumed 240 s
   time. During the `quench` scenario's re-spin-up, Q then sits about 25 %
   below Q_B(P). The equal-spin-temperature fitter reads P from the line, so it
   reads about 13 % low there. A spin-up from near zero P with fitted P and Q in
   the past data (or the Spin-1-Rates-Fit results) would settle this; the value
   lives in `cooldown_sim/model/fit_params.py` (`Q_heal_tau_s`).

## Settled

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
