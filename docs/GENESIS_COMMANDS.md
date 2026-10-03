# Genesis Phase-I Operations Cheat Sheet

Repository:

```bash
conda activate genesis-p1
```

Use:

```bash
genesis help
```

# Operator command model

Generic `genesis start`, `genesis stop`, and `genesis force-stop` are retired and intentionally refuse execution.
Physical operation must use explicit subsystem commands.

```bash
genesis mw-start
genesis mw-status
genesis mw-rf-ready
genesis mw-stop
genesis ssrf-physical-status
genesis ssrf-deploy-keysight
genesis ssrf-start
genesis ssrf-stop
genesis status
genesis physical-status
genesis endpoints
```

The retired combined prototype used read-only defaults:

```text
device write_enabled = false
sequencer execution_enabled = false
initialize_on_connect = false
```

`genesis physical-status` is read-only and must not enable physical writes.

# Simulator workflow

Required localhost backends:

| Device | Endpoint |
|---|---|
| SMC100A simulator | `127.0.0.1:5025` |
| WaveGen simulator | `127.0.0.1:5027` |
| NMR switch simulator | `127.0.0.1:8081` |
| ssRF switch simulator | `127.0.0.1:8082` |

Check:

```bash
genesis sim-check
```

Start:

```bash
genesis sim-start
```

Status:

```bash
genesis sim-status
```

Stop:

```bash
genesis sim-stop
```

`sim-stop` must recognize simulator mode by the dedicated `ssrf-sim-manager` Screen session, discover the exact Screen identifiers returned by `screen -ls`, stop only:

```text
ssrf_sequencer
ssrf_interlock
ssrf_rf_switch
ssrf_nmr_switch
ssrf_keysight_wavegen
ssrf_smc100a
ssrf-sim-manager
```

and verify that no simulator IOC sessions remain.

It must not blindly kill normal Genesis sessions, restore normal Genesis automatically, or kill separately started localhost backend simulators.

# Fitter / adaptive waveform

Fitter endpoints:

```text
input      127.0.0.1:8777
publisher  127.0.0.1:8778
```

Fitter EPICS prefix:

```text
UNH:GEN:SSRF:FIT
```

Operator screen:

```text
gui/genesis_ssrf_fitter.bob
```

The fitter GUI provides mode/material controls, Larmor entry, `Update Constants`, calibration state, fit quantities/constants, and the NMR/fit plot. It has no direct hardware authority.

A successful calibration should report:

```text
CALIBRATED = 1
CONSTANTS_SOURCE = fit
constants_good = true
```

Replay/demo calibration is not a replacement for the current physical equilibrium/reference calibration.

Fitter polarization outputs are percent-valued. Planner `polarization` and `high_polarization` inputs are explicit fractions.

# Authorized waveform staging

Validated path:

```text
FitEngine / publisher
        |
        v
FitSnapshot
        |
        v
WaveformPlan
        |
        v
WaveformStager
        |
        v
UNH:GEN:SSRF:SEQ:WAVEFORM_STAGE_REQ
        |
        v
ssRF sequencer
        |
        v
UNH:GEN:SSRF:WGEN:WAVEFORM_CMD
        |
        v
WaveGen IOC memory
```

Adaptive waveform contract:

```text
8192 points
finite samples
all samples in [-1, +1]
```

Explicit IOC-memory acknowledgment:

```text
UNH:GEN:SSRF:WGEN:WAVEFORM_STAGE_ID
```

The sequencer declares staging complete only after:

```text
WAVEFORM_STAGE_ID increases
WAVEFORM_POINTS = 8192
```

This prevents stale waveform data from being mistaken for a fresh stage.

# Stage versus load

Staging only:

```text
WAVEFORM_STAGE_ID increments
WAVEFORM_POINTS = 8192
WAVEFORM_ID unchanged
ARB_POINTS_RBV unchanged
WAVEFORM_READY = 0
```

`LOAD_CMD` remains a separate controlled instrument-commit operation.

The analysis layer has no direct authority for:

```text
LOAD_CMD
OUTPUT_CMD
PERIOD_CMD
VHIGH_CMD
VLOW_CMD
SMC RF/FM commands
switch commands
ARM
START
```

