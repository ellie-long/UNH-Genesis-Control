# Genesis Phase-I Microwave Supervisor

The Microwave Supervisor is the operator-facing EPICS coordination layer above the SPD1305X UCA power-supply IOC and the Vaunix / Bridge12 microwave-source IOC.

It does not communicate directly with either physical device.

## Phase-I hierarchy

```text
SPD1305X hardware
    -> SPD1305X device IOC
    -> UNH:GEN:MW:PS:*

Vaunix / Bridge12 source
    -> Raspberry-Pi shim
    -> Vaunix device IOC
    -> UNH:GEN:MW:SRC:*

UCA + Vaunix EPICS PVs
    -> Microwave Supervisor
    -> UNH:GEN:MW:SUP:*
```

The PCL602 robot is an independent positioning IOC under `UNH:GEN:MW:ROBOT:*`; it is not an RF permissive.

## Current Phase-I frequency convention

The current Genesis Vaunix implementation uses a synthesizer range of 11.5 to 11.8333333333 GHz with a multiplier of 12.

Therefore the current output-frequency range is **138 to 142 GHz**.

Older imported Microwave Supervisor documentation referred to 134 to 142 GHz. That range is not treated as validated by the current Genesis configuration and must be resolved with the experiment owners before physical RF commissioning.

## RF permissive

The Supervisor permits an OFF-to-ON RF request only when:

- the Vaunix IOC reports connected;
- the UCA IOC reports connected; and
- measured UCA voltage is within the configured window around the required voltage.

RF OFF remains allowed.

The checked-in Supervisor configuration remains:

```yaml
simulation: true
```

The current 5.0 V +/- 0.10 V window is used for software/interlock validation and must not be treated as a certified physical operating tolerance until confirmed for the microwave system.

## Magic frequencies

Magic-frequency calculations remain disabled operationally because the required experiment constants and dopant definitions have not yet been certified in Genesis.

No values should be inferred or substituted.

## Operator boundary

Normal RF/frequency controls use:

```text
UNH:GEN:MW:SUP:*
```

Raw device PVs under:

```text
UNH:GEN:MW:PS:*
UNH:GEN:MW:SRC:*
```

remain available for commissioning and maintenance, but should not be the normal operator-control path.

The PCL602 robot remains a separate positioning device and is not an RF permissive.

## Phase-I GUI hierarchy

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

## Archive and IOC heartbeat integration

Microwave device/subsystem PVs use the common Genesis CSV archiver. The IOC Manager heartbeat PVs are archived by the same common archiver.

The validated full Microwave simulation stack contains:

```text
51 device/subsystem archive records
3 IOC heartbeat records
54 monitored PVs total
```

Use the common viewer:

```bash
./scripts/genesisctl.sh archive-viewer
```

The viewer provides Microwave device grouping and Phase-I quick views without changing ssRF archive behavior.

## Physical readiness boundary

Before physical RF operation:

1. confirm VDI green before USB/source-control connection;
2. confirm reflected-power SIG remains negative during tuning;
3. confirm the approved physical output-frequency range;
4. confirm the physical UCA permissive tolerance;
5. keep Magic actions disabled until constants are certified.

Before physical robot operation:

1. begin from the verified hardware home reference;
2. confirm axis direction and physical scale;
3. do not rely on current software travel limits as a safety boundary.

See `docs/MICROWAVE_PHASE1_CLOSEOUT.md` for the full software/off-lab closeout and remaining physical-commissioning checklist.
