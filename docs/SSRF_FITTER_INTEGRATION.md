# Genesis Phase-I ssRF Fitter Integration

## Purpose

Integrate the existing UNH deuteron NMR fitter and adaptive ssRF waveform
generation into the Genesis Phase-I architecture without allowing analysis
software to bypass EPICS safety, interlocks, authorization, or the ssRF
sequencer.

Authoritative reference source:

`Genesis-Mission-ssRF-Controller-main_Utsav.zip`

The reference implementation contains:

- `online_fitter/` — live/offline spin-1 NMR fitting
- `Keysight/ssRF_sweep_height.py` — adaptive ssRF waveform generation
- `Keysight/deuts.py` — legacy waveform-physics helper
- direct Keysight/device-control code used by the older prototype

Only the hardware-independent fitting and waveform mathematics are reused
in the Genesis analysis layer.

## Genesis architecture

```text
NMR acquisition
      |
      v
online_fitter
      |
      v
validated FitSnapshot
      |
      v
ssrf_analysis.waveform_planner
      |
      v
validated WaveformPlan
      |
      v
WaveGen waveform staging
      |
      v
Genesis sequencer
      |
      v
Genesis interlock / authorization
      |
      v
Keysight + SMC100A + RF switches
```

Analysis determines what waveform should be used.

Analysis does not determine whether hardware is allowed to execute it.

The Genesis sequencer and interlock remain authoritative for all hardware
execution.

## Imported fitter

The authoritative `online_fitter` package was copied source-identically
into the Genesis repository.

Core dependencies:

- Python
- NumPy
- SciPy

GUI dependencies are treated separately from the core fitter.

The fitter supports:

- baseline
- equilibrium
- ssRF / hole-burn
- frequency hopping
- no-NMR operation

The live fitter protocol remains loopback-only.

Default fitter input port:

`127.0.0.1:8777`

Default fit-result publisher:

`127.0.0.1:8778`

## Adaptive waveform planner

Genesis provides:

`ssrf_analysis/waveform_planner.py`

It reproduces the adaptive waveform mathematics from the authoritative
`Keysight/ssRF_sweep_height.py` implementation while removing:

- PyVISA communication
- direct Keysight writes
- RF output control
- switch control
- continuous burn loops
- automatic burn execution

The planner supports:

- ND3
- deuterated butanol
- Enhancement
- Suppression
- Zero

It produces:

- 8192 normalized ARB samples
- physical-voltage representation
- peak and shoulder frequency regions
- height-weighting arrays
- waveform period
- WaveGen voltage bounds
- fitter provenance information

Regression tests compare the Genesis waveform directly against the
authoritative implementation.

## Current validated constants

The authoritative prototype currently uses:

```text
WaveGen ARB points = 8192
Waveform period    = 0.010 s
VHIGH              = +0.7 V
VLOW               = -0.7 V
```

Normalized samples supplied to the Genesis WaveGen IOC remain:

```text
[-1, +1]
```


## Safe waveform staging boundary

Genesis provides:

`ssrf_analysis/waveform_stager.py`

The stager has one purpose only: transfer an already validated normalized
waveform into the existing WaveGen IOC in-memory staging record.

Its only permitted staging target is:

    UNH:GEN:SSRF:WGEN:WAVEFORM_CMD

The stager validates:

- the `WaveformPlan` itself
- exactly 8192 adaptive-waveform points
- all samples are finite
- all samples lie in `[-1, +1]`

The stager does not have authority to issue:

- `LOAD_CMD`
- `OUTPUT_CMD`
- `PERIOD_CMD`
- `VHIGH_CMD`
- `VLOW_CMD`
- SMC100A commands
- RF/NMR switch commands
- ARM
- START

`WAVEFORM_CMD` changes IOC memory only. It does not commit an ARB waveform
to the physical Keysight.

This preserves an explicit separation between:

    PLAN -> STAGE

and the independently controlled execution path:

    LOAD -> READY -> ARM -> START


## Fitter publisher to waveform bridge

Genesis provides:

`ssrf_analysis/fitter_waveform_bridge.py`

This bridge consumes the existing online-fitter publisher rather than
creating a new fitter interface.

Publisher endpoint:

    127.0.0.1:8778

Transport:

    TCP
    newline-delimited JSON
    one fitter-result object per line

The bridge is deliberately restricted to loopback hosts.

The validated analysis path is:

    fitter publisher
        -> FitSnapshot
        -> WaveformPlan
        -> WaveformStager
        -> WAVEFORM_CMD

Only messages with:

    type = fit

are accepted.

Automatic waveform generation additionally requires calibrated fitter
constants:

    constants_good = true

An uncalibrated fit is rejected before the staging writer is called.

The bridge itself has no EPICS execution authority. It cannot issue:

- `LOAD_CMD`
- `OUTPUT_CMD`
- SMC100A RF/FM commands
- NMR or ssRF switch commands
- ARM
- START

This keeps live NMR analysis logically upstream of the existing Genesis
sequencer, interlock, authorization, and device layers.

## Important unresolved hardware item

The authoritative source numerically maps frequency offset in MHz to
WaveGen voltage.

That numerical convention is preserved for source equivalence but is NOT
yet independently hardware-verified in Genesis.

Therefore:

`scaling_verified_for_hardware = False`

The physical relationship among:

- NMR frequency offset
- WaveGen voltage
- SMC100A external-FM deviation

must be confirmed during physical commissioning before real ssRF burns.

## Safety boundary

The fitter, waveform planner, and staging bridge may never directly perform:

- `LOAD_CMD`
- WaveGen output enable
- SMC RF enable
- SMC FM enable
- RF-switch changes
- NMR-switch changes
- ARM
- START
- burn execution

Those operations belong to the existing Genesis controlled execution path.

## Current milestone status

Validated:

- authoritative fitter source located
- fitter source imported source-identically
- SciPy environment dependency installed
- fitter imports successfully
- loopback-only fitter server verified
- non-loopback binding rejected
- legacy `deuts.py` preserved source-identically
- adaptive waveform planner extracted
- six material/case waveform combinations match authoritative source
- live measured-height waveform path matches authoritative source
- uncalibrated fit input is rejected
- safe `WAVEFORM_CMD` staging boundary implemented
- staging authority contains exactly one IOC-memory PV
- invalid plans are rejected before staging
- non-8192 adaptive waveforms are rejected
- out-of-range samples are rejected
- fitter publisher endpoint fixed to loopback `127.0.0.1:8778`
- non-loopback publisher hosts are rejected
- non-fit publisher messages are rejected
- calibrated fitter result generates exactly one 8192-point staging operation
- uncalibrated fitter result generates zero staging operations
- existing ssRF safety/sequencer regression tests remain passing

Next:

1. consume the existing fit-result publisher at `127.0.0.1:8778`
2. convert each calibrated fit message into `FitSnapshot`
3. generate `WaveformPlan`
4. pass the plan through the safe stager
5. validate this path without EPICS hardware execution
6. validate IOC-memory staging with the WaveGen simulator
7. add fit/waveform event metadata
8. add operator helper commands after the runtime workflow is validated
