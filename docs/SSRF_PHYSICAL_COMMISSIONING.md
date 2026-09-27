# Genesis Phase-I ssRF Physical Commissioning

## Scope

This document records the physical-connectivity baseline established during Genesis Phase-I milestone 5C.1.

Milestone 5C.1 is a **read-only hardware commissioning milestone**. It proves that Genesis can communicate with the real ssRF hardware without enabling hardware execution. No physical burn is authorized by this milestone.

## 1. Architecture

The physical control path is:

```text
Mac operator workstation
    |
    | EPICS Channel Access
    v
Raspberry Pi 192.168.1.24
    |
    | Genesis Keysight IOC
    | PyVISA / USB
    v
Keysight DSO-X 3024T
    |
    | analog Gen Out BNC
    v
Rohde & Schwarz SMC100A MOD EXT
```

The Mac remains the operator machine. The Raspberry Pi is the hardware-side interface for the Keysight USB connection; it is not intended to replace the Mac GUI, fitter, logging, or analysis environment.

The fitter / waveform path is:

```text
NMR data
    |
Mac fitter
    |
Genesis waveform planner / bridge
    |
Genesis EPICS staging + safety / interlock boundary
    |
Raspberry Pi Keysight IOC
    |
Keysight
```

The fitter, analysis, and later ML layers must not bypass the Genesis EPICS/interlock layer.

## 2. Physical endpoints proven in 5C.1

### SMC100A

```text
IP:       192.168.1.16
Port:     5025
Protocol: raw TCP / SCPI
Model:    Rohde & Schwarz SMC100A
```

Safe commissioning baseline:

```text
frequency = 33 MHz
level     = -120 dBm
RF        = OFF
FM        = OFF
mode      = CW
FM source = EXT
FM dev    = 10 kHz
```

### NMR Mini-Circuits switch

```text
IP:        192.168.1.8
Port:      80
Model:     RC-2SPDT-A18
Safe mask: 0
```

Physical hardware may return identity replies such as:

```text
MN=RC-2SPDT-A18
SN=02206270097
```

Genesis normalizes these to the bare model and serial values.

### ssRF Mini-Circuits switch

```text
IP:        192.168.1.11
Port:      80
Model:     RC-2SPDT-A18
Safe mask: 3
```

### Raspberry Pi

```text
Hostname: keysight
IP:       192.168.1.24

Genesis root:
/home/unhpoltarg/genesis_keysight
```

The Genesis directory must remain dedicated and minimal. Do not operate from Farooq's directories, environments, or services. Those may be inspected as reference material only when necessary.

### Keysight WaveGen

```text
Model:  DSO-X 3024T
Serial: MY57252012

PyVISA resource:
USB0::10893::5990::MY57252012::0::INSTR
```

Physical 5C.1 readback:

```text
function = ARB
output   = OFF
period   = 0.010 s
VHIGH    = +0.700 V
VLOW     = -0.700 V
```

The physical `*IDN?` reply is longer than the native EPICS short-string limit. The Genesis `IDN` PV therefore uses a long-string record and preserves the full physical identity including serial and firmware.

## 3. Physical read-only profile

The physical Keysight commissioning profile is:

```text
configs/commissioning/
    ssrf_physical_readonly/
        keysight_wavegen/
            settings.yaml
```

Required safety settings:

```yaml
write_enabled: false
initialize_on_connect: false
```

Direct command PVs retain the `GENESIS_SSRF_CMD` Access Security group. Startup therefore queries the hardware without changing its state.

## 4. Raspberry Pi runtime

The deployed minimal runtime is:

```text
~/genesis_keysight/runtime
```

The dedicated Python environment is:

```text
~/genesis_keysight/.venv
```

Only files required for the Keysight EPICS runtime belong under the Genesis Keysight tree. Do not substitute another user's virtual environment.

The physical Keysight IOC is intentionally not started automatically by the 5C.1 repository helper.

## 5. Clock and timestamp convention

