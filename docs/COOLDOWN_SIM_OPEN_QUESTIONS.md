# Simulated cool-down: open questions

Deferred questions for the lab, to be answered before the channel map is
treated as authoritative. Answers go into `cooldown_sim/channel_map.yaml`
(then regenerate `docs/COOLDOWN_SIM_CHANNEL_MAP.md` with
`python -m cooldown_sim channel-map-report --coverage`).

## Channel map (currently marked **guess**)

1. **MKS 902B `Cell`**: is this the same gauge as the LabVIEW manometer
   (`Manometer-Hi/Lo (Torr)`, range chosen by `Vapor Pressure Range`)?
2. **TPG 262 gauge 1 (`Pump`)**: is this the gauge logged as
   `Roots Pressure (Torr)`?
3. **TPG 262 gauge 2 (`OVC`)**: is this the cold-cathode gauge logged as
   `ColdCathode.Pres (Torr)`? That column is empty since 2025-11. Is the OVC
   gauge now the IONIVAC?
4. **Coldboard supply**: confirm no past record column exists. It is currently
   emulated as present with output OFF.

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

* No fridge thermal time constants exist. The closed-loop thermal model fits
  them from past microwave on/off steps (`python -m cooldown_sim fit-params`).
* Microwave heat: 62 mW at full drive, measured as a Roots-flow differential
  (logbook 2024-12-17). Boil-off is 59.5 mW/slm. The cup thermometry (T1-T3) is
  far more sensitive to microwaves than the bath.