# Validated pre-lab state

Validated off-lab:

- fitter EPICS interface;
- Phoebus fitter interface;
- `Update Constants` through EPICS/Phoebus;
- calibrated `ssRF` / `hole-burnt` publication;
- `constants_good=true`;
- Enhancement/Suppression/Zero 8192-point plans;
- explicit polarization inputs;
- analysis-to-sequencer staging boundary;
- sequencer-only WaveGen forwarding;
- explicit WaveGen IOC-memory stage acknowledgment;
- two consecutive fresh simulator stage acknowledgments;
- `LOAD_CMD` never issued during staging;
- `WAVEFORM_ID` unchanged;
- `ARB_POINTS_RBV` unchanged;
- `WAVEFORM_READY=0`;
- WaveGen output OFF;
- SMC RF/FM OFF;
- sequencer DISARMED;
- RF routing SAFE_IDLE;
- no physical hardware contacted during simulator validation.

# Physical commissioning order

```text
1. Restore/read physical Keysight IOC safely.
2. Verify physical IDs and readbacks.
3. Verify switch states and SMC RF/FM OFF.
4. Establish current equilibrium/reference calibration.
5. Generate calibrated WaveformPlan.
6. Stage through SEQ:WAVEFORM_STAGE_REQ.
7. Confirm physical IOC-memory stage acknowledgment.
8. Keep LOAD_CMD separate.
9. Verify WaveGen-to-SMC external-FM scaling.
10. Perform controlled LOAD validation.
11. Validate logging, fault handling, interlocks, and cleanup.
12. Only then proceed toward controlled ARM/START execution.
```

Until external-FM scaling is physically verified:

```text
scaling_verified_for_hardware = False
```

No real ssRF burn should be performed.

# Tests

```bash
genesis test-ssrf
genesis test-auth
genesis test-fitter
genesis test-all
genesis compile
genesis check
```

Before freezing:

```bash
git diff --check
python -m unittest discover -s tests -p 'test_*.py' -q
git status --short
git -C devices status --short
git stash list
```

Protected stash:

```text
stash@{0}: On feature/microwave-genesis-integration: WIP stop_genesis_mac.sh before ssRF integration
```

# Current checkpoint

```text
5C.3B-E1      fitter EPICS interface
5C.3B-E2      fitter Phoebus/operator interface
5C.3B-CAL-2   calibration through EPICS/Phoebus
5C.3B-2       calibrated fit -> 8192-point WaveformPlan
5C.3B-3A      authorized analysis -> sequencer staging boundary
5C.3B-3B-SIM  end-to-end simulator IOC-memory staging + fresh ACK
```

Next laboratory work is physical calibration, physical IOC-memory staging, controlled LOAD, external-FM scaling verification, and physical interlock/logging validation.


## Current physical ssRF operation

Complete system / non-invasive GUI reuse:

    ./scripts/start_ssrf_system.sh

Lower-level controls:

    ./scripts/genesisctl.sh ssrf-start
    ./scripts/start_ssrf_phoebus.sh
    ./scripts/genesisctl.sh ssrf-stop

Startup verifies safe source-OFF/disarmed state. Reopening a complete live
stack observes local processes/sessions only and preserves manual RF/FM.
`ssrf-physical-status` is an explicit diagnostic/safe-OFF preflight, not runtime
health. `ssrf-deploy-keysight` deploys files without starting devices.

Choose the intended GUI workflow:

- Plain RF: select carrier/level → MANUAL ARM → RF ON; no calibration gate.
- Optional FM: stage/select waveform → PREPARE → MANUAL ARM → RF ON → FM ON.
- Adaptive burn: calibrated fitter → GENERATE / LOAD → ARM → BURN ONCE.

RF OFF / either DISARM / ABORT request common verified safe cleanup, even idle.
STOP verifies the safe baseline before owner termination; lock/verification
failure retains owners and returns nonzero. ACF/direct-CA ownership remains a
physical acceptance item. The current canonical procedure is
`SSRF_OPERATOR_QUICK_START.md`; details are in `SSRF_FINAL_PHYSICAL_OPERATION.md`.
Historical no-RF commissioning records may show their original commands; the dedicated demo runtime has been removed.
