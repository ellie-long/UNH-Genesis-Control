# Magnet Lake Shore 224 — target-stick 2-T1 / 2-T3 read-only calibration

**Authoritative source:** `calibration/Thermo_Calibrations.ods` (sheet `Thermo_Calibrations`).
This calibration is independent of the X141509 PDF certificate and independent
of any temperature curve installed inside the Lake Shore instrument.

## Operator-confirmed spreadsheet assignments (2026-10-19)

| Genesis PV channel | Lake Shore input | .ods row | CCX curve |
| --- | --- | --- | --- |
| T1 | C4 | 21 | 2-T1 |
| T3 | C2 | 22 | 2-T3 |

Each row has four consecutive (ZU, ZL, A0...A9) groups: D:O, P:AA,
AB:AM, AN:AY. For raw resistance `R` in ohms calculate `Z=log10(R)`;
select a containing [ZL,ZU] group; calculate
`k=((Z-ZL)-(ZU-Z))/(ZU-ZL)`; then
`T = sum(Ai*cos(i*acos(k)) for i in range(10))`.

### Safety and validity

- Conversion reads raw `SRDG?` ohms only; **never uploads calibration curves** to the Lake Shore.
- Lake Shore `RDGST?` bits 16/32 describe an instrument-internal temperature
  range error. These are not sensor-invalid flags and do not veto a usable
  ODS-derived resistance conversion. Status explicitly shows `OK; 224 TEMP RANGE`.
- All remaining/unknown `RDGST?` bits, missing/bad ODS data, resistance outside
  the four calibrated ranges, NaN or non-positive resistance publish NaN.
- No certificate-derived Kelvin bounds are imposed; the ODS Z bands define
  the approved calibration domain. If ODS source identification changes,
  startup fails closed rather than silently selecting another sensor.
- All physical `WRITE_ENABLED` gates remain disabled. No instrument settings,
  safety permits, or controller settings are changed by this patch.
- `T1_TI`, `T3_TI`, `T1_RI`, `T3_RI`, and existing archive/trend PV names stay intact.

### Known read-only live sample before correction

C4=40.049 ohm -> 2-T1: about 297.115 K; C2=47.929 ohm -> 2-T3: about 296.586 K.
These are **offline calculations** from the ODS; physical acceptance requires
reading both channel PVs after a controlled IOC restart and checking status.

### Updating calibration coefficients (no code changes)

1. Replace/edit only `calibration/Thermo_Calibrations.ods` on the Genesis
   Cryo branch. Keep worksheet `Thermo_Calibrations` and the sensor identities
   `2-T1`/`C4` in row 21, `2-T3`/`C2` in row 22. Each sensor uses the four
   consecutive blocks D:O, P:AA, AB:AM, AN:AY (ZU,ZL,A0..A9).
2. Run `python -m pytest -q tests/test_cryo_*.py` and review the ODS file
   diff/version before committing. Check values independently at known
   calibration points when coefficients change.
3. **During an operator-coordinated maintenance window**, restart only the
   magnet Lake Shore 224 IOC and verify both live `_RI`, `_TI`, and `_Status`
   readbacks. There is no automatic hot-reload; the file is loaded on IOC
   startup. Keep all hardware write gates disabled.

`calibration/ccx_cernox_multiband.csv` contains a historical extracted copy;
**it is not used by the configured target-stick IOC**. Do not edit that file
in an attempt to change the live T1/T3 calibration.

## All Lake Shore 224 thermometry from the same ODS

Fridge F1–F11 and magnet M1–M5 now use the **same authoritative ODS file**
`calibration/Thermo_Calibrations.ods` as the target-stick sensors. The fridge
uses spreadsheet rows 7–17 (F1–F10 are CCS, F11 is CCCS); magnet M1–M5 use
rows 2–6 (M1 is CCS2 and M2–M5 are CCS). The 16 conventional sensors use
their row's coefficients from columns D onward; target-stick `2-T1` and
`2-T3` continue to use their four-band Chebyshev calibration in rows 21–22.
The same ODS contains all coefficients; Python source and YAML contain no
calibration numeric constants.

The physical Lake Shore input assignments remain explicit in the two IOC
settings files and must match the `Lakeshore Slot` column. Missing files,
missing/invalid coefficients, unexpected sensor labels or mismatched slots
cause an invalid temperature (NaN), not a silent fallback to the instrument's
`KRDG?` reading. The instrument's KRDG-only temperature-range flags may be
reported as `OK; 224 TEMP RANGE` if raw resistance and calibration are valid;
sensor faults continue to invalidate the reading.

After changing the ODS, run the Cryo regression tests and independently check
known calibration points. Schedule a controlled restart of **both** Lake
Shore 224 IOCs to reload the ODS; unrelated Cryo IOCs need not be restarted.
The previous `calibration/fridge_thermometry.csv` and
`calibration/magnet_thermometry.csv` remain as historical references but are
not consulted by either configured physical Lake Shore IOC.
