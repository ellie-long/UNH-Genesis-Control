# Magnet Lake Shore 224 — provisional T1/C2 calibration

**Baseline:** Genesis Cryo branch `feature/cryo-gui-completeness`, freeze `017b330`.

## Active read-only behavior

- Magnet LS224 (`192.168.1.13:7777`) temperature input **C2 = T1**.
- Operator provisionally assigns **sensor X141509**, model CX-1030-SD-HT-0.3L, Lake Shore certificate report **1078107**, to T1/C2.
- `calibration/ccx_cernox_multiband.csv` contains full curves transcribed from `Thermo_Calibrations (1).ods` (source row **1-T1**) and the other offline reference curves.
- `ccx_sensors: {T1: 1-T1}` is the *only* enabled Cernox assignment. Four Chebyshev ranges are selected by measured resistance. T1 readings outside calibrated resistance or certificate bounds **0.30–325 K**, missing curves, or instrument faults produce **NaN** and an explanatory `_Status`.
- `T1_Status` shows **OK PROVISIONAL X141509** for valid conversion, to distinguish the engineering assumption from a verified serial-to-channel assignment.
- Magnet input **C4 = T3**. **No curve is assigned**, so `T3_TI` intentionally returns **NaN** with **CCX UNASSIGNED (NO TEMP)** (or the instrument fault) rather than an unverified KRDG conversion. This matches the last known physically disconnected sensor.
- Raw `T1_RI` and `T3_RI` remain visible. Other LS224 channels and all other cryogenic device behavior are unchanged.
- There are **no hardware writes**: no instrument curve upload, input changes, actuator commands, extra IOC owners, or changes to `autostart` flags.

## Later sensor remapping

First verify installed sensor serial(s) against the physical C2/C4 wiring. Update the explicit `ccx_sensors`, `ccx_provisional` and `ccx_kelvin_bounds` mapping in `configs/target_physical/ls224_magnet/settings.yaml`; use a *verified* full four-band calibration (not a single warm-region fit), rerun `tests/test_cryo_ccx_provisional.py`, then perform operator-authorized read-only hardware checks. Do not silently fall back to KRDG for a mapped CCX channel.

## Pending live acceptance (not covered by offline tests)

- Confirm physical serial-to-channel mapping and compare T1/C2 `SRDG?` resistance to independent thermometer and certificate points; T3/C4 requires hardware reconnect and curve identification.
- Verify the right-flow Raspberry Pi gateway at `192.168.1.24:5010` and manometer at `192.168.1.24:5030`, and left/separator flow gateway at `192.168.1.251:5012/5013`; IOCs remain optional/non-autostart.
- Validate AMI level probes, pressure units, alarm and reconnect behavior, archive/trend collection, and 13 Phoebus Cryo screens with an authorized operator. GUI navigation/tests alone do **not** establish live hardware acceptance.
