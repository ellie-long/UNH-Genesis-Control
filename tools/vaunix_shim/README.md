# Vaunix LMS shim and IOC

## Current Genesis RF-control contract

See [deliberate RF operation](../../docs/MICROWAVE_RF_OPERATION.md) for the
current command/readback model and physical acceptance requirements. The
Genesis physical IOC acquires `CONTROL ACQUIRE`, renews `CONTROL HEARTBEAT`,
and monitors `CONTROL?`. Mutating commands require the exclusive connection
lease. Default `--lease-timeout` is 6 seconds; expiry or owner disconnect attempts
independent sweep stop and verified RF OFF. Diagnostic queries do not renew
ownership. Reacquisition starts OFF. This is ownership, not authentication or
a guarantee against vendor/USB/hardware failure.

The current IOC lives in `microwave_ioc/vaunix_lms/toolkit_device.py`. Genesis
has an explicit in-process local simulation profile requiring no vendor library
or network; it is distinct from the ARM library vendor test mode described
below. Dedicated physical profiles set `shim.start_with_ioc: false`, so the IOC
does not SSH/start the service implicitly. The legacy deployment examples
below are not the current physical-startup procedure.

The ARM-only Vaunix library runs on the Raspberry Pi and the EPICS IOC runs on
the Genesis control computer. The Raspberry Pi hosts `vaunix_shim.py` and
`liblmshid.so`; the local `microwave_ioc.vaunix_lms` IOC communicates with that
service over TCP.

## Current deployment and startup policy

The earlier laptop-side deployment helper has been retired from the current
repository because its configuration schema no longer matches the canonical
Genesis Vaunix profile. Its implementation remains available in Git history.

Installation or replacement of the Raspberry Pi shim/service is a separate
commissioning/administrative operation. Normal Genesis physical startup does not
rebuild or redeploy the vendor library.

The current physical configuration is
`configs/commissioning/microwave_physical/vaunix_lms/settings.yaml`.

Normal physical operation uses the explicit Genesis Microwave workflow:
`genesis mw-start`, `genesis mw-status`, `genesis mw-rf-ready`, and
`genesis mw-stop`.

The physical launcher expects the deployed Raspberry Pi service to remain
available at the configured endpoint and verifies its hardware mode and RF-OFF
state before starting the local Vaunix IOC.

## Ownership

- canonical Vaunix profile: TCP endpoint, IOC timing, safety limits and shim metadata.
- `vaunix_shim.py`: ctypes signatures, device selection, API errors, 10 Hz /
  0.25 dBm conversion, and serialized access.
- `microwave_ioc/vaunix_lms/toolkit_device.py`: Genesis TCP/EPICS IOC; no ARM vendor library.

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
