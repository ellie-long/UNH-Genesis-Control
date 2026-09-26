# Vaunix LMS shim and IOC

The ARM-only Vaunix library runs on the Raspberry Pi. The EPICS IOC runs on the
laptop. `run_from_laptop.py` makes that split one command:

```
laptop: run_from_laptop.py -> SSH deploy/start -> Pi: vaunix_shim.py + liblmshid.so
                         \-> local master_ioc.py -> EPICS PVs
```

## Run it

From `/Users/blimric06/softioc_toolkit` on the laptop:

```bash
source venv/bin/activate
python tools/vaunix_shim/run_from_laptop.py
```

This reads `settings.yaml`, copies the shim and ARM object to the Pi, links
`liblmshid.so` on the ARM machine, installs/enables the Pi user service, starts
the selected mode, verifies TCP, then starts the local EPICS IOC. SSH securely prompts in the terminal
if the Pi account uses a password. The first run needs the Pi packages `gcc`,
`python3`, and libusb. Later runs can skip deployment:

```bash
python tools/vaunix_shim/run_from_laptop.py --no-deploy
```

Use `--shim-only` to start and check the shim without starting EPICS.

After the first deployment, normal IOC startup also starts the service before
connecting (using the installed SSH key):

```bash
python master_ioc.py -i bridge12_mw
```

## Select test or hardware mode

The only switch is in `settings.yaml`:

```yaml
bridge12_mw:
  shim:
    test_mode: true
```

Restart the command after changing it.

- `true`: the Pi loads the real ARM `liblmshid.so` and calls
  `fnLMS_SetTestMode(True)`. This uses the vendor library's test device.
- `false`: the same code and library call `fnLMS_SetTestMode(False)` and use
  the physical USB device.

There is no fake Python library or Python simulation path.

## Ownership

- `settings.yaml`: mode, Pi login/path, TCP endpoint, IOC timing and PV fields.
- `run_from_laptop.py`: deployment, ARM linking, service installation, local IOC.
- `vaunix_shim.py`: ctypes signatures, device selection, API errors, 10 Hz /
  0.25 dBm conversion, and serialized access.
- `devices/instruments/vaunix_lms.py`: TCP and EPICS records; no ARM library.

## Wire protocol

Every request and response is one ASCII line. Units are Hz and dBm.

```
*IDN?       -> VAUNIX,<model>,<serial>,<library-version>,TEST|HARDWARE
LIMITS?     -> <minimum-Hz> <maximum-Hz> <minimum-dBm> <maximum-dBm>
READ?       -> <Hz> <dBm> <rf-0|1> <internal-reference-0|1> <lock-0|1>
FREQ <Hz>   -> OK <achieved-Hz>
POW <dBm>   -> OK <achieved-dBm>
RF <0|1>    -> OK <0|1>
REF <0|1>   -> OK <0|1>
TEST_MODE? / TEST_MODE <0|1>
SWEEP?      -> <start-Hz> <end-Hz> <step-Hz> <time-ms> <up> <repeat> <bidir> <trigger> <active>
SWEEP_START <Hz> | SWEEP_END <Hz> | SWEEP_STEP <Hz> | SWEEP_TIME <ms>
SWEEP_DIRECTION <0|1> | SWEEP_REPEAT <0|1> | SWEEP_BIDIRECTIONAL <0|1>
SWEEP_EXTERNAL_TRIGGER <0|1> | SWEEP_RUN <0|1>
SAVE        -> OK
```

Failures return `ERR <message>` without corrupting the connection.

The IOC exposes sweep start/end/step/time command and readback records, plus
direction, repeat, bidirectional, external-trigger, run and active records.
`MW_Connected` reports the live connection. `MW_TestMode` is writable: 0 closes
the test device and scans for USB hardware; 1 closes hardware and reopens the
configured vendor test device. A failed switch restores the previous mode. A
service restart returns to the default mode configured in YAML.

The vendor LMS-123 test device does not implement stepped-sweep readback, so
`MW_SweepStepI` is 0 in test mode and step writes return its vendor error.
Start/end/time and the sweep mode/run commands still use the real C library.

## Fixed frequency safety range

The Pi shim—not the GUI—is authoritative. Microwave output is restricted to
138–142 GHz. With the configured ×12 multiplier, the launcher converts that to
the LMS synthesizer range 11.5–11.833333333 GHz. Carrier and sweep endpoint
writes outside that range are rejected. RF and sweep start commands are also
rejected unless their frequency values are in range. There is no override.

## Troubleshooting

The Pi service is `vaunix-shim.service`. Inspect its log from the laptop with:

```bash
ssh unhpoltarg@mmWaveRobot.local \
  'journalctl --user -u vaunix-shim.service -n 100 --no-pager'
```

If linking fails, install libusb development files on the Pi. If hardware mode
finds no devices, check USB permissions and ensure no other process owns the
Lab Brick. If several units are found, set `shim.serial` in the YAML. Vendor
test mode reports LMS-103 as `100103` and LMS-123 as `200123`; the included
settings select LMS-123. Hardware mode must use the real unit's serial number.
If SSH says `Permission denied`, confirm `shim.ssh_user` and the Pi password.
For password-free future runs, install this laptop's SSH public key with
`ssh-copy-id unhpoltarg@mmWaveRobot.local`.

This code uses names exported by the supplied Linux ARM object:
`liblmshid.so`, `fnLMS_GetLibVersion`, and `fnLMS_GetPowerLevel`. The prior
shim required Windows-only names and could not bind to this library.
