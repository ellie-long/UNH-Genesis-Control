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

## Final acceptance completed — 2026-09-28

The Phase-I physical no-RF operator path was accepted in the lab and then
reproduced from a clean start.

Canonical operator path:

```text
genesis ssrf-physical-status
genesis ssrf-demo-start
START FITTER → REPLAY EQ → CALIBRATE → ARM → BLAST/BURN
genesis archive-viewer
genesis ssrf-demo-stop
genesis ssrf-physical-status
```

Acceptance evidence:

- physical Keysight accepted the fresh 8192-point adaptive ARB waveform;
- `WAVEFORM_POINTS = 8192`, `ARB_POINTS_RBV = 8192`,
  `WAVEFORM_READY = 1`, and WaveGen output remained OFF;
- both physical Mini-Circuits switches audibly/visibly actuated;
- automatic restoration returned NMR mask to 0 and ssRF mask to 3;
- SMC RF and FM remained OFF throughout;
- the workflow reached `COMPLETE` with ARM consumed;
- correlated fit/waveform/session data were written and visible in the viewer;
- successful session `ssrf-20260928T171935Z-4137581b` was followed by a clean
  reproducibility run `ssrf-20260928T173405Z-bd5ae96e`;
- the final reproducibility session contains both `fit_data.npz` and
  `waveform_data.npz` and records `session_completed` with status `success`;
- final shutdown left no workflow/fitter runtime, no physical Keysight upload
  IOC, no screen session, SMC RF/FM OFF, NMR mask 0, ssRF mask 3, and
  WaveGen output OFF.

No additional ssRF physical no-RF laboratory run is required for this
Phase-I milestone after the office-only hardening/regression closeout.

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
