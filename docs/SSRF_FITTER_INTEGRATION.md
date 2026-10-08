# Genesis Phase-I ssRF Fitter Integration

## Purpose

Integrate the UNH spin-1 NMR fitter and adaptive ssRF waveform generation into Genesis without allowing analysis software to bypass EPICS authorization, the ssRF sequencer, interlocks, or device write gates.

## Validated PyNMR TestSim interface

The software-only PyNMR TestSim -> Genesis fitter path has been validated independently of the physical ssRF hardware stack.

Operational reproduction and validation instructions are maintained in `docs/PYNMR_FITTER_TESTSIM_QUICK_START.md`.

The accepted Genesis milestone is tagged `pynmr-fitter-integration-validated-2026-10-08`.

## Architecture

```text
NMR acquisition
      |
      v
online_fitter
      |
      v
FitSnapshot
      |
      v
waveform planner
      |
      v
WaveformPlan
      |
      v
analysis stager
      |
      v
SEQ:WAVEFORM_STAGE_REQ
      |
      v
Genesis ssRF sequencer
      |
      v
protected WGEN:WAVEFORM_CMD
      |
      v
WaveGen IOC memory

---------------- separate boundary ----------------

LOAD_CMD -> instrument ARB -> READY -> ARM -> START
```

Analysis determines the proposed waveform. Genesis determines whether it may be staged, loaded, and executed.

## Online fitter

```text
input server   127.0.0.1:8777
publisher      127.0.0.1:8778
```

Supported modes include baseline, equilibrium, `ssRF`, frequency hopping, and no-NMR. In `ssRF` mode, `fit_type=hole-burnt` is expected for hole-burned fitting.

## Fitter EPICS / Phoebus interface

Prefix:

```text
UNH:GEN:SSRF:FIT
```

Important controls/readbacks include mode, material, Larmor, `Update Constants`, calibration state, fit type/model, polarization/tensor quantities, fitted constants, warnings/timestamps, and NMR/fit arrays.

Operator page:

```text
gui/genesis_ssrf_fitter.bob
```

The fitter IOC and GUI contain no WaveGen, SMC, switch, ARM, START, or burn authority.

## Calibration

Use equilibrium mode with a representative equilibrium/reference plateau, fill the recent equilibrium buffer, then issue `Update Constants`.

Successful calibration establishes fitted constants and reports:

```text
CALIBRATED = 1
CONSTANTS_SOURCE = fit
constants_good = true
```

Replay/demo calibration is software validation only. A current physical equilibrium/reference calibration is still required in the lab.

## Polarization units

Fitter polarization outputs are percent-valued.

Planner inputs:

```text
polarization
high_polarization
```

are explicit fractions. Do not silently convert fitter percentages into planner fractions.

## Waveform planning

Adaptive fitter-generated waveforms contain exactly:

```text
8192 normalized samples
```

with every sample finite and inside:

```text
[-1, +1]
```

Validated cases:

```text
Enhancement
Suppression
Zero
```

Automatic planning requires a calibrated fitter result:

```text
type = fit
constants_good = true
```

## Authorized staging boundary

The analysis layer must not write the protected WaveGen record directly.

Analysis target:

```text
UNH:GEN:SSRF:SEQ:WAVEFORM_STAGE_REQ
```

Sequencer forwarding target:

```text
UNH:GEN:SSRF:WGEN:WAVEFORM_CMD
```

The sequencer independently validates exact point count, finiteness, and normalized range before forwarding.

## Explicit IOC-memory acknowledgment

WaveGen exposes:

```text
UNH:GEN:SSRF:WGEN:WAVEFORM_STAGE_ID
```

The staging protocol is:

```text
read baseline WAVEFORM_STAGE_ID = N
write WAVEFORM_CMD
WaveGen validates/copies waveform
WAVEFORM_POINTS = 8192
WAVEFORM_STAGE_ID = N + 1
sequencer reports STAGED
```

The sequencer requires a fresh stage ID plus `WAVEFORM_POINTS=8192`, preventing stale data from satisfying a new request.

## Stage versus load

IOC-memory stage state:

```text
WAVEFORM_STAGE_ID
WAVEFORM_POINTS
```

Instrument-load state:

```text
WAVEFORM_ID
ARB_POINTS_RBV
WAVEFORM_READY
```

During validated staging-only simulator tests:

```text
two consecutive stage requests succeeded
SEQ WAVEFORM_STAGE_ID  = 2
WGEN WAVEFORM_STAGE_ID = 2
WAVEFORM_POINTS        = 8192

WAVEFORM_ID            = 0
ARB_POINTS_RBV         = 0
WAVEFORM_READY         = 0

WaveGen output          = OFF
SMC RF                  = OFF
SMC FM                  = OFF
sequencer ARM           = OFF
RF routes               = SAFE_IDLE
```

Thus staging did not load the simulated instrument.

## Prohibited analysis authority

The fitter/planner/analysis stager may not directly issue:

```text
LOAD_CMD
OUTPUT_CMD
PERIOD_CMD
VHIGH_CMD
VLOW_CMD
SMC RF/FM commands
NMR/ssRF switch commands
ARM
START
burn execution
```

## Physical scaling gate

The physical relationship among:

```text
NMR frequency offset
WaveGen output voltage
SMC100A external-FM deviation
```

is not yet independently verified on the current physical system.

Therefore:

```text
scaling_verified_for_hardware = False
```

No physical burn should be performed until that mapping is measured and validated.

## Validated pre-lab status

Validated:

- fitter source integration and loopback runtime;
- fitter EPICS IOC;
- Phoebus fitter operator interface;
- replay through fitter EPICS;
- `Update Constants` through Phoebus/EPICS;
- calibrated `ssRF` / `hole-burnt` fit;
- `constants_good=true`;
- explicit planner polarization fractions;
- Enhancement/Suppression/Zero 8192-point plans;
- analysis-to-sequencer staging request;
- sequencer-only protected WaveGen forwarding;
- explicit WaveGen stage acknowledgment;
- two consecutive fresh acknowledgments;
- no `LOAD_CMD` during staging;
- no `WAVEFORM_ID` or ARB change during staging;
- `WAVEFORM_READY=0`;
- WaveGen output and SMC RF/FM remained OFF;
- sequencer remained DISARMED;
- RF routing remained SAFE_IDLE;
- physical hardware was not contacted during simulator validation.

## Next physical steps

```text
1. restore physical Keysight read-only IOC
2. verify physical IDs/readbacks
3. establish current equilibrium/reference calibration
4. generate calibrated WaveformPlan
5. stage through SEQ:WAVEFORM_STAGE_REQ
6. verify physical IOC-memory ACK
7. keep LOAD separate
8. verify external-FM scaling
9. controlled physical LOAD
10. physical logging/fault/interlock validation
11. full physical loop after all gates pass
```
