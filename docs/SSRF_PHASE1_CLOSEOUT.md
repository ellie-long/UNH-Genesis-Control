# ssRF Phase-I Control/Integration Closeout

## Current validated boundary

The current ssRF Phase-I control/integration scope has demonstrated:

- SMC100A physical communication and safe readback/control commissioning;
- Keysight DSOX3024T WaveGen physical communication;
- validated 8192-point ARB upload path;
- modulation-voltage calibration;
- external-FM static calibration;
- physical NMR switch control/readback;
- physical ssRF switch control/readback;
- interlock topology and safe ordering;
- fitter/calibration integration;
- adaptive ssRF waveform planning;
- one-shot ARM authorization;
- one-button BLAST/BURN physical no-RF workflow;
- automatic NMR disconnect / ssRF connect / restore;
- final safe restoration;
- integrated 1920 x 1080 ssRF operator GUI;
- detail screens opening as tabs in one Phoebus instance;
- structured ssRF event/session logging and read-only session viewer.

## Final acceptance still required

Before formally freezing ssRF Phase-I and moving laboratory effort to Microwave, perform one final operator acceptance run using only the final operator entry points:

```text
genesis ssrf-physical-status
genesis ssrf-demo-start
genesis archive-viewer
genesis ssrf-demo-stop
```

Acceptance requires:

1. one clean Phoebus instance;
2. the main landscape ssRF screen;
3. detail screens opening as tabs;
4. successful START FITTER → REPLAY EQ → CALIBRATE → ARM → BLAST/BURN;
5. physical switch motion and restoration;
6. 8192-point physical Keysight waveform loaded with output OFF;
7. SMC RF OFF and FM OFF throughout;
8. NMR mask 0 and ssRF mask 3 restored at completion;
9. a correlated session/event record visible in the ssRF archive viewer;
10. safe shutdown and clean runtime state.

## Intentionally deferred amplified-RF / target work

The following are not required to close the present no-RF Phase-I control milestone:

- upper ssRF switch RF-through characterization with the proper 50-ohm hardware;
- powered LZY-22+ commissioning;
- SPD3303X-E operation under amplifier load;
- MFJ-945E tuning/orientation validation;
- amplified RF power measurements;
- target-connected ssRF burn;
- actual polarization manipulation.

These belong to a later amplified-RF/target commissioning milestone.

## Next subsystem after acceptance

After the ssRF acceptance run and documentation freeze, proceed to physical Microwave commissioning:

```text
SPD1305X
→ Vaunix/LabBrick
→ PCL602 robot
→ Microwave Supervisor
→ archiver/viewer
→ unified Microwave subsystem validation
```
