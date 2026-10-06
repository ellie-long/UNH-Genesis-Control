# Genesis ssRF physical operator quick start

## Purpose and scope

This document is the operator procedure for the Genesis ssRF physical
source-control subsystem.

It covers three distinct workflows:

1. **Adaptive ssRF burn**
   - This is the normal sequenced burn workflow.
   - The fitter provides the NMR-derived burn information.
   - `GENERATE / LOAD` prepares the waveform and RF-source parameters.
   - `ARM` authorizes one burn.
   - `BURN ONCE` automatically performs RF routing, WaveGen, FM, RF, the
     programmed SMC sweep, shutdown, and NMR restoration.

2. **Manual RF / manual FM**
   - This is a commissioning and troubleshooting workflow.
   - It independently verifies RF-source operation, authorization, interlocks,
     switching, and safe shutdown.
   - It is not required during a normal adaptive burn.
   - Do not manually enable RF or FM before `BURN ONCE`.

3. **Source-control physical acceptance**
   - This is the repeatable commissioning test for the complete Genesis ssRF
     control chain.
   - It is performed with the downstream/LZY amplifier unpowered.
   - It validates software, EPICS, authorization, switches, WaveGen, SMC100A,
     interlocks, sequencing, GUI operation, and automatic cleanup.
   - It does not constitute powered downstream-RF or target commissioning.

The current validated physical WaveGen operating limits are:

```text
VHIGH = +0.500 V
VLOW  = -0.500 V
```

The adaptive waveform planner retains its legacy internal ±0.7 numerical
reference. That internal mathematical reference is not the physical WaveGen
voltage setting. The physical preparation boundary enforces ±0.5 V.

The validated physical source-control baseline starts safely with SMC RF OFF,
SMC FM OFF, WaveGen OFF, sweep disarmed, execution disarmed, NMR connected,
and the ssRF burn path disconnected. Restart/fault recovery never restores RF
or an arm.

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

## 1. Plain manual RF — commissioning / troubleshooting only

### Purpose

Manual RF exists to independently test the physical RF-control path.

Use it to verify:

- operator authorization;
- interlock behavior;
- NMR/ssRF switch routing;
- SMC RF ON/OFF;
- RF readback;
- safe cleanup.

It is not part of the normal adaptive burn sequence.

For an adaptive burn, do not use `MANUAL ARM` or `RF ON`; `BURN ONCE`
automatically performs the required routing and RF/FM sequence.

### Operation

Set **Manual carrier MHz** and **Manual RF level dBm**, then deliberately press:

```text
MANUAL ARM → RF ON
```

Each RF ON requires an explicit manual arm. RF ON consumes it, verifies hard
safety inputs and routing, disconnects NMR, connects ssRF, checks RF-enable
permission, and enables/verifies SMC RF.

For plain manual RF:

```text
SMC RF       = ON
SMC FM       = OFF
WaveGen      = OFF
SMC sweep    = DISARMED
```

Use **RF OFF**, **DISARM / SAFE OFF**, or **ABORT** to request the common safe
cleanup path.

Fitter result, freshness, calibration, `constants_good`, `P_fit`, adaptive
recipe, and adaptive waveform preparation are not plain-RF authorization.

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

### Purpose

Use this workflow for an actual sequenced ssRF burn.

The fitter **ssRF** button selects the NMR fitting mode. It does not operate
the physical ssRF Mini-Circuits switch.

The physical switch routing, WaveGen output, FM, RF, SMC sweep, cleanup, and
NMR restoration are controlled automatically by the sequencer when
`BURN ONCE` is executed.

### Operator sequence

```text
Acquire equilibrium NMR data
→ CALIBRATE
→ select fitter ssRF mode
→ acquire a fresh calibrated ssRF fit
→ GENERATE / LOAD
→ verify PREPARED / READY_TO_ARM
→ ARM
→ BURN ONCE
→ verify COMPLETE and safe baseline
```

