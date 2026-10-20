# Genesis Master Archiver — PV Catalog

This document maps the `value` column of each registered master
archiver PV to its meaning, engineering unit, and signal category.

**Registry:** 369 configured PVs, from the Genesis master registry.

**Important distinctions:**

- `Config DESC`: description taken from the device's configuration.
- `Known device mapping`: meaning established from the Genesis
  Cryo device setup and recorded observations.
- `Lake Shore 224 driver`: meaning and units established from `target_ioc/instruments/ls224.py`.
- `Fitter engine + EPICS mapping`: meaning and units established from `online_fitter/engine.py` and `online_fitter/epics_run.py`.
- `Name-derived; verify`: interpretation based on PV naming and known subsystem conventions; not yet confirmed against the implementing driver or hardware documentation.
- `Needs verification`: no authoritative description was identified.
- A PV being registered does not guarantee it was connected during
  the recording.
- An engineering unit shown as `—` is unspecified, not necessarily
  dimensionless.
- Command and status PVs are not physical measurements.

See [Master Archiver File Format](MASTER_ARCHIVER_FILE_FORMAT.md)
for timestamps, `value`, `quality`, `age_s`, and other CSV columns.

## Catalog


## Cryo — 168 PVs

| EPICS PV | Meaning / function | Unit | Type | Evidence |
|---|---|---|---|---|
| `UNH:GEN:CRYO:ALARM:Alarm_Count` | Cryo PVs in MINOR or worse | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:ALARM:Alarm_First` | First PV at worst severity | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:ALARM:Alarm_Latch` | Worst severity since acknowledge | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:ALARM:Alarm_LatchAt` | Time alarm latch was raised | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:ALARM:Alarm_LatchPV` | PV which raised alarm latch | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:ALARM:Alarm_Major` | Cryo PVs in MAJOR or INVALID | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:ALARM:Alarm_Missing` | Number of unreadable Cryo PVs | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:ALARM:Alarm_Scanned` | Number of Cryo PVs scanned | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:ALARM:Alarm_Worst` | Worst current Cryo severity | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:ALARM:MAN:alarm_summary_time` | IOC heartbeat/time record for alarm summary; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:CELL:COMM` | Cryo CELL communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:CELL:Cell_IDN` | MKS 902B device type and serial | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:CELL:Cell_PI` | MKS 902B piezo pressure | Torr | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:CELL:Cell_Status` | MKS 902B reply status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:CELL:MAN:mks902b_time` | IOC heartbeat/time record for mks902b; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:COLDBOARD:COMM` | Cryo COLDBOARD communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:COLDBOARD:Coldboard_CC` | Cold board supply current limit | A | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:COLDBOARD:Coldboard_CI` | Cold board supply measured current | A | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:COLDBOARD:Coldboard_CVCC` | Cold board supply regulation mode | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:COLDBOARD:Coldboard_Conn` | Coldboard instrument connection indicator | — | Status | Name-derived; verify |
| `UNH:GEN:CRYO:COLDBOARD:Coldboard_IDN` | Cold board supply identification | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:COLDBOARD:Coldboard_Mode` | Cold board supply output enable | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:COLDBOARD:Coldboard_PI` | Cold board supply measured power | W | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:COLDBOARD:Coldboard_VC` | Cold board supply voltage setpoint | V | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:COLDBOARD:Coldboard_VI` | Cold board supply measured voltage | V | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:COLDBOARD:MAN:siglent_coldboard_time` | IOC heartbeat/time record for siglent coldboard; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:FIELD:COMM` | Cryo FIELD communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:FIELD:Fringe_Field` | Fringe field, instrument unit | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FIELD:Fringe_FieldT` | Fringe field in tesla | T | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FIELD:Fringe_IDN` | Lake Shore 455 identification | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FIELD:Fringe_Unit` | Gaussmeter display unit | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FIELD:MAN:ls455_time` | IOC heartbeat/time record for ls455; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:FLOW:LEFT:COMM` | Cryo FLOW / LEFT communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:FLOW:LEFT:LPump_Raw` | Left pump flow, as reported | slm | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FLOW:LEFT:LPump_SLM` | Left pump flow, corrected | slm | Flow measurement | Config DESC |
| `UNH:GEN:CRYO:FLOW:LEFT:LPump_Status` | Left pump flowmeter status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:FLOW:LEFT:MAN:flowmeter_left_time` | IOC heartbeat/time record for flowmeter left; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:FLOW:RIGHT:COMM` | Cryo FLOW / RIGHT communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:FLOW:RIGHT:MAN:flowmeter_pi_time` | IOC heartbeat/time record for flowmeter pi; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:FLOW:RIGHT:RPump_Raw` | Right pump flow, as reported | slm | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM` | Right pump flow, corrected | slm | Flow measurement | Config DESC |
| `UNH:GEN:CRYO:FLOW:RIGHT:RPump_Status` | Right pump flowmeter status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:FLOW:SEP:COMM` | Cryo FLOW / SEP communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:FLOW:SEP:MAN:flowmeter_sep_time` | IOC heartbeat/time record for flowmeter sep; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:FLOW:SEP:SepFlow_Raw` | Separate flowmeter, as reported | slm | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FLOW:SEP:SepFlow_SLM` | Separate flowmeter, corrected | slm | Flow measurement | Config DESC |
| `UNH:GEN:CRYO:FLOW:SEP:SepFlow_Status` | Separate flowmeter status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:COMM` | Cryo FRIDGE communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:FRIDGE:F10_RI` | Fridge F10 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F10_Status` | Fridge F10 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F10_TI` | Fridge F10 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F11_RI` | Fridge F11 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F11_Status` | Fridge F11 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F11_TI` | Fridge F11 temperature (CCCS sensor) | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F1_RI` | Fridge F1 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F1_Status` | Fridge F1 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F1_TI` | Fridge F1 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F2_RI` | Fridge F2 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F2_Status` | Fridge F2 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F2_TI` | Fridge F2 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F3_RI` | Fridge F3 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F3_Status` | Fridge F3 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F3_TI` | Fridge F3 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F4_RI` | Fridge F4 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F4_Status` | Fridge F4 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F4_TI` | Fridge F4 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F5_RI` | Fridge F5 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F5_Status` | Fridge F5 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F5_TI` | Fridge F5 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F6_RI` | Fridge F6 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F6_Status` | Fridge F6 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F6_TI` | Fridge F6 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F7_RI` | Fridge F7 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F7_Status` | Fridge F7 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F7_TI` | Fridge F7 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F8_RI` | Fridge F8 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F8_Status` | Fridge F8 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F8_TI` | Fridge F8 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:F9_RI` | Fridge F9 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F9_Status` | Fridge F9 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:FRIDGE:F9_TI` | Fridge F9 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:LHE:COMM` | Cryo FRIDGE / LHE communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:FRIDGE:LHE:Fridge_IDN` | AMI 1700 identity | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:LHE:Fridge_LI` | Fridge space LHe level | inch | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:LHE:Fridge_Raw` | Fridge level, as reported | inch | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:LHE:Fridge_Status` | Fridge level probe status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:FRIDGE:LHE:MAN:lhe_fridge_time` | IOC heartbeat/time record for lhe fridge; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:FRIDGE:MAN:ls224_fridge_time` | IOC heartbeat/time record for ls224 fridge; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:ION:COMM` | Cryo ION communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:ION:Ion_PI` | IONIVAC combination gauge pressure | Torr | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:ION:Ion_Raw` | IONIVAC raw telegram count | counts | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:ION:MAN:ionivac_time` | IOC heartbeat/time record for ionivac; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:ION:PUMP:COMM` | Cryo ION / PUMP communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:ION:PUMP:Ion_PI` | Pump-cart Ionivac pressure | Torr | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:ION:PUMP:Ion_Raw` | IONIVAC raw telegram count | counts | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:ION:PUMP:MAN:ionivac_pump_time` | IOC heartbeat/time record for ionivac pump; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:MAG:COMM` | Cryo MAG communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:MAG:LHE:COMM` | Cryo MAG / LHE communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:MAG:LHE:MAN:lhe_magnet_time` | IOC heartbeat/time record for lhe magnet; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:MAG:LHE:Magnet_LI` | Magnet space LHe level | inch | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:MAG:LHE:Magnet_Raw` | Magnet level, as reported | cm | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:MAG:LHE:Magnet_Status` | Magnet level probe status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:MAG:M1_RI` | Magnet-side M1 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M1_Status` | Magnet-side M1 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M1_TI` | Magnet M1 temperature (CCS2 sensor) | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:MAG:M2_RI` | Magnet-side M2 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M2_Status` | Magnet-side M2 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M2_TI` | Magnet M2 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:MAG:M3_RI` | Magnet-side M3 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M3_Status` | Magnet-side M3 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M3_TI` | Magnet M3 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:MAG:M4_RI` | Magnet-side M4 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M4_Status` | Magnet-side M4 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M4_TI` | Magnet M4 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:MAG:M5_RI` | Magnet-side M5 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M5_Status` | Magnet-side M5 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:M5_TI` | Magnet M5 temperature | K | Temperature indication | Config DESC |
| `UNH:GEN:CRYO:MAG:MAN:ls224_magnet_time` | IOC heartbeat/time record for ls224 magnet; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:MAG:T1_RI` | Magnet-side T1 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:T1_Status` | Magnet-side T1 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:T1_TI` | Magnet-side T1 temperature (target-stick ODS-calibrated) | K | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:T3_RI` | Magnet-side T3 raw sensor resistance | ohm | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:T3_Status` | Magnet-side T3 sensor reading validity/status | text | Diagnostic status | Lake Shore 224 driver |
| `UNH:GEN:CRYO:MAG:T3_TI` | Magnet-side T3 temperature (target-stick ODS-calibrated) | K | Physical measurement | Lake Shore 224 driver |
| `UNH:GEN:CRYO:SAFE:CB_Permit` | Safe to power the cold board | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:Data_Permit` | Readings behind permits are fresh | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:MAN:safety_interlock_time` | IOC heartbeat/time record for safety interlock; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:SAFE:Safe_Enforce` | Enforce or report-only mode | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:Safe_Fault` | Latched Cryo safety trip | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:Safe_Limits` | Safety-limit configuration state | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:Safe_Permit` | All Cryo safety permits | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:Safe_Reason` | First unsafe condition | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:Safe_Stale` | Watched readings not updating | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:Safe_State` | Cryo safety interlock state | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:Safe_Trips` | Safety trips since IOC start | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAFE:Vac_Permit` | Vacuum good enough to add heat | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAVE:MAN:save_restore_time` | IOC heartbeat/time record for save restore; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:SAVE:SR_Auto` | Snapshot automatically | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAVE:SR_Count` | PVs saved or restored | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAVE:SR_Failed` | PVs that could not be read or written | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAVE:SR_File` | Snapshot to restore | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAVE:SR_Last` | Time of last snapshot | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAVE:SR_Restore_Enables` | Permit restoring enable states | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:SAVE:SR_Status` | Result of last save or restore | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:STATE:MAN:target_status_time` | IOC heartbeat/time record for target status; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:STATE:auto` | Allow automatic state advance | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:STATE:conditions` | All selected-state conditions met | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:STATE:material` | Target material | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:STATE:production` | State actually satisfied now | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:STATE:state_applied` | Last target-state page applied | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:STATE:status` | Intended target state | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:STATE:unmet` | First condition not met | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:VAC:COMM` | Cryo VAC communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:VAC:LOW:COMM` | Cryo VAC / LOW communications status | — | Connectivity status | Name-derived; verify |
| `UNH:GEN:CRYO:VAC:LOW:MAN:manometer_low_time` | IOC heartbeat/time record for manometer low; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:VAC:LOW:Mano_PI` | Manometer Low pressure | Torr | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:VAC:LOW:Mano_Raw` | Manometer Low raw line | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:VAC:LOW:Mano_Status` | Manometer Low line status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:VAC:LOW:Mano_VapPhase` | He-II / He-I, from the lambda point | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:VAC:LOW:Mano_VapStatus` | Vapor pressure conversion status | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:VAC:LOW:Mano_VapT` | Vapor pressure temperature (ITS-90) | K | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:VAC:MAN:tpg262_time` | IOC heartbeat/time record for tpg262; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:CRYO:VAC:OVC_PI` | TPG 262 gauge 2 pressure | Torr | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:VAC:OVC_Status` | TPG 262 gauge 2 sensor status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:VAC:Pump_IDN` | TPG 262 sensor types | — | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:VAC:Pump_PI` | TPG 262 gauge 1 pressure | Torr | Signal / verify | Config DESC |
| `UNH:GEN:CRYO:VAC:Pump_Status` | TPG 262 gauge 1 sensor status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:CRYO:VAC:Pump_Unit` | TPG 262 display unit | — | Signal / verify | Config DESC |

