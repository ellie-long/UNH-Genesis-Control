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

## RF operation

`RFEnable` is the deliberate operator request. Vendor actual/readback and validity are separate. Repeated OFF and RFAbort stop sequencing and verify fresh vendor RF OFF. Startup and reconnect do not restore ON; the remote exclusive lease/watchdog handles client loss while the shim remains functional.

ON requires fresh source/UCA communications, UCA output ON near 5 V, physical source mode, valid carrier/power inside existing limits, PLL lock and exclusive ownership. The 5 V condition is transition-only. Calibration is diagnostic and is not an RF-enable gate. Physical and explicit simulation profiles remain separate.

See [current RF operation and integration requirements](../../docs/MICROWAVE_RF_OPERATION.md) for exact PVs, protocol, manual protections, software tests and outstanding physical acceptance.

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
