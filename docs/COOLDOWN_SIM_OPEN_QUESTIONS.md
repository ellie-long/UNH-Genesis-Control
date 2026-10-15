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

## Settled

* No measured microwave heat-load or fridge thermal time constants exist. The
  closed-loop thermal model fits them from past microwave on/off steps
  (`python -m cooldown_sim fit-params`).