## ssRF — 97 PVs

| EPICS PV | Meaning / function | Unit | Type | Evidence |
|---|---|---|---|---|
| `UNH:GEN:SSRF:FIT:CALIBRATED` | NMR fitter calibration-state indicator | — | Status | Name-derived; verify |
| `UNH:GEN:SSRF:FIT:FITTER_UP` | NMR fitter availability indicator | — | Status | Name-derived; verify |
| `UNH:GEN:SSRF:FIT:LAST_OK` | Most recent fitter operation success indicator | — | Status | Name-derived; verify |
| `UNH:GEN:SSRF:FIT:LAST_TS` | Timestamp of most recent fitter result | — | Timestamp | Name-derived; verify |
| `UNH:GEN:SSRF:FIT:MATERIAL_RBV` | Fitter selected-material readback | — | Readback | Name-derived; verify |
| `UNH:GEN:SSRF:FIT:MODE_RBV` | Fitter operating-mode readback | — | Readback | Name-derived; verify |
| `UNH:GEN:SSRF:FIT:PUBLISHER_CONNECTED` | Fitter input publisher connectivity | — | Status | Name-derived; verify |
| `UNH:GEN:SSRF:FIT:P_AREA` | Area-method vector polarization from the calibrated NMR signal | % | Derived measurement | Fitter engine + EPICS mapping |
| `UNH:GEN:SSRF:FIT:P_FIT` | Vector polarization calculated from the fitted NMR line shape | % | Derived measurement | Fitter engine + EPICS mapping |
| `UNH:GEN:SSRF:FIT:Q_EQ` | Equilibrium tensor polarization derived from area-method P | % | Derived measurement | Fitter engine + EPICS mapping |
| `UNH:GEN:SSRF:FIT:Q_FIT` | Tensor polarization calculated from the fitted NMR line shape | % | Derived measurement | Fitter engine + EPICS mapping |
| `UNH:GEN:SSRF:FIT:RED_CHISQ` | Reduced chi-square goodness-of-fit statistic | dimensionless | Fit diagnostic | Fitter engine + EPICS mapping |
| `UNH:GEN:SSRF:GEN:COMM` | SMC100A communication state | — | Connectivity / control status | Config DESC |
| `UNH:GEN:SSRF:GEN:ERROR` | Current SMC100A IOC or instrument fault | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:GEN:FM_DEV_RBV` | FM deviation readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:FM_RBV` | FM enable readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:FM_SOURCE_RBV` | FM source readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:FREQ_RBV` | RF carrier frequency readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:LEVEL_RBV` | RF output level readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:RF_RBV` | RF output state readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV` | Power sweep arm state | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:SWEEP_DWELL_RBV` | Power sweep dwell readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:SWEEP_SHAPE_RBV` | Power sweep shape readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:SWEEP_START_RBV` | Power sweep start level readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:SWEEP_STEP_RBV` | Power sweep step readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:SWEEP_STOP_RBV` | Power sweep stop level readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:GEN:WRITE_ENABLED` | SMC100A hardware write permission | — | Connectivity / control status | Config DESC |
| `UNH:GEN:SSRF:ILK:INPUTS_OK` | Required interlock inputs valid | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:INTERLOCK_OK` | ssRF interlock safe state | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:NMR_CONNECT_PERMIT` | Permit to connect NMR path | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:NMR_PATH_OFF` | NMR path confirmed disconnected | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:REASON` | Current interlock decision reason | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:RF_ENABLE_PERMIT` | Permit to enable SMC RF | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:RF_OFF` | SMC RF output confirmed off | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:SSRF_CONNECT_PERMIT` | Permit to connect ssRF path | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:SSRF_PATH_OFF` | ssRF path confirmed disconnected | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:SSRF_PATH_ON` | ssRF path confirmed connected | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:ILK:STATE` | Current ssRF interlock state | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:NMR_SW:COMM` | NMR switch communication state | — | Connectivity / control status | Config DESC |
| `UNH:GEN:SSRF:NMR_SW:ERROR` | Current NMR switch fault | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:NMR_SW:MASK_RBV` | NMR switch SWPORT mask | — | Readback | Config DESC |
| `UNH:GEN:SSRF:NMR_SW:STATE_RBV` | NMR switch confirmed route | — | Readback | Config DESC |
| `UNH:GEN:SSRF:NMR_SW:WRITE_ENABLED` | NMR switch write permission | — | Connectivity / control status | Config DESC |
| `UNH:GEN:SSRF:RECIPE:ARCHIVE_STATUS` | JSON and session archive status | — | Status / diagnostic | Config DESC |
| `UNH:GEN:SSRF:RECIPE:CURRENT_POLARIZATION` | Latest fitted vector polarization | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RECIPE:FITTER_CONNECTED` | Fitter publisher connection | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RECIPE:FIT_AGE_S` | Age of latest fitter result | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RECIPE:FIT_TYPE` | Latest fitter analysis mode | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RECIPE:LATEST_FIT_VALID` | Latest calibrated fit usable | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RECIPE:MATERIAL` | Material from latest fitter result | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RECIPE:OBJECTIVE_RBV` | Selected burn objective | — | Readback | Config DESC |
| `UNH:GEN:SSRF:RECIPE:PLAN_VALID` | Recipe plan generated successfully | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RECIPE:REFERENCE_POLARIZATION` | Calibration reference polarization | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RECIPE:SESSION_ID` | Current correlated burn session | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RECIPE:STATE` | Burn recipe workflow state | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RF_SW:COMM` | ssRF switch communication state | — | Connectivity / control status | Config DESC |
| `UNH:GEN:SSRF:RF_SW:ERROR` | Current ssRF switch fault | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:RF_SW:MASK_RBV` | ssRF switch SWPORT mask | — | Readback | Config DESC |
| `UNH:GEN:SSRF:RF_SW:STATE_RBV` | ssRF switch confirmed route | — | Readback | Config DESC |
| `UNH:GEN:SSRF:RF_SW:WRITE_ENABLED` | ssRF switch write permission | — | Connectivity / control status | Config DESC |
| `UNH:GEN:SSRF:SEQ:CARRIER_MHZ` | SMC carrier frequency | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:EXECUTION_ARMED` | One-shot runtime execution arm | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:EXECUTION_ENABLED` | ssRF execution layer enabled state | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:EXECUTION_REASON` | ssRF execution layer status reason | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:EXECUTION_STATE` | ssRF execution layer state | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:FM_DEV_KHZ` | SMC external FM deviation | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:FM_PERIOD_S` | Keysight waveform period | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:FM_REPEATS` | FM waveform repeats per burn | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:MANUAL_LEVEL_DBM` | SMC plain manual RF level | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:PARAMETERS_OK` | Burn parameters valid | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:PREPARED` | Source prepared and waveform loaded | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:PREPARED_WAVEFORM_ID` | Loaded waveform identity | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:PREPARE_STATE` | Source preparation state | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:READY` | Burn readiness status | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:REASON` | Sequencer burn readiness reason | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:START_LEVEL_DBM` | SMC sweep start level | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:STATE` | Sequencer burn readiness state | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:SWEEP_DWELL_MS` | Derived SMC sweep dwell | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:SWEEP_STEP_DB` | Derived SMC sweep step | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:TARGET_LEVEL_DBM` | SMC target RF level | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:VHIGH_V` | Keysight waveform high voltage | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:VLOW_V` | Keysight waveform low voltage | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:WAVEFORM_STAGE_ID` | Verified sequencer staging counter | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:WAVEFORM_STAGE_POINTS` | Points in most recent staging request | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:SEQ:WAVEFORM_STAGE_STATE` | Sequencer staging state | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:WGEN:ARB_POINTS_RBV` | Instrument-confirmed ARB point count | — | Readback | Config DESC |
| `UNH:GEN:SSRF:WGEN:COMM` | Keysight WaveGen communication state | — | Connectivity / control status | Config DESC |
| `UNH:GEN:SSRF:WGEN:ERROR` | Current Keysight WaveGen fault | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:WGEN:OUTPUT_RBV` | WaveGen output state readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:WGEN:PERIOD_RBV` | FM waveform period readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:WGEN:VHIGH_RBV` | WaveGen high voltage readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:WGEN:VLOW_RBV` | WaveGen low voltage readback | — | Readback | Config DESC |
| `UNH:GEN:SSRF:WGEN:WAVEFORM_ID` | Successful waveform upload counter | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:WGEN:WAVEFORM_POINTS` | Number of staged waveform points | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:WGEN:WAVEFORM_READY` | Verified waveform upload state | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:WGEN:WAVEFORM_STAGE_ID` | IOC-memory waveform staging counter | — | Signal / verify | Config DESC |
| `UNH:GEN:SSRF:WGEN:WRITE_ENABLED` | WaveGen hardware write permission | — | Connectivity / control status | Config DESC |

