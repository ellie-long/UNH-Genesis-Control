# magnet_ioc: porting map from the student's Magnet_V3

`magnet_ioc` is the student's IPS120-10 magnet IOC (Magnet_V3,
`Magnet_EPICS_IOC_ALL_FUNCTIONS.py` plus its `.bob` screen), rebuilt as a
Genesis device and with the review's fixes.  The student is fixing the same
review issues in their own code.  When the next version arrives, use this
map to carry their changes over.

The student's code and the review documents stay in the local, gitignored
`magnet_student/` folder (`docs/3_ISSUES.md` holds the issue numbers cited
here).  Neither is copied into this repository.

## How the code is laid out

| Student (one file) | Here | Why it moved |
|---|---|---|
| `IPSClient`, read and decode helpers | `ips120_protocol.py` | the serial protocol on its own, testable against the emulator |
| ramp, heater, match and hold coroutines | `ips120_sequences.py` (`MagnetSequencer`) | one operation at a time, a shared abort and permit check, injectable clock |
| (none) | `interlocks.py` | the cryogenics permit (review issue 13; Ells, questions 85 and 87) |
| PV creation, `poll_ips`, `publish_measurements`, the `*_changed` callbacks | `device.py` (`Device`, a `GenesisBaseDevice`) | Genesis health PVs, `write_enabled` gate, reconnect, event log |
| hard-coded IP, rates, limits | `configs/magnet_physical/ips120/settings.yaml` | settings, not code |
| `Magnet_full_control_ALL_FUNCTIONS_no_overlap.bob` | `tools/make_magnet_screen.py` produces `gui/genesis_magnet.bob` | generated like the other Genesis screens |

Genesis runs it as `python master_ioc.py -s configs/magnet_physical/ips120 -i ips120`,
or through `configs/magnet_physical/ioc_manager`.

## Functions

| Student | Here | Changed |
|---|---|---|
| `IPSClient.__init__/connect/close` | `SerialTcpConnection` + `IPSClient(transport)` | the Genesis serial-gateway transport and reconnect |
| `IPSClient.query` | `IPSClient.query` | stale input drained first (issue 4); the reply must echo the command; `?` raises; V exempt from the echo check |
| `parse_numeric_response` | same | rejects non-finite numbers |
| `read_demand_current` / `read_persistent_current` / `read_setpoint` / `read_sweep_rate` / `read_status` | `IPSClient.read_*` (R0 / R16 / R5 / R6 / X) | added R1 voltage, R2 measured current, R17 trip, R20 heater mA, R21/R22 safe limits, R23 lead resistance, R24 inductance, V |
| `decode_status`, `get_heater_status`, `get_activity_status`, `get_motion_status` | same names, `Status` dataclass | the system and limit digits decoded too (issue 2) |
| `get_gamma`, `calculate_field`, `calculate_frequency` | `Device` field equation; `NMR_D`, `NMR_P` | Ells's NMR-measured equation B = 0.1027405 I + 0.00003497875 (a setting) |
| `choose_sweep_rate` | `choose_sweep_rate` | rate from the band the segment lies in (issue 1); a 3 A/min cap checked separately (issue 6) |
| `next_ramp_target` | `next_ramp_target` | unchanged |
| `validate_current` | `validate_current` | unchanged in spirit; limit from settings |
| `check_abort` | `MagnetSequencer._check` | also checks the permit, supply faults, R6 against the cap, the observed rate and the inductance |
| `hold_magnet` / `hold_changed` | `MagnetSequencer.hold` / `HOLD` action | always allowed; never needs the permit; a clamped supply stays clamped |
| `verify_safe_for_H2` | `verify_safe_for_H2` | R0 and R16 read twice (issue 4) |
| `heater_turn_on_progress` | `_heater_wait` | 20 s after heater ON and after heater OFF, current untouched (Ells) |
| `switch_heater_on` / `switch_heater_off` | `heater_on` / `heater_off` | two explicit commands (screen issue 14); heater off keeps the supply energized (`persistent_supply: hold`, this cool-down) |
| `heater_toggle_changed` | `HEATER_ON_CMD`, `HEATER_OFF_CMD` actions | no toggle |
| `recover_persistent_mode` / `match_psu_to_magnet` / `match_psu_changed` | `_match_to` / `match` / `MATCH` action | deadline on every wait (issue 11) |
| `ensure_switch_open` | `_ensure_switch_open` | a ramp may open the switch itself (issue 7) |
| `execute_ramp` / `start_ramp_changed` | `ramp` / `RAMP` action | permit, preflight (R24 near 38 H, R20 near 40 mA), segment ends at rest (issue 12), deadlines, one operation at a time (issue 3), abort flag cleared at start (issue 8) |
| `poll_ips`, `read_all_values`, `publish_measurements` | `Device.do_reads` | fast reads every poll, settings every 10th; on a supply fault, HOLD if an operation is running |
| `poll_fringe_gaussmeter`, `read_fringe_field` | not ported | the LS455 is already a Genesis cryo IOC (`UNH:GEN:CRYO:FIELD`) |
| `target_current_changed` | `TARGET_CURRENT` (aOut, DRVH = limit) | |
| `species_changed`, the converter (`converter_*`, `set_converter_values`) | not ported | `TARGET_FIELD` covers field to current; a species calculator can come later |
| (none) | `MagnetSequencer.outage` / `OUTAGE` action | the power-outage button (issue 19): HOLD, then make the magnet persistent |

