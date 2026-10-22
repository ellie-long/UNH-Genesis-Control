# Genesis Phase-I Microwave Physical Operation

## Status

The Microwave Phase-I software/control chain has been physically commissioned through:

VDI/UCA -> Vaunix/LabBrick -> PCL602 robot -> EPICS device IOCs -> Microwave Supervisor -> IOC Manager -> Phoebus GUI -> common Genesis archiver.

Validated physical state on 2026-09-29:
- Siglent SPD1305X UCA supply connected and controllable.
- UCA measured approximately 5.001 V with output ON.
- Vaunix LMS-123 serial 15033 operating in hardware mode.
- Synthesizer set to approximately 11.66666667 GHz, corresponding to approximately 140 GHz after the x12 multiplier.
- Vaunix source power set to +10 dBm with RF OFF.
- PLL lock confirmed.
- PCL602 hardware transport and visible relative motion validated.
- Robot is left Disabled for normal startup.
- Microwave Supervisor connected to the physical UCA and Vaunix IOCs.
- Supervisor RF permissive validated at UCA approximately 5 V.
- Phoebus Microwave overview/detail displays validated.
- Common Genesis archiver validated on the physical stack; 66 monitored PVs were discovered during commissioning.

Subsequent physical RF validation exercised supervised RF ON/OFF and bounded robot motion on the integrated stack. The Microwave baseline is not yet cooldown-ready: carrier/output frequency has intermittently shifted from the 140 GHz operating point to approximately 144 GHz after RF OFF, and the dedicated stop workflow has encountered a local-cleanup failure after the physical safe state was already established. The RF/SIG acceptance procedure below must be repeated after those issues are corrected.

## Important operating constraints

The microwave hardware procedure is authoritative:

- Do not reflect more than 10% of output power back to the RF output port.
- UCA 5 V corresponds to zero microwave output from the VDI chain.
- UCA 0 V corresponds to full microwave output.
- The robot/stand must only be moved with the UCA at 5 V.
- During the initial reflected-signal test, begin observing SIG near UCA 4 V and do not lower the UCA below 3.5 V.
- SIG should remain negative; a positive SIG indication is unsafe.
- Use the current Genesis physical frequency range of 138-142 GHz.
- The commissioning baseline is 140 GHz output with the LabBrick source at +10 dBm.
- RF must remain OFF until the physical turn-on sequence and software readiness checks have passed.
- VDI green-light status and SIG are not instrumented in EPICS and therefore require operator confirmation.

## Physical turn-on order

1. With RF input power OFF, UCA OFF, and the VDI-175T supply OFF, connect the large silver power connector, UCA cable, and SIG cable.
2. Turn ON the VDI-175T supply and verify its green light.
3. Bring the UCA to 5 V.
4. Only after the UCA is at 5 V, connect the USB cables.
5. Start the Genesis Microwave physical stack.
6. Verify the readiness check before enabling RF.

The Genesis startup must leave RF OFF.

## Routine Genesis startup

From the repository root, activate the `genesis-p1` environment and run the dedicated physical Microwave startup script.

The script should:
- launch the dedicated physical IOC Manager profile;
- start the UCA IOC first;
- establish/verify UCA approximately 5 V;
- verify the hardware-mode Vaunix Raspberry-Pi service;
- start Vaunix, PCL602, Supervisor, and archiver IOCs;
- prepare the source at 140 GHz / +10 dBm with RF OFF;
- leave the robot Disabled;
- enable the common Genesis archiver;
- launch the Microwave Phoebus GUI;
- finish with an RF-readiness summary.

Do not use the generic read-only Genesis startup as a substitute for the dedicated physical Microwave startup.

## Final RF/SIG acceptance test

Before enabling RF, confirm all of the following:
- VDI green light is ON.
- UCA measured voltage is approximately 5 V.
- UCA output is ON.
- Vaunix is in HARDWARE mode.
- Output frequency is within 138-142 GHz; commissioning baseline is 140 GHz.
- Source power is +10 dBm.
- Vaunix RF readback is OFF.
- PLL is locked.
- Supervisor System Connected is true.
- Supervisor RF Permissive is true.
- Robot is Disabled and not moving.
- SIG meter/readout is visible to the operator.
- A small aluminum/foil reflector is available for the reflected-signal check.

Then:
1. Enable RF from the Microwave Supervisor GUI while the UCA remains at 5 V.
2. Confirm RF readback is ON.
3. Slowly lower the UCA voltage while watching SIG.
4. Start looking for reflected response near UCA 4 V.
5. Swipe/place aluminum or foil briefly below the microwave window to confirm a reflected-signal response.
6. Keep reflected power below 10%.
7. Do not lower UCA below 3.5 V during this initial reflection/alignment check.
8. Keep SIG negative.
9. Align the microwave path so power is not being reflected from the waveguide sides.
10. Only after alignment is accepted should lower UCA values be considered. Do not go to full power merely for commissioning.

If any reading is unexpected, set RF OFF and return UCA to 5 V.

## Robot status

Physical relative motion has been observed. The axes remain unreferenced and software travel limits are not yet commissioned. Therefore:
- do not rely on X/Y values as certified absolute positions;
- do not use HOME;
- keep routine robot control Disabled unless deliberate motion is required;
- always return the UCA to 5 V before robot/stand motion.