Genesis event/session data continue to use their established UTC ISO-8601 timestamps.

For filenames, run directories, IOC logs, and commissioning evidence, use:

```text
YYYY-MM-DD_HH-MM-SS_UTC
```

Example:

```text
2026-09-25_21-02-21_UTC
```

Before physical IOC or logging work, the Mac and Pi system clocks should agree within 10 seconds.

Preferred hierarchy:

```text
1. Normal NTP synchronization.
2. If NTP is unavailable on the lab network:
       ~/genesis_keysight/scripts/sync_clock_https.sh
3. Independently compare Mac and Pi epochs.
4. Require absolute difference <= 10 s.
```

The HTTPS helper is a commissioning fallback, not a replacement for a proper laboratory NTP service.

## 6. IOC launch rule

For a detached GNU Screen IOC on the Raspberry Pi use:

```text
screen -dmS <session> ...
```

The lowercase `-d` form detaches and returns to the caller.

Do not use the uppercase `-D` form for this workflow:

```text
screen -DmS
```

During commissioning, `-DmS` left the SSH launch wrapper waiting even though the IOC itself was healthy.

## 7. Read-only operator status

From the repository on the Mac:

```bash
./scripts/genesisctl.sh physical-status
```

or, when the local helper is installed:

```bash
genesis physical-status
```

This performs read-only checks only. It does not start a physical IOC and it does not enable hardware execution.

## 8. Safe baseline and shutdown verification

After a read-only commissioning test verify:

```text
SMC RF        OFF
SMC FM        OFF
SMC mode      CW
NMR mask      0
ssRF mask     3
WaveGen       OFF
```

Also verify:

```text
no unintended Keysight IOC remains
port 8778 is free unless fitter transport is intentionally running
```

Use PID/session-specific cleanup. Do not use broad process killing in the laboratory environment.

## 9. External-FM topology

The physical adaptive FM path requires the analog connection:

```text
Keysight Gen Out
    |
    | BNC
    v
SMC100A MOD EXT
```

EPICS/network communication does not replace this analog cable.

## 10. Gates remaining after 5C.1

5C.1 proves connectivity only. It does **not** authorize a burn.

Before fitter-driven physical execution:

```text
- fitter must run on the Mac
- fitter transport / port 8778 must be verified
- fitter-to-waveform bridge must be verified
- waveform staging must be verified
- explicit polarization values must replace silent physical defaults
- MHz <-> voltage <-> SMC external-FM scaling must be measured
- scaling_verified_for_hardware must remain false until measurement
- physical command/interlock tests must pass
```

The Keysight waveform interface intentionally separates:

```text
WAVEFORM_CMD
```

which stages waveform data in IOC memory, from:

```text
LOAD_CMD
```

which is the explicit instrument-commit boundary.

No fitter or ML component may bypass that boundary.

## 11. 5C.1 evidence

The following were physically demonstrated:

```text
SMC100A TCP/SCPI readback
NMR Mini-Circuits switch readback
ssRF Mini-Circuits switch readback
Raspberry Pi <-> Keysight USB communication
Genesis Keysight DeviceConnection
Genesis physical read-only Keysight IOC
Mac <-> Raspberry Pi EPICS communication
full Keysight IDN and serial over EPICS
WaveGen output remained OFF
WRITE_ENABLED remained 0
no state-changing physical command was required
```

This is the frozen starting point for subsequent Genesis physical ssRF commissioning.

<!-- BEGIN VERIFIED SSRF PHYSICAL WIRING 2026-09-27 -->

## Verified physical NMR / ssRF wiring — 2026-09-27

This section records direct visual inspection and cable tracing performed
during Genesis Phase-I physical commissioning.

### Lower RC-2SPDT-A18 — NMR switch

Section A:

- A COM = NMR Signal from target-stick / cryostat side
- A1 = To VME Crate, Deuteron Signal
- A2 = 50 ohm termination

Section B:

- B1 = R&S NMR signal-generator path
- B COM = Reference path toward target stick
- B2 = 50 ohm termination