### Calibration

Use representative equilibrium NMR data and press:

```text
CALIBRATE
```

Successful calibration establishes the fitter constants.

Recalibration is required when the relevant NMR tuning, material, field, or
calibration conditions change.

### Fresh ssRF fit

After calibration, press the fitter:

```text
ssRF
```

button.

This changes the fitter analysis mode only.

Acquire another NMR polarization sweep. A valid ssRF result should report a
`hole-burnt` fit and the Burn Setup **Fit valid** indicator should become green.

Burn Setup accepts only a fresh fitter result. The current freshness limit is
60 seconds.

If **Fit valid** turns red only because the fit has become older than 60 seconds,
acquire another fresh ssRF/polarization sweep. Recalibration is not required
solely because the fit aged out.

### GENERATE / LOAD

Press:

```text
GENERATE / LOAD
```

once.

This generates the adaptive waveform and performs source preparation.

It must not energize the RF chain.

Verify:

```text
Waveform points       = 8192
VHIGH                 = +0.500 V
VLOW                  = -0.500 V
SMC RF                = OFF
SMC FM                = OFF
WaveGen output        = OFF
SMC sweep             = DISARMED
NMR switch mask       = 0
ssRF switch mask      = 3
PREPARED              = 1
recipe state          = READY_TO_ARM
```

`GENERATE / LOAD` prepares the burn. It does not perform the burn.

### ARM

Press:

```text
ARM
```

once.

This is the adaptive-burn arm. It is separate from `MANUAL ARM`.

The arm is one-shot and is consumed by one `BURN ONCE`.

### BURN ONCE

Press:

```text
BURN ONCE
```

once.

The sequencer automatically performs:

```text
disconnect NMR
→ verify receiver protection
→ connect ssRF path
→ WaveGen ON
→ FM ON
→ verify RF-enable permit
→ RF ON
→ arm SMC sweep
→ trigger one sweep
→ wait for the programmed burn interval
→ RF OFF
→ FM OFF
→ WaveGen OFF
→ disconnect ssRF
→ restore NMR
→ COMPLETE
```

Do not manually enable RF, FM, WaveGen output, or switch routing before an
adaptive burn.

### Successful final state

After `BURN ONCE`, verify:

```text
EXECUTION_STATE       = COMPLETE
EXECUTION_ARMED       = 0
RF                     = OFF
FM                     = OFF
SMC sweep              = DISARMED
WaveGen output         = OFF
NMR switch mask        = 0
ssRF switch mask       = 3
Interlock state        = NMR_LIVE
```

## 4. Repeatable source-control physical acceptance

### Purpose

Use this procedure when validating the Genesis ssRF source-control chain after
a software change, deployment change, computer migration, authorization change,
or hardware-control change.

This procedure is not a powered target burn.

The downstream/LZY amplifier must remain unpowered.

### Required starting conditions

Before testing:

- use the canonical Genesis checkout;
- activate the `genesis-p1` environment;
- confirm there is only one owner for each physical instrument;
- confirm the Keysight Raspberry Pi is reachable;
- confirm the replay NMR dataset is available if live NMR data are not being
  used;
- confirm the downstream/LZY amplifier is unpowered.

The replay dataset used for the October 6, 2026 acceptance was:

```text
test_data/2026-06-Cooldown/data_record_2026-06-17.csv
```

This dataset is intentionally not repository-tracked and must already be
available locally when performing replay-based acceptance.

### Step 1 — Read-only physical status

Run:

```bash
./scripts/genesisctl.sh ssrf-physical-status
```

Expected safe hardware baseline:

```text
SMC RF             = OFF
SMC FM             = OFF
SMC sweep          = DISARMED
WaveGen output     = OFF
WaveGen period     = 0.010 s
WaveGen VHIGH      = +0.500 V
WaveGen VLOW       = -0.500 V
NMR switch mask    = 0
ssRF switch mask   = 3
```

### Step 2 — Start the physical stack

