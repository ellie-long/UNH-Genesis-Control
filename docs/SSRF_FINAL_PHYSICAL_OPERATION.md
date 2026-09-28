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

## Final operator GUI workflow

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
