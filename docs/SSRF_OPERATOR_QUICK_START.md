# Genesis ssRF physical operator quick start

The validated physical source-control baseline starts safely with SMC RF OFF,
SMC FM OFF, WaveGen OFF, sweep disarmed and execution disarmed. The guarded
manual-RF extension is software validated and requires physical acceptance
before lab use. Restart/fault recovery never restores RF or an arm.

Use this checkout's repository root and the `genesis-p1` environment. Do not
start a second instrument owner or use a different checkout's devices library.

## Start or reopen the GUI

```bash
./scripts/start_ssrf_system.sh
```

With no stack, this uses the existing physical startup/preflight then opens the
GUI. With a complete live local stack, it checks only processes/sessions and
opens/reuses the GUI without requiring RF OFF or probing IOC-owned instruments.
Partial/ambiguous ownership is refused. The remote Keysight owner must still be
healthy; the reuse decision is local process/session liveness, not remote health.

Lower-level commands:

```bash
./scripts/genesisctl.sh ssrf-start       # hardware/control stack, initially safe
./scripts/start_ssrf_phoebus.sh          # GUI only
./scripts/genesisctl.sh ssrf-stop        # verified safe shutdown
```

`ssrf-physical-status` remains an explicit direct diagnostic/safe-OFF preflight,
not a runtime-health check during RF operation. `ssrf-deploy-keysight` deploys
runtime files only. A historical no-RF commissioning record is preserved in
`SSRF_PHYSICAL_NO_RF_DEMO.md`; its dedicated runtime has been removed and it is
not a current operator workflow.

## 1. Plain manual RF

Set **Manual carrier MHz** and **Manual RF level dBm**, then deliberately press:

```text
MANUAL ARM → RF ON
```

Each RF ON requires explicit manual arm. RF ON consumes it, verifies hard safety
inputs and routing, disconnects NMR, connects ssRF, checks RF-enable permission,
and enables/verifies SMC RF. FM/WaveGen stay OFF and the sweep stays disarmed.

Fitter result, freshness, calibration, constants_good, P_fit, adaptive recipe,
and adaptive waveform preparation are **not plain-RF authorization**. WaveGen
communication/output readback is required only to verify safe source state.
Observe manual state/reason/fault, hardware readbacks and interlock permits.

## 2. Optional manual FM

While idle, explicitly stage an operator-selected finite normalized 8192-point
waveform through `SEQ:WAVEFORM_STAGE_REQ`. Select the sequencer carrier/level,
FM deviation/period and WaveGen high/low voltages before preparation. The current
preparation uses the validated source configuration/load path, including its
burn-parameter validation; it does not enable source outputs or move switches.

```text
PREPARE SELECTED FM WAVEFORM → MANUAL ARM → RF ON → FM ON
```

FM requires verified external-FM settings, WaveGen communications/write
permission, voltage/period readbacks and prepared waveform identity/points.
It does not require fitter calibration and never triggers an adaptive burn or
sweep. **FM OFF** turns FM and WaveGen OFF while leaving plain RF ON.

## 3. Adaptive fitter-driven one-shot burn

```text
NMR / CALIBRATE → Burn Setup → GENERATE / LOAD → ARM → BURN ONCE
```

This separate analysis workflow requires a valid current calibrated fitter
result to generate its adaptive waveform. GENERATE / LOAD configures/loads
sources with outputs OFF. The burn arm is distinct from MANUAL ARM and is
consumed by one burn. Each subsequent burn requires new preparation and arm.

## Safe OFF, abort and shutdown

**RF OFF**, either **DISARM**, and **ABORT** request common safe de-energization
from idle, manual RF/FM, preparation or adaptive execution. Each clears arms
and independently attempts sweep disarm, SMC RF OFF, SMC FM OFF and WaveGen OFF.
Actual EPICS status/severity and source values must verify OFF before routing
changes. Cleanup then disconnects ssRF and, with NMR-connect permission,
restores NMR. The safe baseline is:

```text
RF=0  FM=0  WaveGen=0  sweep=0  RF switch mask=3  NMR switch mask=0
```

CLEANUP_FAILED means the safe state was not verified. Do not force routing or
assume NMR is protected. Correct the fault and request safe OFF again; RF never
resumes automatically and requires a fresh explicit arm.

```bash
./scripts/genesisctl.sh ssrf-stop
```

STOP uses fresh sequencer safe-off acknowledgment and verifies the baseline
before terminating owners. Missing-sequencer recovery acquires exclusive
command ownership before device-IOC fallback. Ownership/verification failure
retains physical owners and returns nonzero. Concurrent lifecycle requests
fail safely rather than creating competing command streams.

## Physical acceptance still required

- Confirm hardware routing, source readbacks and safe-OFF recovery.
- Measure transition/fault/abort response timing, including failed reads.
- Verify CA identity/ACF and exclusive command policy. ACF is unchanged; allowed
  direct-CA clients can still bypass ordering. Disabled expert output/routing
  GUI controls are not a security boundary.
- Integrate the later validated subsystem-scoped Phoebus shutdown helper; this
  branch does not terminate Phoebus during ssRF STOP.

Powered downstream RF-chain/target commissioning remains a separate step.
See `SSRF_FINAL_PHYSICAL_OPERATION.md` for sequence and authorization details.
