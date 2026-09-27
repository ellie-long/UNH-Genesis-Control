# Genesis ssRF Fitter Interface Plan

## Purpose

Define the fitter, planner, sequencer-staging, and WaveGen IOC-memory interfaces without granting hardware-execution authority.

## Fitter publisher

```text
127.0.0.1:8778
TCP
newline-delimited JSON
```

Automatic waveform generation accepts only:

```text
type = fit
constants_good = true
```

## FitSnapshot

`FitSnapshot` is the validated fitter-to-planner boundary and includes the material/model/calibration state, fitted constants, processed frequency/data arrays, timestamp, and fit type required by the planner.

## WaveformPlan

Relevant fields include material/case, fit provenance, fitted frequencies, peak/shoulder regions and weights, control time/offset, normalized samples, physical-voltage representation, period and voltage bounds, point count, and scaling provenance.

Adaptive requirements:

```text
waveform_points = 8192
all samples finite
all samples in [-1, +1]
```

## Polarization boundary

Fitter polarization outputs are percentages.

Planner `polarization` and `high_polarization` inputs are explicit fractions and are required arguments.

## Analysis staging interface

The analysis layer writes only:

```text
UNH:GEN:SSRF:SEQ:WAVEFORM_STAGE_REQ
```

It does not write:

```text
UNH:GEN:SSRF:WGEN:WAVEFORM_CMD
```

directly.

## Sequencer staging gateway

The sequencer validates:

```text
exactly 8192 points
all values finite
all values in [-1, +1]
```

The sequencer rejects staging while burn execution is starting/running, and START is blocked while staging is active.

Staging itself does not require ARM because it changes IOC memory only.

## WaveGen IOC-memory interface

Protected command:

```text
UNH:GEN:SSRF:WGEN:WAVEFORM_CMD
```

Readbacks/acknowledgment:

```text
UNH:GEN:SSRF:WGEN:WAVEFORM_POINTS
UNH:GEN:SSRF:WGEN:WAVEFORM_STAGE_ID
```

Protocol:

```text
baseline_id = WAVEFORM_STAGE_ID

write WAVEFORM_CMD

wait until:
    WAVEFORM_STAGE_ID > baseline_id
    and
    WAVEFORM_POINTS == 8192
```

Only then may the sequencer report `STAGED`.

## Stage versus load

Staging:

```text
WAVEFORM_STAGE_ID increments
WAVEFORM_POINTS = 8192
```

Separate instrument load:

```text
WAVEFORM_ID
ARB_POINTS_RBV
WAVEFORM_READY
```

A staging request must never issue `LOAD_CMD`.

Validated simulator result after two stages:

```text
SEQ WAVEFORM_STAGE_ID  = 2
WGEN WAVEFORM_STAGE_ID = 2
WAVEFORM_POINTS        = 8192
WAVEFORM_ID            = 0
ARB_POINTS_RBV         = 0
WAVEFORM_READY         = 0
```

## Prohibited analysis authority

No direct analysis authority for:

```text
LOAD_CMD
OUTPUT_CMD
PERIOD_CMD
VHIGH_CMD
VLOW_CMD
SMC commands
switch commands
ARM
START
burn execution
```

## Fitter EPICS / Phoebus interface

Prefix:

```text
UNH:GEN:SSRF:FIT
```

Primary controls:

```text
MODE_CMD
MATERIAL_CMD
LARMOR_KHZ_CMD
UPDATE_CONSTANTS_CMD
```

Primary status/readback:

```text
FITTER_UP
PUBLISHER_CONNECTED
MODE_RBV
MATERIAL_RBV
LARMOR_KHZ_RBV
CALIBRATED
UPDATE_BUSY
CONSTANTS_SOURCE
FIT_TYPE
MODEL
NPTS
WARNING
LAST_TS
```

Operator page:

```text
gui/genesis_ssrf_fitter.bob
```

## Physical scaling gate

Until the current physical relation among NMR frequency offset, WaveGen voltage, and SMC100A external-FM deviation is measured:

```text
scaling_verified_for_hardware = False
```

## Validated progression

```text
fitter publisher
      |
      v
calibrated fit
      |
      v
FitSnapshot
      |
      v
WaveformPlan
      |
      v
SEQ:WAVEFORM_STAGE_REQ
      |
      v
sequencer validation
      |
      v
protected WGEN:WAVEFORM_CMD
      |
      v
WGEN:WAVEFORM_STAGE_ID acknowledgment
      |
      v
IOC-memory STAGED

-------------- separate controlled step --------------

LOAD_CMD -> instrument ARB / READY -> interlocks / ARM / START
```

## Pre-lab status

Validated:

- calibrated fitter handling;
- `FitSnapshot` creation;
- 8192-point adaptive waveform generation;
- operator calibration through Phoebus;
- analysis-to-sequencer staging;
- sequencer-only protected WaveGen forwarding;
- explicit WaveGen stage acknowledgment;
- two consecutive fresh stage acknowledgments;
- no LOAD/output/RF/FM/ARM during staging.

Next: physical current calibration, physical IOC-memory staging, external-FM scaling verification, controlled LOAD, and physical interlock/logging validation.
