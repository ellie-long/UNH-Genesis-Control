# Genesis Phase-I Microwave Subsystem Closeout

## Scope

This document records the current UNH Genesis Phase-I Microwave software and physical-integration state. The canonical physical stack has been commissioned through real UCA, Vaunix/LabBrick, Supervisor, bounded robot motion, Phoebus, and archiving. Final physical closeout remains open for the carrier-persistence and shutdown-cleanup defects documented below, together with the explicitly deferred robot and Magic-frequency work.

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

Physical Microwave operation uses the dedicated canonical profile at
`configs/commissioning/microwave_physical` through `genesis mw-start` or
`scripts/microwave_physical.sh start`.

The IOC Manager entries remain `autostart: false` so that the launcher controls
the hardware order explicitly. The physical workflow:

1. starts the SPD1305X/UCA IOC first;
2. establishes and verifies approximately 5 V with the UCA output ON;
3. verifies the hardware-mode Vaunix Raspberry-Pi service;
4. starts the Vaunix, PCL602, Supervisor, and archiver IOCs;
5. prepares the source at approximately 140 GHz and +10 dBm with RF OFF;
6. leaves the robot Disabled;
7. enables archiving and exposes the dedicated Microwave GUI workflow.

The physical SPD1305X profile is write-enabled because the dedicated launcher
must establish the verified 5 V safe state. `initialize_on_connect` remains
false, and the driver retains the independent 5 V / 0.1 A software ceiling.


## Frequency convention

Current Genesis configuration:

```text
Vaunix synthesizer : 11.5 to 11.8333333333 GHz
Multiplier         : 12
Output frequency   : 138 to 142 GHz
```

Older imported prototype documentation used 134 to 142 GHz. That older range is not accepted as the Genesis physical operating range until the experiment owners resolve it.

## Supervisor status

The canonical physical Supervisor profile runs with `simulation: false` and
has been exercised against the physical UCA and Vaunix source. The integrated
workflow has validated physical connectivity, the 138-142 GHz configured
output-frequency window, approximately 140 GHz baseline operation, RF
permissive behavior near the 5 V UCA safe state, deliberate RF ON/OFF control,
and Supervisor-driven frequency control.

Software validation also covers request/readback separation, repeated
OFF/ABORT, UCA-output-loss handling, safe reconnect behavior, remote control
lease ownership, and deterministic shutdown verification.

Physical closeout is not complete because the commanded 140 GHz carrier has
intermittently shifted to approximately 144 GHz after RF OFF or shutdown.
The exact physical response to PLL loss and the final UCA transition tolerance
also remain experiment-owner acceptance items.


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

The canonical physical profile is write-enabled under the dedicated Microwave launcher. Startup deliberately establishes approximately 5 V, 0.05 A, output ON before the Vaunix/source-control path is used. `initialize_on_connect` remains false, and the independent 5 V / 0.1 A driver ceiling remains the hard software limit. Return the UCA to 5 V before robot motion and before physical shutdown.

### Robot

The known reference is the physical home-switch position.

Physical operation must begin from the verified home reference.

Software travel limits are not yet trusted and therefore are not a physical safety boundary.

Bounded relative X/Y jog motion has been physically validated through the Genesis operator path. Routine startup still leaves the robot Disabled. Absolute HOME/reference behavior, trusted software travel limits, and joystick operation remain deferred and must not be treated as accepted physical safety boundaries.

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

Routine physical shutdown uses `genesis mw-stop`, which invokes the dedicated
Microwave physical shutdown workflow.

Before local owners are terminated, the shutdown implementation verifies the
physical safe baseline using fresh readbacks:

- Supervisor RF request and actual RF state OFF;
- Vaunix vendor-derived RF OFF and valid/fresh;
- safe-source readiness established and hardware sweep inactive;
- UCA output ON at approximately 5 V;
- robot Disabled and stationary;
- local process/session ownership unambiguous.

The archiver is closed only after the physical device owners have been made
safe. Unknown or ambiguous owners cause the workflow to retain processes rather
than forcing cleanup.

During physical validation, the safe hardware state was successfully
established, but one stop attempt subsequently aborted during local cleanup
when process inspection returned a normal nonzero result for an already-absent
process. That cleanup defect remains open. The final workflow must treat an
already-stopped process as stopped, continue cleanup of the remaining owners,
and still report genuine survivors.

The physical hardware shutdown order remains RF OFF -> verified safe state ->
UCA 5 V/output ON -> disconnect Vaunix USB -> VDI OFF -> UCA OFF.


## Remaining physical work

Before declaring the Microwave subsystem physically complete:

1. Correct the RF-OFF carrier-persistence defect and demonstrate repeated
   140 GHz OFF -> ON -> OFF operation without an unintended shift to
   approximately 144 GHz.
2. Correct the local shutdown-cleanup defect so safe shutdown proceeds through
   complete owner cleanup even when an expected process is already absent.
3. Repeat the physical RF/SIG reflected-power acceptance procedure after the
   carrier correction.
4. Verify reconnect, disconnect, and lease-expiry behavior on the deployed
   Vaunix hardware/service baseline.
5. Confirm the final PLL-loss response policy and exact UCA permissive
   tolerance with the experiment owners.
6. Commission robot absolute reference/home, axis directions, scale, usable
   travel, and trusted software travel limits.
7. Validate joystick operation separately before relying on it for routine
   positioning.
8. Add the read-only robot-position visualization after the physical coordinate
   system is accepted.
9. Certify Magic-frequency constants before enabling Magic actions.
10. Repeat the complete physical archive, GUI, RF, robot, and shutdown
    regression on the final candidate commit.

Until these items are closed, the canonical physical Microwave profile remains
the controlled commissioning/operation path but is not yet the final
cooldown-ready accepted baseline.
