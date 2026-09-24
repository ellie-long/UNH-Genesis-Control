# ssRF execution authorization

Genesis Phase-I separates three independent execution gates.

## Static device write gate

Each hardware IOC has `write_enabled`.

Normal Genesis configuration keeps this `false`.

The localhost simulator execution profile intentionally sets it `true`
only for simulated devices.

## EPICS command access

Physical-device command records use the
`GENESIS_SSRF_CMD` Access Security group.

The protected command inventory includes:

- all SMC100A RF/FM/sweep command records;
- Keysight output, period, voltage, waveform staging and load commands;
- NMR switch `PORT_CMD`;
- ssRF switch `PORT_CMD`.

`WAVEFORM_CMD` is protected because changing staged waveform data can
change the subsequent burn even though staging itself does not yet send
the waveform to the instrument.

Physical execution requires the sequencer process to run under the
dedicated POSIX service account:

`genesis_ssrf_sequencer`

The operating-system identity is used. Genesis does not synthesize or
override the Channel Access username in Python.

## Execution mode

The sequencer supports:

- inactive: no active execution;
- simulator: localhost-only simulator execution;
- physical: requires the configured dedicated OS service account.

Physical mode fails closed when the effective OS user does not match
`required_os_user`.

## Runtime one-shot arm

Active execution begins disarmed.

`ARM_CMD` authorizes one START only.

An accepted `START_CMD` immediately consumes the arm.

A second START requires another explicit ARM.

`DISARM_CMD` clears authorization.

`ABORT_CMD` always clears authorization and retains the existing
graceful-stop-at-next-checkpoint behavior.

IOC restart always returns to disarmed state.

`EXECUTION_ARMED` is read-only status.

## Safety relationship

Execution authorization does not replace burn readiness or the ssRF
interlock.

A burn still requires:

1. valid burn parameters;
2. valid staged waveform;
3. all required device communications;
4. device write gates;
5. safe initial RF/WaveGen state;
6. confirmed switch state;
7. live interlock permits;
8. one-shot execution arm.

The sequence continues to verify permits at hazardous transitions and
runs the existing fail-closed cleanup sequence after execution,
abort, or post-write failure.

## Physical commissioning requirement

Before physical ssRF execution is enabled:

- create and use the dedicated sequencer OS/service account;
- run the sequencer as that account;
- confirm ordinary operator CA and PVA clients cannot write protected
  device commands;
- confirm the dedicated sequencer account can write only through the
  controlled sequencer path;
- keep `initialize_on_connect: false`;
- perform controlled hardware commissioning before enabling a burn.
