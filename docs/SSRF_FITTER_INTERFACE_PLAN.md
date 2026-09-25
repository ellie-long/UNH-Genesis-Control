# Genesis ssRF Fitter Interface Plan

## Purpose

Define the data interface between the online NMR fitter, the adaptive ssRF
waveform planner, and the existing Genesis WaveGen staging layer.

This document describes what data are accepted, validated, produced, and
passed downstream. It does not grant hardware-execution authority.

## Input: online fitter result

The waveform planner consumes one calibrated fitter-publisher message.

Required high-level fields:

```text
type
timestamp
material
model
constants_good
constants
```

The fit must have:

```text
constants_good = true
```

Uncalibrated or preset-only fits are rejected for automatic waveform
generation.

## Required fit constants

Common:

```text
omegD
omegQ1
A1
eta1
scale
phase
r
```

Butanol additionally requires:

```text
omegQ2
eta2
K
```

Optional measured-data arrays:

```text
freqs_mhz
data
```

If valid measured NMR data are supplied, they are used for adaptive
per-frequency-bin weighting.

If measured data are unavailable, the fitted model is used.

## Genesis FitSnapshot

`FitSnapshot` is the validated boundary between the fitter and the ssRF
waveform planner.

It contains:

```text
material
model
constants_good
constants
freqs_mhz
data
timestamp
fit_type
```

## Genesis WaveformPlan

The waveform planner returns a hardware-independent `WaveformPlan`.

Important fields:

```text
material
case

fit_ts
fit_type

omegD_mhz
frequency_shift_mhz

f_peak_mhz
f_shoulder_mhz

peak_weights
shoulder_weights
peak_time_fraction

control_time_s
control_offset_mhz

normalized_samples
physical_voltage_v

period_s
vhigh_v
vlow_v

waveform_points
r_high

used_measured_heights

legacy_offset_mhz_per_v
scaling_verified_for_hardware
```

## Current WaveGen staging interface

The existing Genesis Keysight IOC accepts normalized waveform data at:

```text
UNH:GEN:SSRF:WGEN:WAVEFORM_CMD
```

Requirements:

```text
2 <= number of points <= 8192
all values finite
all values in [-1, +1]
```

For the adaptive fitter-generated waveform:

```text
waveform_points = 8192
```

`WAVEFORM_CMD` stages waveform data in IOC memory.

It does not itself commit the waveform to the physical Keysight.

Physical waveform commit is a separate operation and remains under the
Genesis execution and safety architecture.



## Fitter-to-waveform bridge

The Genesis fitter bridge consumes the existing loopback-only online-fitter
publisher:

    127.0.0.1:8778

Transport:

    TCP
    newline-delimited JSON
    one fitter-result object per line

The bridge accepts only:

    type = fit

Automatic waveform generation additionally requires:

    constants_good = true

The validated software path is:

    fitter publisher
        -> FitSnapshot
        -> WaveformPlan
        -> WaveformStager
        -> WAVEFORM_CMD

An uncalibrated fitter result is rejected before the staging writer is
called.

The bridge does not contain authority for:

- `LOAD_CMD`
- `OUTPUT_CMD`
- WaveGen scalar hardware commands
- SMC100A RF/FM commands
- NMR or ssRF switch commands
- ARM
- START
- burn execution

This keeps the analysis path upstream of the existing Genesis sequencer,
interlock, authorization, and hardware-control layers.

## Safe staging authority

During 5C.3A, the fitter integration staging layer is intentionally limited
to exactly one IOC-memory target:

    UNH:GEN:SSRF:WGEN:WAVEFORM_CMD

It must not directly issue:

- `LOAD_CMD`
- `OUTPUT_CMD`
- `PERIOD_CMD`
- `VHIGH_CMD`
- `VLOW_CMD`
- SMC RF commands
- SMC FM commands
- RF/NMR switch commands
- ARM
- START

The staging operation therefore prepares waveform data without authorizing
or initiating hardware execution.

## Execution separation

The analysis layer may generate:

```text
waveform samples
waveform period
WaveGen voltage bounds
frequency regions
fit metadata
```

Analysis must not automatically issue physical commands.

The intended controlled progression is:

```text
FIT
 -> PLAN
 -> STAGE
 -> VALIDATE
 -> LOAD
 -> READY
 -> ARM
 -> START
```

Each later transition remains independently gated.

## Current safe staging authority

During 5C.3A, the fitter integration bridge is intentionally limited to:

```text
UNH:GEN:SSRF:WGEN:WAVEFORM_CMD
```

It must not directly issue:

```text
LOAD_CMD
OUTPUT_CMD
PERIOD_CMD
VHIGH_CMD
VLOW_CMD

SMC RF commands
SMC FM commands
switch commands
ARM
START
```

This preserves the separation between analysis and hardware execution.

## Physical scaling status

The legacy waveform implementation uses a numeric convention equivalent
to:

```text
1 MHz offset -> 1 V numeric waveform value
```

This convention is retained for source equivalence.

It has not yet been independently verified against the current physical
Keysight / SMC100A external-FM configuration.

Therefore:

```text
scaling_verified_for_hardware = False
```

Physical execution must remain blocked until the frequency-offset,
WaveGen-voltage, and SMC external-FM mapping is verified during physical
commissioning.

## Phase-I status

Validated so far:

- calibrated fitter-result validation
- `FitSnapshot` creation
- ND3 and deuterated-butanol support
- Enhancement, Suppression, and Zero waveform cases
- 8192-point normalized waveform generation
- measured-NMR height weighting
- fitted-model fallback weighting
- numerical agreement with the authoritative Utsav/Chhetra implementation
- safe single-PV IOC-memory waveform staging
- rejection of invalid, non-finite, out-of-range, or wrong-length staging data
- existing Genesis ssRF safety regressions remain passing

Next interface steps:

1. safe WaveGen IOC-memory staging
2. fitter publisher subscription
3. simulator-only end-to-end validation
4. fit/waveform event metadata
5. operator helper commands
6. physical scaling verification during hardware commissioning

## 5C.3A validated interface state

The validated Phase-I software interface is:

    127.0.0.1:8778
        -> calibrated fit message
        -> FitSnapshot
        -> WaveformPlan
        -> WaveformStager
        -> WAVEFORM_CMD

The runtime consumes one newline-delimited JSON fitter result and exits after
processing one accepted fit.

Default runtime behavior is dry-run.

The only EPICS staging target implemented in the runtime is:

    UNH:GEN:SSRF:WGEN:WAVEFORM_CMD

A controlled simulator validation confirmed:

    WAVEFORM_POINTS = 8192
    WAVEFORM_READY  = 0
    WAVEFORM_ID     = unchanged
    ARB_POINTS_RBV  = unchanged
    OUTPUT_RBV      = 0

Therefore IOC-memory staging and physical waveform loading remain distinct
operations.

The fitter runtime does not implement authority for:

- `LOAD_CMD`
- `OUTPUT_CMD`
- WaveGen scalar hardware commands
- SMC100A RF/FM commands
- RF/NMR switch commands
- ARM
- START
- burn execution

The `--stage` runtime option is a commissioning mechanism, not a normal
operator command at the 5C.3A freeze.

The normal Genesis execution and access-security architecture remains
authoritative for later physical execution.

Physical MHz-to-voltage-to-external-FM scaling remains unverified and must
remain blocked from real burn execution until physical commissioning.

Live NMR input is deferred to 5C.3B.
