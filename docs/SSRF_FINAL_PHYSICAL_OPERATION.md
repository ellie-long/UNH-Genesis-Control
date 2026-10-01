# Genesis ssRF Phase-I Final Physical Source-Control Operation

## Status

Validated on September 28, 2026.

The Genesis ssRF Phase-I physical source-control path has been validated through a complete one-shot sequenced burn with the downstream/LZY amplifier unpowered.

This milestone validates the control, switching, interlock, waveform, sequencing, GUI, authorization, and logging architecture.

It does not yet constitute powered amplified-RF or target-connected commissioning.

## Final operator commands

```bash
./scripts/genesisctl.sh ssrf-physical-status
./scripts/genesisctl.sh ssrf-deploy-keysight
./scripts/genesisctl.sh ssrf-start
./scripts/genesisctl.sh ssrf-stop
```

`ssrf-start` is the single final physical operator startup path.

The earlier physical-no-RF demo path is retained only as commissioning history.

## Validated adaptive-burn GUI workflow

```text
NMR / CALIBRATE
→ BURN SETUP
→ GENERATE / LOAD
→ ARM
→ BURN ONCE
```

`ARM` is one-shot and is consumed by one `BURN ONCE`.

A new burn requires a new valid fit/preparation and a new ARM.

## Hardware ownership

- SMC100A IOC → R&S SMC100A
- Keysight WaveGen IOC → Keysight DSOX3024T WaveGen
- NMR switch IOC → receiver-protection Mini-Circuits switch
- ssRF switch IOC → burn-path Mini-Circuits switch
- Interlock → safety decisions only
- Sequencer → coordinated execution only
- Fitter / Burn Setup → analysis and waveform planning only

No legacy/demo IOC participates in final physical operation.

## Burn Setup

Operator-facing terminology is **Burn Setup**.

The internal EPICS namespace remains:

```text
UNH:GEN:SSRF:RECIPE:*
```

for compatibility with the validated implementation.

Burn Setup includes material, objective, current polarization from the latest valid fitter result, reference polarization, carrier frequency, start RF level, target RF level, and FM repeat count.

Derived/read-only values include FM period, sweep dwell, waveform points, VLOW, VHIGH, and preparation state.

## Validated waveform contract

```text
Waveform points = 8192
T_FM            = 0.010 s
VLOW            = -0.7 V
VHIGH           = +0.7 V
```

Sweep dwell is derived from:

```text
T_dwell = N_FM × T_FM
```

For the validated commissioning case:

```text
N_FM    = 10
T_FM    = 0.010 s
T_dwell = 100 ms
```

## GENERATE / LOAD boundary

`GENERATE / LOAD` performs source preparation only.

During preparation, SMC RF, SMC FM, and WaveGen output remain OFF; the SMC sweep remains DISARMED; NMR remains connected; ssRF remains disconnected; and execution remains DISARMED.

The adaptive 8192-point waveform passes through the authorized analysis → sequencer → WaveGen staging boundary.

## Validated one-shot burn sequence

1. Verify prepared state.
2. Consume one-shot ARM.
3. Disconnect NMR.
4. Verify receiver protection.
5. Connect ssRF.
6. Verify routing.
7. Enable WaveGen output.
8. Enable SMC external FM.
9. Verify RF-enable permit.
10. Enable SMC RF.
11. Arm the SMC sweep.
12. Trigger one sweep.
13. Wait for the planned sweep interval.
14. RF OFF.
15. FM OFF.
16. WaveGen output OFF.
17. Positively verify all source outputs OFF.
18. Disconnect ssRF.
19. Restore NMR.
20. Verify NMR live.
21. COMPLETE and disarm.

## Fail-safe cleanup

Abort/failure cleanup targets:

```text
SMC sweep       = DISARMED
RF              = OFF
FM              = OFF
WaveGen output  = OFF
ssRF            = disconnected
NMR             = restored whenever safely possible
```

NMR restoration occurs only after the burn path is confirmed disconnected and source outputs are confirmed OFF.

## Final validated state

```text
EXECUTION_STATE   = COMPLETE
EXECUTION_ARMED   = 0
RF                 = OFF
FM                 = OFF
SMC sweep          = DISARMED
WaveGen output     = OFF
NMR switch mask    = 0
ssRF switch mask   = 3
Interlock state    = NMR_LIVE
```

The downstream/LZY amplifier remained unpowered for this validation.

## EPICS transport

The final Phoebus main and detail GUIs use Channel Access (`ca://`) with the dedicated physical CA port list.

PVA is not required for the validated operator path.

## Access security

Protected hardware command PVs remain behind EPICS access security.

The burn analysis layer does not directly command the physical WaveGen. The adaptive waveform passes through the authorized sequencer staging request and is forwarded to the protected WaveGen staging command.

The physical sequencer owns the one-shot ARM and coordinated execution boundary.

## Logging

Final physical operation records sequencer/session JSON event logging, per-PV CSV archiving, hardware IOC logs, and fitter logs.

## Regression

Final repository regression before closeout:

```text
357 passed, 17 subtests passed
```

## Tags

The earlier frozen tag remains unchanged:

```text
ssrf-phase1-no-rf-2026-09-28
```

Do not move or rewrite that tag.

The final physical source-control milestone uses:

```text
ssrf-phase1-source-control-2026-09-28
```

## Next hardware milestone

The next ssRF hardware stage is powered downstream RF-chain commissioning: amplifier power supply, LZY amplifier, tuner/SWR path, calibrated RF power measurements, and target-connected validation.