## PVs (student prefix `MAG:`, here `UNH:GEN:MAG:`)

| Student | Here |
|---|---|
| `CONNECTED` | `COMM` (Genesis health) |
| `CURRENT` | `CURRENT` (R0); `CURRENT_MEAS` (R2) is new |
| `PERSISTENT_CURRENT` | same (R16) |
| `FIELD`, `PS_FIELD`, `MAGNET_FIELD` | `FIELD` (the magnet's field, from `MAGNET_CURRENT`) with `FIELD_SOURCE` = supply or persistent (issue 9) |
| `FREQUENCY`, `PS_FREQUENCY`, `MAGNET_FREQUENCY` | `NMR_D` (deuteron), `NMR_P` (proton) |
| `CURRENT_DIFFERENCE` | not ported (CURRENT and PERSISTENT_CURRENT are both shown) |
| `SETPOINT`, `SWEEP_RATE` | same (R5, R6) |
| `STATUS`, `ACTIVITY`, `CONTROL`, `HEATER`, `MOTION` | same, plus `SYSTEM`, `LIMITS`, `QUENCH`, `CLAMPED` |
| `HEATER_ON_STATE`, `HEATER_OFF_STATE` | `HEATER_ON` |
| `RAMP_STATE`, `IDLE` | `STATE`, `OPERATION`, `BUSY` |
| `HEATER_PROGRESS` | same |
| `LAST_ERROR` | `LAST_EVENT`, `ERROR` (Genesis health) |
| `FRINGE_*` | not ported (LS455 IOC) |
| `TARGET_CURRENT` | same; `TARGET_FIELD` is new |
| `SPECIES`, `CONV_*` | not ported |
| `HOLD`, `START_RAMP`, `HEATER_TOGGLE`, `MATCH_PSU_TO_MAGNET` (longOut) | `HOLD`, `RAMP`, `HEATER_ON_CMD` / `HEATER_OFF_CMD`, `MATCH` (Action records) |
| (none) | `VOLTAGE`, `TRIP_CURRENT`, `HEATER_MA`, `SAFE_NEG`, `SAFE_POS`, `LEAD_RES`, `INDUCTANCE`, `INDUCTANCE_MEAS`, `MAGNET_CURRENT`, `FIELD_SLOPE`, `FIELD_OFFSET`, `PERMIT`, `PERMIT_REASON`, `EXPERT`, `OUTAGE`, `VERSION` |

The full list with descriptions is the `records:` block of the settings file
(45 records; the master archiver records them and the IOC heartbeat, 46 PVs).

## Screen

`gui/genesis_magnet.bob` keeps the student's groups (readbacks, ramp,
heater, status) and adds the permit card, the supply-settings card, the
power-outage button and the field and current trends.  Every command that
moves the magnet asks for confirmation; HOLD does not.  The Panopticon's
Superconducting Magnet card shows `MAGNET_CURRENT`, `FIELD`, `NMR_D` and
`HEATER`, and links to this screen.

## Porting a new student version

1. Extract it next to the old one in `magnet_student/` (keep both; never edit them).
2. Diff the new version against Magnet_V3, function by function.
3. Find each changed function in the table above.  Apply the change to its
   counterpart here, unless the review fix already covers it.  If the
   student's fix differs from ours, note it for Ells instead of
   choosing one silently.
4. New PVs: add them to `device.py` and to `records:` in the settings, and
   update the master-registry counts in `tests/test_master_registry*.py`.
5. New commands: add a confirm dialog on the screen (except stop or hold
   commands) and a test against the emulator (`cooldown_sim/emulators/ips120.py`).
6. Run `tests/test_magnet_*.py` and `tests/cooldown_sim/test_cooldown_sim_magnet_leg.py`,
   then regenerate the screens (`python tools/make_magnet_screen.py`,
   `python tools/make_panopticon.py`).
