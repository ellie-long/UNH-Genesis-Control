# UNH Genesis Phase-I — macOS Quick Start

This document describes the validated macOS startup procedure for the UNH Genesis Phase-I EPICS prototype.

## Current Integrated Subsystem

The currently validated live subsystem is:

- Subsystem: Microwave
- Device: Siglent SPD1305X
- Device communication: TCP/SCPI
- Device address: `192.168.1.13:5025`
- EPICS IOC: `mw_spd1305x`
- PV prefix: `UNH:GEN:MW:PS`
- IOC Manager prefix: `UNH:GEN:MAN`
- Main GUI: `gui/genesis_microwave.bob`
- Device GUI: `gui/genesis_mw_spd1305x.bob`

The Mac must be connected to the same lab network as the SPD1305X.

## Normal Startup

From the repository root:

```bash
./scripts/start_genesis_mac.sh
```

The script:

1. Uses the `genesis-p1` Conda environment.
2. Configures Channel Access for the Python EPICS tools.
3. Starts the IOC Manager if needed.
4. Starts the Microwave SPD1305X IOC.
5. Starts the archiver.
6. Enables archiving.
7. Checks the live SPD1305X connection and important PVs.
8. Launches Phoebus with the Genesis PVAccess configuration.
9. Opens the Microwave subsystem GUI.

Normal startup is intentionally read-only:

```yaml
write_enabled: false
initialize_on_connect: false
```

This prevents routine software startup from modifying the physical power-supply state.

## Phoebus on macOS

Phoebus uses EPICS PVAccess for the Genesis GUI.

Configuration:

```text
configs/phoebus/genesis_mac_settings.ini
```

The local PVA server ports are:

```text
IOC Manager : 5075
SPD1305X    : 5076
Archiver    : 5077
```

Genesis Phoebus `.bob` files therefore use PV names such as:

```text
pva://UNH:GEN:MW:PS:VSET_RBV
```

The Python tools and archiver use Channel Access on:

```text
IOC Manager : 5064
SPD1305X    : 5066
Archiver    : 5067
```

The PVA configuration was adopted because it was validated reliably with Phoebus on the macOS development system.

## Expected Normal State

A normal live Microwave system should show approximately:

```text
UNH:GEN:MAN:mw_spd1305x_control = 1
UNH:GEN:MW:PS:COMM              = 1
UNH:GEN:MW:PS:VSET_RBV          = 5.0
UNH:GEN:MW:PS:ISET_RBV          = 0.05
UNH:GEN:MW:PS:OVP_RBV           = 5.1
UNH:GEN:MW:PS:OCP_RBV           = 0.11
UNH:GEN:MW:PS:OUTPUT_RBV        = 0
UNH:GEN:MW:PS:ERROR             = blank
UNH:GEN:Archive_Status          = 1
UNH:GEN:Archive_PV_Count        = 12
```

The voltage, current, OVP, and OCP values can differ if the physical hardware settings have intentionally been changed.

## Temporary Live-Write Mode

Hardware writes must only be enabled deliberately.

First verify that the physical output is OFF:

```bash
python - <<'PY'
import asyncio
from aioca import caget

async def main():
    print(
        await caget(
            "UNH:GEN:MW:PS:OUTPUT_RBV",
            timeout=3
        )
    )

asyncio.run(main())
PY
```

The expected result is:

```text
0
```

Stop the Microwave IOC:

```bash
python - <<'PY'
from tools.ioc_cli import load_settings, stop_ioc

settings = load_settings()
print(stop_ioc(settings, "mw_spd1305x"))
PY
```

Edit:

```text
configs/microwave/spd1305x/settings.yaml
```

Change only:

```yaml
write_enabled: false
```

to:

```yaml
write_enabled: true
```

Keep:

```yaml
initialize_on_connect: false
```

Restart the Microwave IOC:

```bash
python - <<'PY'
from tools.ioc_cli import load_settings, start_ioc

settings = load_settings()
print(start_ioc(settings, "mw_spd1305x"))
PY
```

Wait several seconds and confirm:

```text
COMM       = 1
OUTPUT_RBV = 0
ERROR      = blank
```

The Phoebus SPD1305X device-control screen can then issue hardware commands.

## Return to Safe Read-Only Mode

After live hardware-control work, stop the Microwave IOC again.

Restore:

```yaml
write_enabled: false
initialize_on_connect: false
```

Restart the IOC.

Verify:

```text
COMM       = 1
OUTPUT_RBV = 0
ERROR      = blank
```

The Git repository must retain the safe default:

```yaml
write_enabled: false
initialize_on_connect: false
```

Do not commit the repository with `write_enabled: true`.

## Useful Diagnostics

Show running Genesis processes:

```bash
screen -ls
```

During normal operation there should be sessions for:

```text
ioc-manager
mw_spd1305x
archiver
```

Check the Microwave system through EPICS:

```bash
python - <<'PY'
import asyncio
from aioca import caget

P = "UNH:GEN:MW:PS"

async def main():
    for pv in [
        f"{P}:COMM",
        f"{P}:VSET_RBV",
        f"{P}:ISET_RBV",
        f"{P}:OUTPUT_RBV",
        f"{P}:ERROR",
        "UNH:GEN:Archive_Status",
        "UNH:GEN:Archive_PV_Count",
    ]:
        print(pv, "=", await caget(pv, timeout=3))

asyncio.run(main())
PY
```

If Terminal EPICS access works but the Phoebus GUI is blank, open Phoebus Probe and test:

```text
pva://UNH:GEN:MW:PS:VSET_RBV
```

A connected system should return the current voltage setpoint.

## Validated Phase-I Behavior

The following behavior has been validated with the physical SPD1305X:

- TCP/SCPI hardware communication
- Read-only IOC startup
- Device disconnect and reconnect
- IOC Manager start and stop control
- Phoebus PVA monitoring
- Phoebus setpoint control during temporary write mode
- Voltage command and readback
- Current command and readback
- OVP and OCP readback
- Output-state readback
- EPICS archiving
- Twelve selected Microwave archive PVs
- IOC restart without modifying the physical hardware state
- Restoration to read-only operation

The physical output does not need to be energized merely to validate the software control architecture.


## ssRF physical operator workflow

Use `./scripts/start_ssrf_system.sh` for physical stack startup or non-invasive
GUI reuse during existing RF operation. Use `./scripts/genesisctl.sh ssrf-stop`
for verified safe shutdown. See `SSRF_OPERATOR_QUICK_START.md` for plain manual
RF, optional manual FM and adaptive one-shot burns. Startup remains source-OFF
and disarmed; fitter calibration is not plain-RF authorization. The generic
Microwave startup described above is a separate workflow.
