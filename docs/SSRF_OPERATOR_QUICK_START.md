# Genesis ssRF Phase-I Operator Quick Start

## Scope

This guide is for the validated **physical no-RF ssRF workflow**. It demonstrates the complete Genesis control path using the real Keysight WaveGen and physical RF switches while keeping:

- SMC RF OFF
- SMC FM OFF
- Keysight WaveGen output OFF
- LZY amplifier unpowered
- target burn disabled

It is **not** the procedure for an amplified-RF or target-connected ssRF burn.

## Before starting

From the Genesis repository:

```bash
cd /Users/shrestha/project/unh/genesis/Development/genesis-phase1
conda activate genesis-p1
export PYTHONPATH="$PWD"
```

Confirm that the repository is clean and that the physical system is at its safe baseline:

```bash
git status
genesis ssrf-physical-status
```

Expected safe physical baseline:

```text
SMC frequency   = 33 MHz
SMC level       = -120 dBm
SMC RF          = OFF
SMC FM          = OFF
NMR switch      = mask 0
ssRF switch     = mask 3
WaveGen output  = OFF
```

## Start the physical no-RF workflow

```bash
genesis ssrf-demo-start
```

The startup script performs the authoritative physical preflight, starts the physical Keysight upload-only IOC and the local no-RF workflow IOC, and opens the Genesis ssRF main GUI through the shared single-instance Phoebus launcher.

The main screen should open as one landscape Phoebus tab. Device/fitter detail screens should open as additional tabs in the same Phoebus instance.

## Operator sequence

Use the main `Genesis ssRF Subsystem` GUI.

Press:

```text
START FITTER
→ REPLAY EQ
→ CALIBRATE
→ ARM
→ BLAST / BURN
```

`ssRF MODE` is optional for this workflow because BLAST/BURN switches the fitter to the ssRF mode automatically before generating the fresh waveform.

The BLAST/BURN transaction performs:

```text
fresh ssRF fit
→ adaptive WaveformPlan
→ 8192-point Keysight ARB load
→ verify Keysight output remains OFF
→ NMR switch disconnect
→ ssRF switch connect
→ no-RF commissioning dwell
→ ssRF switch disconnect
→ NMR switch restore
→ COMPLETE
```

Expected completion state:

```text
Execution mode   = PHYSICAL_NO_RF
Fitter           = running
Calibrated       = yes
Fit mode         = ssRF
Waveform state   = LOADED
ARB points       = 8192
WaveGen ready    = yes
WaveGen output   = OFF
SMC RF           = OFF
SMC FM           = OFF
NMR mask         = 0
ssRF mask        = 3
ARM              = consumed / OFF
Current step     = COMPLETE
```

## Main GUI during PHYSICAL_NO_RF

The right-side workflow panel contains the authoritative commissioning mirrors used by the validated physical no-RF workflow.

Some left-side hardware cards belong to the normal Genesis deployment IOC namespace. If those production IOCs are intentionally not running in the isolated `PHYSICAL_NO_RF` profile, their widgets can appear disconnected/magenta. Do not start competing hardware IOCs merely to make those cards populate.

For the physical no-RF demonstration, use the workflow panel for:

- execution mode;
- fitter state and calibration;
- fit mode;
- waveform state;
- ARB point count;
- WaveGen readiness/output;
- NMR/ssRF switch masks;
- SMC RF/FM state;
- ARM state;
- current workflow step and reason.

## Archive/session viewer

Launch the read-only viewer in another terminal:

```bash
genesis archive-viewer
```

Default URL:

```text
http://127.0.0.1:8501
```

Use the `ssRF Sessions` page to inspect the correlated fit, waveform, event timeline and session record.

For final Phase-I acceptance, verify that a fresh physical no-RF demonstration run is represented by the expected session/event data.

Stop the viewer with `Ctrl-C`.

## Safe shutdown

When finished:

```bash
genesis ssrf-demo-stop
```

Then verify:

```bash
genesis ssrf-physical-status
genesis processes
genesis screens
```

The final physical state must show:

```text
SMC RF            = OFF
SMC FM            = OFF
NMR mask          = 0
ssRF mask         = 3
WaveGen output    = OFF
```

There should be no physical no-RF workflow/fitter runtime, no physical Keysight upload IOC, and no residual Genesis screen sessions associated with this workflow.

## Abort / abnormal condition

If the workflow is active and an operator needs to stop the transaction, use the GUI `ABORT` control.

If RF OFF cannot be confirmed, do not reconnect NMR merely by assumption. Follow the physical commissioning safety procedure and establish the RF state first.

Do not use the generic force-stop command unless the normal safe shutdown procedure cannot be used and the operator understands that `force-stop` bypasses hardware-state verification.

## Deferred hardware work

The following are **not part of this Phase-I no-RF operator workflow**:

- direct RF-through measurement of the upper ssRF switch path;
- powered LZY-22+ commissioning;
- SPD3303X-E validation under amplifier load;
- MFJ-945E orientation/tuning validation;
- amplified RF power measurement;
- target-connected ssRF burn;
- actual polarization-manipulation measurement.

These remain a separate amplified-RF/target commissioning milestone.