Robot absolute reference and soft-limit commissioning remain a follow-up before routine automated positioning.

## Shutdown

Use the dedicated Microwave physical stop script first. It should:
- command RF OFF;
- return UCA to 5 V;
- return the robot to Disabled;
- stop archiving;
- stop the managed Microwave IOCs.

Software shutdown first closes the independent Microwave Phoebus Java process,
identified by its exact settings profile and `genesis_microwave.bob` resource.
This closes that instance's main and expert windows; ssRF and unrelated Java
instances are preserved. Process identity is rechecked before signaling, and
exit is verified within a bounded wait. A GUI-close failure is reported but
still permits the physical safe-state attempt. Even if safety succeeds, that
failure returns nonzero and retains local owners for recovery because the GUI
could issue new commands. No persistent PID file is used.

Shutdown then establishes the safe baseline using fresh successful
IOC readbacks: source RF OFF in hardware mode, Supervisor RF OFF with its sweep
stopped, UCA output ON at 5 V, and robot Disabled/stationary. Motor holding
current is preserved. Failed verification retains the physical owners for
recovery and returns failure; shutdown never forces cleanup past this gate.

Local cleanup targets only sessions whose process ancestry, repository path,
and dedicated physical profile establish Microwave ownership. It stops the
Supervisor, robot IOC, source IOC, and UCA IOC in that order, then disables
archiving and verifies monitoring/file closure before stopping the archiver.
The dedicated manager stops last. Shared/generic managers and archivers,
ssRF sessions, and remote Vaunix/PCL602 services are preserved. Shared ownership
requires the exact same-repository generic profile and process ancestry; unknown
or ambiguous manager, archiver, or device ownership blocks shutdown. A surviving
session/process produces an explicit failure; a repeated stop with no dedicated owners performs no hardware writes.
Keep operator RF/motion controls idle throughout shutdown.

Then complete the physical hardware shutdown:
1. Confirm UCA is 5 V.
2. Confirm LabBrick RF input is OFF.
3. Unplug USB at the green box.
4. Turn OFF the VDI-175T supply.
5. Replace the sticker over the USB input if required by the apparatus procedure.
6. Turn OFF the UCA power supply.
7. Remove remaining cables only if desired.

## Baseline and future use

Freeze and tag the physical-control baseline only after the remaining carrier-persistence and shutdown-cleanup defects are corrected and the same commit passes the full regression plus repeated physical RF/SIG validation. Do not treat the current software-only regression result as a cooldown-ready hardware acceptance.

## Deferred joystick validation

The Raspberry Pi PCL602 configuration includes optional joystick control, but
physical joystick motion has not yet been accepted as part of this baseline.
Joystick validation is deferred and is not required for the Microwave RF/SIG
commissioning or cooldown operation. Normal startup therefore leaves the robot
in Disabled mode. Test joystick control separately before relying on it for
routine positioning.

## Integrated RF-operation status

The supervised RF-control hardening and deterministic shutdown work are now
integrated into the canonical Microwave physical stack. The implementation
separates operator request from vendor readback, performs verified repeated
OFF/ABORT actions, establishes safe OFF on connection/reconnection, uses an
exclusive remote Vaunix control lease with a watchdog, and verifies fresh
physical readbacks during shutdown.

The physical validation performed after that integration confirmed that the
complete control path can operate the real subsystem, but it also exposed two
remaining defects: carrier/output frequency can move away from the commanded
140 GHz point after RF OFF, and local process cleanup can abort after the
hardware safe state has already been established. Those defects must be closed
and the physical RF/SIG acceptance repeated before this document represents a
final cooldown-ready baseline.

## Physical commissioning update — 2026-10-22

Validated on Ubuntu/WSL under the utsav account:

- Microwave IOC Manager moved to CA port 5094 to avoid the Windows Magnet IOC conflict.
- Microwave Phoebus overview and detail screens validated using Channel Access.
- Raspberry Pi mmWaveRobot at 192.168.1.23.
- Corrected Vaunix shim deployed and verified:
  SHA-256 da52dfc7e6cd5b724dece9a3ef96a2fa33ff602aa5b631071fe30914dbdebbb6
- Previous Pi shim preserved as:
  /home/unhpoltarg/vaunix_shim/vaunix_shim.py.backup.20261022T083314
- Startup/readiness verifies the deployed shim and running service.
- RF ON/OFF physically tested with source at 140 GHz, +10 dBm.
- UCA voltage adjusted from 5.001 V to 4.501 V with RF ON,
  restored to 5.001 V before RF OFF.
- RF Permissive may become 0 below the 5 V ON-edge requirement;
  this does not itself terminate an existing RF session.
- RF OFF preserves the 140 GHz carrier with the corrected shim.
- RF-OFF software frequency sweep tested from 139.9 to 140.1 GHz
  with 0.025 GHz steps and 2-second dwell.
- Stop Sweep successfully interrupted at point 4 of 9.
- Final state: RF OFF, UCA 5.001 V, 140 GHz, robot Disabled.
- Microwave archiver active with 76 PVs.
- Full Genesis regression: 1308 passed, 203 subtests passed.

Outstanding physical acceptance:
- Document quantitative SIG/reflected-power acceptance.
- Validate RF-enabled frequency sweeping separately.
- Complete robot referencing, limits, and motion acceptance.
- Confirm controlled shutdown and restart from the finalized main.