## Guarded manual RF operation (software validation pending physical acceptance)

The sequencer also exposes MANUAL_ARM_CMD, MANUAL_DISARM_CMD, RF_ON_CMD,
RF_OFF_CMD, FM_ON_CMD and FM_OFF_CMD under UNH:GEN:SSRF:SEQ.
MANUAL_ARMED, MANUAL_STATE, MANUAL_REASON and MANUAL_FAULT describe this mode.
This arm is separate from the existing adaptive-burn ARM_CMD. Both arms start
false; accepted manual RF ON consumes its arm. No operation resumes on recovery.

Set SEQ:CARRIER_MHZ and SEQ:TARGET_LEVEL_DBM deliberately before manual ARM.
Manual RF follows DISARMED → ARMED → RF_ON → optional RF_FM_ACTIVE.
RF ON validates required SMC/switch communications, write permission, interlock
inputs/topology and source-OFF readbacks. It configures/validates carrier and
level with outputs OFF, disconnects NMR, checks SSRF_CONNECT_PERMIT, connects
ssRF, rechecks safety and RF_ENABLE_PERMIT, and enables/verifies RF.
WaveGen must be communicative with output confirmed OFF for source safety;
its write permission, adaptive waveform, fitter and calibration are not RF
prerequisites. FM and sweep remain OFF/disarmed in plain RF.

For operator-selected FM, explicitly stage a normalized 8192-point waveform
through SEQ:WAVEFORM_STAGE_REQ while idle, select the sequencer FM parameters,
and invoke PREPARE_CMD before manual ARM. This existing preparation path loads
and verifies the waveform with all source outputs OFF. It does not require a
fitter result. FM ON verifies the prepared signature, waveform identity/points,
external FM source/deviation, voltage/period readbacks, communications/write
permission and permits before WaveGen ON then FM ON. It never triggers a sweep.
FM OFF turns FM and WaveGen OFF while retaining RF. Failed transitions request
universal cleanup. Adaptive GENERATE / LOAD still requires calibrated fitter
data; that analysis requirement does not authorize or block manual RF.

Sustained manual operation checks communications, permissions, topology,
RF_ENABLE_PERMIT, RF/FM/WaveGen/sweep readbacks and source configuration.
FM additionally monitors the loaded waveform identity and FM configuration.
Loss of required safety invokes cleanup and requires explicit re-arm.
These checks use the IOC polling loop, not an independent hardware safety loop;
EPICS/hardware timeouts bound response latency and require physical acceptance.

RF OFF, either DISARM, and ABORT request common safe-off even while idle.
Cleanup independently attempts sweep disarm, RF OFF, FM OFF and WaveGen OFF,
then verifies their readbacks before switch motion. It disconnects ssRF,
requires NMR_CONNECT_PERMIT and restores NMR (RF mask 3, NMR mask 0).
Unverified OFF state blocks routing changes and reports CLEANUP_FAILED.
SAFE_OFF_ID counts completed cleanup attempts, including failed attempts.
MANUAL_FAULT preserves the last manual failure across cleanup; explicit
successful manual ARM clears it.

STOP requires fresh sequencer safe-off acknowledgment and independently checks
the baseline before terminating owners. If the sequencer is genuinely unavailable
and exclusive control ownership is acquired, it uses the same protected
OFF/verification/routing plan through device IOCs. A reachable sequencer with
execution disabled blocks fallback and retains owners.
A detected sequencer owner with unreachable execution PV blocks the fallback
to avoid concurrent writers.
If acknowledgment, device access or baseline verification fails, STOP exits
nonzero before any owner termination. It does not guess missing device state.

Expert output/routing controls are disabled in the GUI. The command ACF remains
unchanged: direct CA clients with an allowed identity can still bypass ordering.
CA identity and exclusive command ownership remain physical acceptance items;
GUI disabling is not an access-security boundary. The reusable subsystem-scoped
Phoebus shutdown helper from the Microwave branch is absent here. STOP does not
add Phoebus termination; integration of that validated helper is a dependency.

## Metadata verification and exclusive lifecycle ownership

Production safety reads use aioca 1.8.1 `FORMAT_TIME`, requiring actual status,
severity and successful scalar value. Missing metadata, any alarm, failed reads
and nonfinite/unusable values fail closed. Ordinary raw reads remain available
for non-safety callers. Cleanup/shutdown cannot treat an alarmed cached OFF as
confirmation. Source verification still precedes all cleanup routing.

`/tmp/genesis-ssrf-physical/lifecycle.lock` excludes concurrent physical START
and STOP through owner termination. The physical sequencer holds `control.lock`
for its lifetime. Fallback cleanup must acquire that same control lock; process
snapshots alone never authorize fallback. Independently launched sequencers
participate in lifecycle acquisition before control ownership. Locks are Unix
`fcntl.flock` locks, released on close/process death; files are not deleted.
Startup children validate a matching live parent PID/token lease; stale inherited
environment markers do not bypass lifecycle acquisition after startup.
Simulator/unit tests use isolated injected paths and never take production locks.

The complete-system launcher now observes local process/session liveness when
reusing a running stack. It does not invoke safe-OFF preflight, directly probe
instruments, change operation, or require outputs OFF. Remote Keysight health
is not established by this local reuse check. Explicit physical diagnostics
remain separate. See the current `SSRF_OPERATOR_QUICK_START.md`.