Observed NMR reference path:

    VME Deuteron Ref
        -> Mini-Circuits ZFSC-6-110-S splitter port 1

    splitter port 2
        -> NMR switch B1

    NMR switch B COM
        -> blue reference cable toward target stick

Observed NMR signal-return path:

    target-stick / cryostat Signal
        -> NMR switch A COM

    NMR switch A1
        -> VME Deuteron Signal

Physical commissioning confirmed:

    NMR mode:
        NMR mask = 0
        both switch sections on Port 1

    Protected / ssRF mode:
        NMR mask = 3
        both switch sections on Port 2

Relay motion was directly heard and observed during commissioning.

### Upper RC-2SPDT-A18 — ssRF switch

Observed cable tracing in the DECOMMISSIONED 2026-09-27 state:

    SMC100A LF output
        -> BNC T
        -> orange coax
        -> ssRF switch A COM

    SMC100A RF 50 ohm output
        -> physically UNCONNECTED

    ssRF switch A1
        -> blue cable
        -> Mini-Circuits LZY-22+ INPUT

    LZY-22+ OUTPUT
        -> orange coax
        -> MFJ-945E ANTENNA connector

    MFJ-945E TRANSMITTER connector
        -> orange coax
        -> ssRF switch B1

    ssRF switch B COM
        -> orange coax toward cryostat / target-stick ssRF port

The target-stick end of this ssRF cable was NOT connected during the
2026-09-27 commissioning session.

A2 and B2 were physically capped / plugged. Their exact electrical
termination type has not yet been independently verified.

Physical commissioning confirmed:

    NMR mode:
        ssRF mask = 3
        both switch sections on Port 2

    ssRF mode:
        ssRF mask = 0
        both switch sections on Port 1

### LZY-22+ amplifier

Observed hardware:

    Mini-Circuits LZY-22+
    frequency range: 0.1-200 MHz
    DC input: +24 V
    front-panel warning:
        DO NOT EXCEED 20 dBm PEAK ENVELOPE POWER at INPUT

The amplifier is powered by a Siglent SPD3303X-E supply.

During all Genesis commissioning described here:

    Siglent output = OFF
    LZY-22+ = unpowered

The amplifier must NOT be powered until:

1. SPD3303X-E channel configuration and current capability are verified.
2. A known safe downstream RF load is present.
3. The target / dummy-load configuration is confirmed.

### MFJ-945E tuner

Observed rear-panel cabling during inspection:

    LZY-22+ output -> MFJ ANTENNA
    MFJ TRANSMITTER -> ssRF switch B1

This is opposite the conventional MFJ transmitter/antenna orientation and
opposite the earlier TN-00a drawing.

DO NOT change this wiring solely from the documentation.

Before higher-power operation, confirm with the ssRF hardware expert whether
this orientation is intentional.

The MFJ 12 V input powers the meter lamp. Lamp OFF does not constitute an
RF disconnect.

### Open physical items before amplified RF

The following remain explicitly OPEN:

1. RESOLVED by direct close-up inspection:
   the orange cable presently feeding ssRF A COM is connected to the
   SMC100A LF output through a BNC T. The SMC100A RF 50 ohm output is
   physically unconnected.

   This is a decommissioned / non-burn configuration and differs from
   TN-00a, which specifies SMC100A RF OUT -> upper switch A-COM.

2. Confirm whether the current MFJ-945E reverse orientation is intentional.

3. Confirm SPD3303X-E configuration and wiring used to supply the LZY-22+.

4. Confirm the electrical termination of ssRF switch A2 and B2.

5. Establish a known safe load / target connection before powering LZY-22+.

### Genesis Phase-I scope

The Agilent 33220A is NOT required for the Genesis Phase-I ssRF control path.

Phase-I control path:

    NMR measurement
        -> ssRF fitter / waveform planner
        -> Keysight DSOX3024T WaveGen
        -> SMC100A external FM
        -> NMR / ssRF physical switches
        -> interlock
        -> SMC RF control
        -> ssRF chain
        -> safe restoration to NMR

