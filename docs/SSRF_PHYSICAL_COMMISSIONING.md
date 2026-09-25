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
