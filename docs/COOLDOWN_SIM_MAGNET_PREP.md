# Magnet power supply: ready for Genesis's magnet controls

Prepared 2026-10-21, before Utsav's magnet IOC lands. What we know, where the
magnet plugs into the simulator and the screens, and what to settle when the
code arrives.

## What we know

- **Instrument:** the magnet power supply on the magnet's serial-to-Ethernet
  server (port 1, `Magnet_IPS`, TCP 5001, 9600 8N2, RS-232): an **Oxford
  IPS120-10** (Ells 2026-10-22). Highest priority of the instruments Genesis
  does not read yet (question 26).
- **Field per current:** 0.10274 T/A, the same in every cool-down from 2022 to
  2026 (`FIELD_T_PER_A` in `cooldown_sim/model/target.py`). The DAQ's "Central
  Mag Field" is that constant times the current, not a separate measurement.
  5 T is about 48.7 A. The field at the target, measured by NMR (Ells,
  question 86): **B = 0.1027405 x I + 0.00003497875 T**, the offset being the
  fields around the magnet, Earth's included (`field_from_current`).
- **The magnet's limits** (AMI specification, system 14109, question 84):
  rated 67.9 A for 70 kG (7 T), not warranted above; maximum charge rate
  0.0504 A/s = 3.02 A/min; inductance 38 H; coil constant 1.031 kG/A;
  minimum operating LHe level 16.0".
- **How it is run** (2022-05 to 2026-07 records):
  - operating fields 5.0-5.5 T (48.7-53.5 A);
  - the logged supply current always equals the magnet current, yet the
    magnet IS run persistent sometimes (Ells 2026-10-22), so the records do
    not show persistent periods and the emulator must support both modes;
  - ramps in current bands from 2021-09 on: median 3.0 / 2.0 / 1.0 A/min
    below 20 / 40 / 65 A (3 A/min at every current in 2020). The shift
    handbook and AMI give a single 3 A/min limit; where the bands come from
    is question 90.
  - The one outlier is 2022-05's 299.6 A reading beside a 5.08 T maximum, a
    glitch.
- **Existing Genesis magnet drivers:** `target_ioc/instruments/cm4g_magnet.py`
  and `cs4_magnet.py` drive Cryomagnetics supplies (Model 4G, CS-4), not the
  IPS120-10, and no UNH profile uses them. They show the Genesis pattern a
  new driver follows (`TelnetDevice`, write gating with `can_write`).
- **The student's draft IOC** (Magnet_V3, local in `magnet_student/`,
  unchanged) and its review: `magnet_student/docs/`.
- **Old record columns:** `PS Current (A)`, `Magnet Current (A)`, `Central Mag
  Field (T)`, `Mag Field Span (T)`. The converter writes them as
  `NOT_IN_GENESIS` today.

## Where the magnet plugs in

When the IOC arrives, each of these changes. None needs a Genesis file
changed.

| Place | Today | With the magnet IOC |
|---|---|---|
| Sim emulator (`cooldown_sim/emulators/`) | none | speaks the supply's protocol as Utsav's driver uses it: output and magnet current, field, target, sweep rate, hold / to set point / to zero, heater, status |
| `cooldown_sim/stacks.yaml` | none | a cryo-leg IOC with its CA/PVA and emulator ports |
| Channel map (`channel_map.yaml`) | `ps_current`, `magnet_current`, `central_field` from the record | the IOC's PVs mapped to those columns; units, valid ranges |
| Sim model (`model/target.py: field_T`) | the record's field, or current x 0.10274 | **closed loop:** the field the emulator's current gives, so a ramp moves the NMR line and the DNP optimum; **replay:** the emulator follows the record's current |
| Quench (open question 8) | field held at the record | the emulator drops the current; the field follows |
| Sim Control fault list | none | the supply's faults (unplugged, silent, garbage, NAK) like every instrument |
| Panopticon (dev-visualizer) | grey: current, field, NMR frequency from field, switch heater | live read-backs; NMR frequency = 6.5359 MHz/T x B (deuteron); a field trend |
| Genesis -> LabVIEW converter | `NOT_IN_GENESIS` | the four magnet columns from the IOC's PVs |
| Microwave supervisor's Magic frequencies | central field from its settings | follow the field PV, if the supervisor offers that |

## Safety

The supply is the most consequential thing Genesis will write to. In the
simulator:

- the IOC must only ever reach the emulator: loopback ports, checked by the
  guard as for every instrument;
- the driver's write-enable / simulation flags are never rewritten by profile
  generation (`profile.SAFETY_KEYS`); check the new driver's flag names are
  in that set;
- Genesis will both show and **command** the supply (Ells 2026-10-22,
  question 85), and refuse to start, or stop, a ramp on: magnet LHe below
  the 20" alarm, magnet thermometers warming, a quench, a shells-vacuum
  alarm. What "stop" does (hold, or run down) and the thresholds: question 87;
- scenarios should exercise each: a ramp above the rate limit, a ramp with
  low magnet LHe, warming magnet thermometers, a shells-vacuum alarm, and a
  quench during a ramp; and check the refusal is seen by the operator.

## To settle when the code arrives

Settled: 82 (IPS120-10), 83 (persistent sometimes), 84 (limits, from the AMI
specification), 85 (show and command; the four conditions block or stop a
ramp), 86 (the field equation). Open: 87 (what "stop" does, and the
thresholds), and 8 (quench behaviour). All in
`docs/COOLDOWN_SIM_OPEN_QUESTIONS.md`.