The Agilent trigger/burst system is considered legacy / out of scope for
Phase I unless the project scope changes later.

### Commissioning status as of 2026-09-27

PASS:
- Physical Keysight communication
- 8192-point ARB upload through EPICS
- Keysight +/-0.7 V waveform verification
- SMC100A physical EPICS communication
- SMC external-FM source/deviation control
- NMR physical switch control
- ssRF physical switch control
- SAFE_IDLE / NMR_LIVE / SSRF_READY interlock transitions
- RF_ENABLE_PERMIT behavior
- SMC RF-enable command/readback at 33 MHz, -120 dBm for approximately 1 second
- BURNING interlock state
- NOTE: SMC RF 50 ohm output was physically unconnected during this test,
  so this validated control/interlock behavior but did NOT inject RF into
  the downstream ssRF chain
- RF shutdown
- safe ssRF disconnect and NMR restoration

Not yet commissioned:
- powered LZY-22+ operation
- amplified RF level
- MFJ/tuner match
- direct RF power measurement after amplification
- full physical burn sequencer
- actual ssRF burn

<!-- BEGIN 2026-09-27 SOURCE CHARACTERIZATION -->

### 2026-09-27 source characterization and physical closeout

#### As-found decommissioned cabling

Close-up inspection established that the laboratory hardware was not
left in its normal burn configuration.

As found:

    Keysight WaveGen -> SMC100A MOD EXT

    SMC100A LF
        -> BNC T
            -> blue-headed 50-ohm coax -> Keysight CH2
            -> orange cable -> ssRF switch A-COM

The "50-ohm" description above refers to the coaxial cable. During the
direct SMC RF characterization, Keysight CH2 was explicitly configured
for 50-ohm input termination. That oscilloscope setting persisted after
the cable was returned to the LF T.

The pre-commissioning CH2 input-termination setting was not recorded, so
this document does not claim that 50-ohm CH2 termination was part of the
as-found configuration. The physical cabling itself was returned to the
as-found decommissioned configuration.

    SMC100A RF 50-ohm
        -> physically unused

    ssRF switch A1
        -> blue cable
        -> LZY-22+ INPUT

    LZY-22+ OUTPUT
        -> MFJ-945E ANTENNA

    MFJ-945E TRANSMITTER
        -> ssRF switch B1

    ssRF switch B-COM
        -> cable toward target-stick ssRF connection

The target stick was decommissioned and the target-side ssRF connection
was not connected.

This decommissioned LF-T arrangement is not the documented normal
operational ssRF topology. The UNH shift-worker procedure describes the
SMC100A RF output as being split, with one branch monitored by the
Keysight and the other branch feeding the RF line. The correct operational
topology must therefore be re-established and verified before future
amplified-RF or target operation.

The Siglent SPD3303X-E output and LZY-22+ amplifier remained OFF for all
Genesis Phase-I commissioning described here.

After measurement work, the cabling was returned to this as-found
decommissioned state.

#### Direct SMC100A RF-output characterization

For measurement only, the SMC100A RF 50-ohm output was connected directly
to Keysight DSOX3024T CH2 configured for 50-ohm termination.

Measured at 33 MHz:

| SMC level | Measured Vpp | Ideal 50-ohm Vpp | Difference |
| --- | ---: | ---: | ---: |
| -40 dBm | 6.432 mV | 6.325 mV | +0.15 dB |
| -30 dBm | 20.101 mV | 20.000 mV | +0.04 dB |
| -20 dBm | 62.714 mV | 63.246 mV | -0.07 dB |

The SMC100A RF source therefore agreed with the expected 50-ohm
power-to-voltage conversion to within approximately 0.15 dB across the
tested range.

This directly commissions the low-power SMC RF output.

#### Direct external-FM characterization

External FM was measured with:

    carrier                  = 33 MHz
    RF level                 = -20 dBm
    SMC FM source            = EXT
    programmed FM deviation  = 10 kHz