Run:

```bash
./scripts/genesisctl.sh ssrf-start
```

Startup must remain de-energized and disarmed.

Verify that the SMC, WaveGen, NMR switch, ssRF switch, interlock, sequencer,
fitter, Burn Setup, and archiver are running.

### Step 3 — Open Phoebus

When operating the Ubuntu/WSL DAQ remotely from the Mac, use a separate SSH
session with trusted X11 forwarding:

```bash
ssh -Y utsav@192.168.1.22
cd ~/UNH-Genesis-Control/repo
conda activate genesis-p1
./scripts/start_ssrf_phoebus.sh
```

The backend/control terminal and GUI/X11 terminal are separate sessions.

### Step 4 — Populate the equilibrium fitter buffer

For replay-based acceptance:

```bash
python -m online_fitter.mock_labview_client \
  --path test_data/2026-06-Cooldown/data_record_2026-06-17.csv \
  --rate 20 \
  --limit 60
```

Press **CALIBRATE** once.

Verify that the fitter reports calibrated constants.

### Step 5 — Produce a fresh ssRF fit

Press the fitter **ssRF** button.

This selects the fitter analysis mode only.

Then run:

```bash
python -m online_fitter.mock_labview_client \
  --path test_data/2026-06-Cooldown/data_record_2026-06-17.csv \
  --rate 20 \
  --limit 12 \
  --polarization-only
```

Verify:

```text
fit type  = hole-burnt
Fit valid = green
```

If the fit expires after 60 seconds, repeat only this fresh ssRF replay unless
the actual calibration conditions have changed.

### Step 6 — Prepare the adaptive burn

Press:

```text
GENERATE / LOAD
```

Verify:

```text
points                 = 8192
VLOW                   = -0.500 V
VHIGH                  = +0.500 V
SMC RF                 = OFF
SMC FM                 = OFF
WaveGen output         = OFF
NMR switch mask        = 0
ssRF switch mask       = 3
state                  = READY_TO_ARM
```

### Step 7 — Confirm acceptance-test scope

Before executing the burn sequence, physically confirm:

```text
downstream/LZY amplifier = UNPOWERED
```

Do not continue with this source-control acceptance procedure if the downstream
amplifier is powered.

### Step 8 — Execute one sequenced burn

Press **ARM** once.

Then press **BURN ONCE** once.

Do not operate other RF/FM/switch controls while the sequence is running.

### Step 9 — Verify automatic cleanup

A successful source-control acceptance ends with:

```text
Burn state             = COMPLETE
Armed                  = 0
RF                     = OFF
FM                     = OFF
SMC sweep              = DISARMED
WaveGen output         = OFF
NMR switch mask        = 0
ssRF switch mask       = 3
Interlock state        = NMR_LIVE
```

### Step 10 — Perform verified shutdown

Run:

```bash
./scripts/genesisctl.sh ssrf-stop
```

The shutdown must establish and directly verify the safe physical baseline
before terminating instrument owners.

The final direct readback must again show:

```text
RF                     = OFF
FM                     = OFF
WaveGen output         = OFF
WaveGen VHIGH          = +0.500 V
WaveGen VLOW           = -0.500 V
NMR switch mask        = 0
ssRF switch mask       = 3
```

A passing shutdown completes the source-control physical acceptance.

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

## Remaining commissioning beyond source-control acceptance

The guarded manual RF path and the adaptive one-shot source-control path were
physically accepted on October 6, 2026.

Still outside the scope of that source-control acceptance:

- powered downstream/LZY amplifier commissioning;
- target-connected RF commissioning;
- experiment-specific RF-power limits and tuning;
- additional fault-injection/response-time characterization where required.

Allowed direct-CA identities remain part of the protected command surface;
disabled expert GUI controls are not themselves a security boundary.

See `SSRF_FINAL_PHYSICAL_OPERATION.md` for the validated sequence,
authorization model, and current physical operating baseline.
