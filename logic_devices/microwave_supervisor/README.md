# Genesis Microwave Supervisor

The Microwave Supervisor is the subsystem-level EPICS coordination layer for the UNH Genesis Phase-I Microwave system.

It communicates only through EPICS PVs. It does not open the SPD1305X, Vaunix / Bridge12 source, Raspberry Pi shim, or robot transport directly.

## Genesis hierarchy

```text
SPD1305X UCA IOC  ----\
                       \
                        -> Microwave Supervisor
                       /      UNH:GEN:MW:SUP:*
Vaunix source IOC ----/

PCL602 robot IOC remains an independent positioning device.
```

Normal RF and output-frequency operation is intended to pass through the Supervisor. Raw Vaunix controls remain commissioning/maintenance interfaces.

## Current frequency convention

The checked-in Genesis Vaunix configuration uses:

```text
Synthesizer frequency : 11.5 to 11.8333333333 GHz
Multiplier            : 12
Microwave output       : 138 to 142 GHz
```

Older imported prototype material referred to 134 to 142 GHz. Genesis does not treat that older range as physically validated. Resolve the physical operating range with the experiment owners before RF commissioning.

## RF permissive

RF OFF is always permitted.

An OFF-to-ON request requires:

- UCA IOC connected;
- Vaunix IOC connected;
- UCA measured voltage inside the configured software-validation window.

The checked-in Supervisor configuration remains:

```yaml
simulation: true
```

The current 5.0 V +/- 0.10 V threshold is for software/interlock validation. It is not a certified physical operating tolerance.

## Magic-frequency actions

Magic-frequency actions remain unavailable because the required experiment constants and dopant definitions have not been certified.

Do not infer or substitute constants.

## Phase-I operator interface

Subsystem overview:

```text
gui/genesis_microwave.bob
```

Detail displays:

```text
gui/genesis_mw_supervisor.bob
gui/genesis_mw_spd1305x.bob
gui/genesis_mw_vaunix.bob
gui/genesis_mw_robot.bob
```

Normal RF/frequency operation is through the Supervisor GUI. Raw Vaunix write controls and robot motion controls are intentionally not exposed as normal operator controls before physical commissioning.

## Archive viewer

Continuous Microwave PVs and IOC heartbeats use the common Genesis CSV archiver and viewer:

```bash
./scripts/genesisctl.sh archive-viewer
```

The viewer provides Microwave device grouping and Phase-I quick views.

## Physical commissioning

Before enabling physical RF or robot motion, follow:

```text
docs/MICROWAVE_PHASE1_CLOSEOUT.md
```

and the approved laboratory procedure.