Measured static modulation points:

| Programmed WaveGen level | Measured MOD EXT voltage | Measured RF frequency |
| --- | ---: | ---: |
| 0.0 V | -0.018474 V | 32.999980 MHz |
| +0.7 V | +0.673891 V | 33.006990 MHz |
| -0.7 V | -0.714352 V | 32.992960 MHz |

Derived response:

    external-FM sensitivity = 10.1063 kHz/V
    positive excursion      = +7.010 kHz
    negative excursion      = -7.020 kHz

This physically verifies the Keysight-to-SMC external-FM transfer and
confirms the approximately 10 kHz/V expected response.

#### WaveGen post-calibration state

The temporary DC-level FM calibration caused the DSOX3024T ARB memory to
return to its instrument-default two-point representation.

This did NOT enable the WaveGen output.

The safe scalar Genesis state was restored:

    function = ARB
    period   = 10 ms
    amplitude = 1.4 Vpp
    offset    = 0 V
    high      = +0.7 V
    low       = -0.7 V
    output    = OFF

The canonical 8192-point Genesis waveform will be reloaded through the
validated Genesis upload path immediately before the next physical FM
operation. Its absence while the WaveGen output is OFF is not treated as
an active RF condition.

#### Upper ssRF switch RF-through measurement

A direct RF transmission measurement through:

    SMC RF
        -> source cable
        -> ssRF switch A-COM
        -> ssRF switch A1
        -> LZY-input cable

was deferred because the required known 50-ohm adapter/cable was not
available in the laboratory during this commissioning session.

The physical Mini-Circuits switch actuation, authoritative SWPORT
readback, routing states, interlock behavior and safe ordering have
already been commissioned.

The RF-through measurement remains an OPEN hardware characterization item.
It should be completed with the proper 50-ohm adapter before amplified-RF
or real-target burn commissioning.

Do not improvise an RF adapter for this measurement.

#### Physical commissioning boundary reached

Physically commissioned:

- SMC100A communication and readback
- SMC RF command and shutdown
- SMC low-power RF output amplitude
- SMC external-FM source and deviation
- measured external-FM sensitivity
- Keysight WaveGen physical communication
- Keysight modulation-voltage calibration
- physical NMR switch control/readback
- physical ssRF switch control/readback
- NMR_LIVE / SAFE_IDLE / SSRF_READY / BURNING interlock transitions
- RF-enable permit behavior
- safe RF shutdown and NMR restoration

Deferred to future amplified-RF / target commissioning:

- direct RF-through measurement of the upper ssRF switch path
- LZY-22+ powered RF commissioning
- SPD3303X-E parallel/current validation under amplifier load
- MFJ-945E orientation / tuning validation
- amplified RF power measurement
- target-connected ssRF burn
- actual polarization-manipulation measurement

No actual ssRF burn was performed during this session.

<!-- END 2026-09-27 SOURCE CHARACTERIZATION -->

<!-- END VERIFIED SSRF PHYSICAL WIRING 2026-09-27 -->


<!-- BEGIN 2026-09-27 FULL NO-RF E2E -->

### 2026-09-27 full physical no-RF workflow — PASS

Verified end-to-end:

```text
offline NMR
-> equilibrium fitter
-> Update Constants
-> ssRF fit
-> adaptive WaveformPlan
-> 8192-point physical Keysight ARB load
-> one-shot ARM
-> NMR 0 -> 3
-> ssRF 3 -> 0
-> no-RF dwell
-> ssRF 0 -> 3
-> NMR 3 -> 0
-> COMPLETE / NMR_LIVE
```

Final state was Keysight output OFF, SMC RF/FM OFF, NMR mask 0,
ssRF mask 3, and the 8192-point adaptive waveform retained in ARB memory.

Both physical switch boxes visibly actuated and returned. No amplified-RF
or target burn was performed.

<!-- END 2026-09-27 FULL NO-RF E2E -->
