# Genesis Phase-I Microwave Subsystem Closeout

## Scope

This document records the software/off-lab completion state of the UNH Genesis Phase-I Microwave subsystem before final physical commissioning.

The implemented subsystem contains:

1. Siglent SPD1305X UCA power-supply IOC.
2. Vaunix / Bridge12 microwave-source IOC.
3. Microwave Supervisor IOC.
4. PCL602 positioning-robot IOC.
5. Common IOC Manager integration.
6. Common Genesis archiving.
7. Phoebus subsystem/device GUIs.
8. Common Genesis archive-viewer support.

## EPICS hierarchy

```text
SPD1305X
  -> microwave_ioc.spd1305x
  -> UNH:GEN:MW:PS:*

Vaunix / Bridge12
  -> Raspberry Pi shim
  -> microwave_ioc.vaunix_lms
  -> UNH:GEN:MW:SRC:*

UCA + Vaunix EPICS PVs
  -> logic_devices.microwave_supervisor
  -> UNH:GEN:MW:SUP:*

PCL602 robot
  -> Raspberry Pi shim
  -> microwave_ioc.pcl602
  -> UNH:GEN:MW:ROBOT:*
```

Higher-level GUI, archiver, and future ML/AI logic communicate through EPICS. They do not bypass device IOCs to open hardware transports directly.

## Current startup policy

Normal Genesis startup automatically starts only the physically validated Microwave SPD1305X IOC.

It does **not** automatically start:

- Vaunix / Bridge12 physical control;
- Microwave Supervisor physical operation;
- PCL602 physical robot control.

Those remain explicit commissioning activities until their physical integration is completed.

The SPD1305X normal configuration remains read-only:

```yaml
write_enabled: false
initialize_on_connect: false
```

## Frequency convention

Current Genesis configuration:

```text
Vaunix synthesizer : 11.5 to 11.8333333333 GHz
Multiplier         : 12
Output frequency   : 138 to 142 GHz
```

Older imported prototype documentation used 134 to 142 GHz. That older range is not accepted as the Genesis physical operating range until the experiment owners resolve it.

## Supervisor status

The Supervisor has been validated in local simulation with:

- EPICS-only device interaction;
- RF permissive logic;
- output-frequency command/readback;
- software sweep;
- sweep progress/state;
- safe RF-OFF behavior;
- rejection of unavailable Magic-frequency operations.

The checked-in Supervisor remains:

```yaml
simulation: true
```

The current 5.0 V +/- 0.10 V UCA permissive window is a software-validation value only.

## Magic-frequency status

Magic actions are intentionally unavailable.

Required dopant names, gamma terms, and Magic +/- constants have not been certified. No constants should be inferred.

## Physical Microwave safety requirements

### VDI / microwave chain

The non-negotiable laboratory startup rule is:

**VDI green light ON before plugging in USB / enabling the source-control path.**

The VDI box powers the amplifier, multiplier, and diode chain. RF must not be sent through the chain before the required VDI power state is established.

The current Genesis software does not instrument the VDI green-light state. Therefore this remains a manual operating requirement until a physical permissive/readback is added.

### Reflected-power SIG

During physical tuning the reflected-power SIG indication must remain negative.

A positive SIG indication is treated as unsafe/unexpected and requires the operator to stop and reassess the setup.

The current software does not yet ingest this SIG signal into the Supervisor, so it remains a manual commissioning check.

### UCA

The SPD1305X software implementation contains a source-level 5 V / 0.1 A ceiling independent of configuration values.

Normal deployment remains read-only.

Physical write commissioning must be deliberate and must return to read-only mode afterward.

### Robot

The known reference is the physical home-switch position.

Physical operation must begin from the verified home reference.

Software travel limits are not yet trusted and therefore are not a physical safety boundary.

Software HOME, ZERO, motor-enable, and motion controls are intentionally not exposed as normal operator controls before robot commissioning.

Shutdown requires the robot to be stationary with both software motor-enable states OFF when the Robot IOC is running.

## GUI hierarchy

```text
gui/genesis_microwave.bob
    |
    +-- gui/genesis_mw_supervisor.bob
    +-- gui/genesis_mw_spd1305x.bob
    +-- gui/genesis_mw_vaunix.bob
    +-- gui/genesis_mw_robot.bob
```

The subsystem overview is the normal navigation entry point.

Normal RF and output-frequency operation goes through the Supervisor.

Vaunix detail remains diagnostic/readback focused.

Robot detail remains status/safety focused until physical commissioning.

### Phase-II robot-position visualization

Ben's earlier robot interface included a useful graphical representation of the microwave source position.

A Genesis version should be added after physical robot commissioning establishes:

- true home position;
- X/Y axis directions;
- usable physical travel;
- cm-to-physical-position mapping;
- trusted travel limits.

The Phase-II display should be read-only: static apparatus/rail geometry with a moving Vaunix/source marker driven by `X_CM` and `Y_CM`. The visualization must not itself issue motion commands.

## Archiving

All four Microwave components define `archive_records`.

The common Genesis archiver stores one timestamped CSV stream per PV and also archives IOC heartbeat PVs.

The full Microwave simulation stack validated:

```text
51 device/subsystem archive records
3 IOC heartbeat records
54 monitored PVs total
```

Microwave heartbeat files were verified to advance with monotonic millisecond-resolution archive timestamps.

The common viewer is launched with:

```bash
./scripts/genesisctl.sh archive-viewer
```

and provides:

- All Genesis;
- Microwave;
- ssRF;
- Microwave UCA / Vaunix / Supervisor / Robot filtering;
- Microwave Phase-I quick views.

## Shutdown policy

The Genesis shutdown script keeps the archiver alive while device IOCs are stopped.

For manually started Microwave commissioning IOCs it should verify:

- Supervisor RF request OFF;
- Vaunix raw RF OFF with healthy communication;
- robot communication healthy;
- X/Y not moving;
- X/Y motor enables OFF;
- SPD1305X physical output OFF.

`GENESIS_FORCE_STOP=1` remains a recovery/development bypass and must not be used as routine operating practice.

## Remaining physical work

Before declaring the Microwave subsystem physically complete:

1. Validate the Vaunix/Raspberry-Pi/USB chain on the real system.
2. Confirm the approved physical output-frequency range.
3. Confirm the UCA voltage/permissive tolerance with experiment owners.
4. Apply the VDI-green-before-USB procedure.
5. Verify reflected-power SIG behavior.
6. Validate Supervisor RF ON/OFF with the physical source.
7. Validate physical sweeps conservatively.
8. Commission robot home, directions, scale, and usable travel.
9. Decide and validate physical software travel limits.
10. Add the read-only robot-position visualization.
11. Certify Magic-frequency constants before enabling Magic actions.
12. Repeat common archive/GUI/shutdown regression with physical devices.

Until these are complete, the checked-in physical Vaunix and robot profiles remain non-autostart, and normal Genesis startup remains conservative.