## Microwave — 104 PVs

| EPICS PV | Meaning / function | Unit | Type | Evidence |
|---|---|---|---|---|
| `UNH:GEN:MW:PS:COMM` | Instrument communication status | — | Connectivity / control status | Config DESC |
| `UNH:GEN:MW:PS:ERROR` | Last IOC or instrument error | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:PS:IMEAS` | Measured output current | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:PS:ISET_CMD` | Current limit command | — | Command / setpoint | Config DESC |
| `UNH:GEN:MW:PS:ISET_RBV` | Current limit readback | — | Readback | Config DESC |
| `UNH:GEN:MW:PS:MAN:spd1305x_time` | IOC heartbeat/time record for spd1305x; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:MW:PS:MODE_RBV` | CV or CC mode readback | — | Readback | Config DESC |
| `UNH:GEN:MW:PS:OCP_CMD` | Overcurrent protection command | — | Command / setpoint | Config DESC |
| `UNH:GEN:MW:PS:OCP_RBV` | Overcurrent protection readback | — | Readback | Config DESC |
| `UNH:GEN:MW:PS:OUTPUT_CMD` | Output enable command | — | Command / setpoint | Config DESC |
| `UNH:GEN:MW:PS:OUTPUT_RBV` | Output state readback | — | Readback | Config DESC |
| `UNH:GEN:MW:PS:OVP_CMD` | Overvoltage protection command | — | Command / setpoint | Config DESC |
| `UNH:GEN:MW:PS:OVP_RBV` | Overvoltage protection readback | — | Readback | Config DESC |
| `UNH:GEN:MW:PS:PMEAS` | Measured output power | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:PS:VMEAS` | Measured output voltage | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:PS:VSET_CMD` | Voltage setpoint command | — | Command / setpoint | Config DESC |
| `UNH:GEN:MW:PS:VSET_RBV` | Voltage setpoint readback | — | Readback | Config DESC |
| `UNH:GEN:MW:ROBOT:COMM_STATUS` | PCL602 shim communication | — | Status / diagnostic | Config DESC |
| `UNH:GEN:MW:ROBOT:CONTROLLER_ERROR` | PCL602 controller error code | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:CONTROL_MODE` | Disabled EPICS or Joystick control | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:ESTOP` | Software stop-all action | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:LAST_ERROR` | Last robot IOC error | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:MAN:pcl602_time` | IOC heartbeat/time record for pcl602; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:MW:ROBOT:SIMULATION` | Genesis robot simulation state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:SPEED_MODE` | Robot speed profile | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:X_BUSY` | X motion state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:X_CM` | X user position | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:X_MACHINE_CM` | X machine position | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:X_MOTOR_ENABLE` | X motor enable | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:X_POSITION_FRESH` | X position freshness | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:X_REFERENCED` | X reference state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:X_SOFT_LIMITS_ENABLED` | X software travel-limit state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:Y_BUSY` | Y motion state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:Y_CM` | Y user position | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:Y_MACHINE_CM` | Y machine position | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:Y_MOTOR_ENABLE` | Y motor enable | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:Y_POSITION_FRESH` | Y position freshness | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:Y_REFERENCED` | Y reference state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:ROBOT:Y_SOFT_LIMITS_ENABLED` | Y software travel-limit state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MAN:vaunix_lms_time` | IOC heartbeat/time record for vaunix lms; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:MW:SRC:MW_CONTROL_OWNED` | Exclusive remote control lease owned | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_CONTROL_SESSION` | Safe control acquisition generation | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_Connected` | Vaunix shim communication status | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_FC` | Synthesizer frequency command | GHz | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_FI` | Synthesizer frequency readback | GHz | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_Lock` | PLL lock status | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_OutFI` | Microwave output frequency readback | GHz | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_PWRC` | Source power command | dBm | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_PWRI` | Source power readback | dBm | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_RF` | Legacy OFF-only command; not readback | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_RF_CMD` | Supervised RF command | — | Command / setpoint | Config DESC |
| `UNH:GEN:MW:SRC:MW_RF_DONE_TIME` | Last RF command completion time | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_RF_ERROR` | RF command or confirmation error | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_RF_RBV` | Vendor-derived RF actual state | — | Readback | Config DESC |
| `UNH:GEN:MW:SRC:MW_RF_READY` | Safe baseline verified for RF control | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_RF_STATE` | UNKNOWN OFF or ON vendor RF state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_RF_TIME` | Last successful vendor RF read time | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_RF_VALID` | Vendor RF readback validity | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_Ref` | Reference source selection | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_Simulation` | Genesis local simulation state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepActive` | Hardware sweep active state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepBidirectional` | Bidirectional sweep enable | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepDirection` | Sweep direction | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepEndC` | Sweep end frequency command | GHz | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepEndI` | Sweep end frequency readback | GHz | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepExternalTrigger` | External-trigger sweep enable | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepRepeat` | Sweep repeat enable | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepRun` | Hardware sweep run command | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepStartC` | Sweep start frequency command | GHz | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepStartI` | Sweep start frequency readback | GHz | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepStepC` | Sweep step command | GHz | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepStepI` | Sweep step readback | GHz | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepTimeC` | Sweep dwell time command | ms | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_SweepTimeI` | Sweep dwell time readback | ms | Signal / verify | Config DESC |
| `UNH:GEN:MW:SRC:MW_TestMode` | Vaunix vendor test-mode state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:CentralFrequency` | Central frequency for Magic calculation | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:Dopant` | Dopant selection | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:InterlockMessage` | RF permissive explanation | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:LastError` | Last supervisor error | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:MAN:microwave_supervisor_time` | IOC heartbeat/time record for microwave supervisor; encoding to verify | — | Timestamp / heartbeat | Name-derived; verify |
| `UNH:GEN:MW:SUP:MagicMinus` | Magic minus frequency action | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:MagicPlus` | Magic plus frequency action | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:OutputFrequency` | Microwave output frequency command | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:OutputFrequencyRBV` | Microwave output frequency readback | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:RFEnable` | Supervisor RF enable request | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:RFPermissive` | Software RF ON permissive | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:Simulation` | Supervisor simulation state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepCancel` | Cancel software sweep | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepCurrentFrequency` | Current software sweep frequency | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepDwell` | Software sweep dwell time | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepError` | Software sweep error | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepPoint` | Current software sweep point | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepProgress` | Software sweep progress | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepStart` | Software sweep start frequency | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepStartCommand` | Start software sweep | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepState` | Software sweep state | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepStep` | Software sweep frequency step | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepStop` | Software sweep stop frequency | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepStopCommand` | Stop software sweep | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SweepTotal` | Total software sweep points | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:SystemConnected` | Microwave subsystem connectivity | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:UCAConnected` | UCA power supply connectivity | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:UCAVoltage` | UCA measured voltage | — | Signal / verify | Config DESC |
| `UNH:GEN:MW:SUP:VaunixConnected` | Vaunix source connectivity | — | Signal / verify | Config DESC |
