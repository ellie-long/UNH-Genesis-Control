# Genesis ssRF Physical No-RF GUI Demo

This dedicated Phase-I demonstration executes the operator workflow:

```text
offline NMR replay
  -> online fitter
  -> Update Constants / calibrated fit
  -> ssRF fit
  -> existing adaptive WaveformPlan runtime
  -> SEQ:WAVEFORM_STAGE_REQ
  -> physical Keysight 8192-point ARB load
  -> physical NMR switch 0 -> 3
  -> physical ssRF switch 3 -> 0
  -> physical ssRF switch 0 -> 3
  -> physical NMR switch 3 -> 0
```

Hard safety boundary:

- Physical SMC is read-only to this demo and must report RF=OFF, FM=OFF.
- The demo code contains no SMC write methods.
- Keysight `OUTPUT_CMD` is not used; physical WaveGen output must remain OFF.
- Only `WAVEFORM_CMD` and `LOAD_CMD` are used on the physical upload-only Keysight IOC.
- LZY-22+ / Siglent remain OFF.
- No actual RF burn is performed.
- The demo uses one-shot ARM followed by RUN DEMO.
- Demo ARM authorizes switch motion only; it cannot enable SMC RF/FM or Keysight output.
- The normal production sequencer/ARM logic is unchanged.

Start:

```bash
./scripts/start_ssrf_physical_no_rf_demo.sh
```

Then use `gui/genesis_ssrf.bob` only, in order:

1. START FITTER
2. REPLAY EQ
3. UPDATE CONSTANTS
4. ssRF MODE
5. PREPARE / LOAD
6. ARM
7. RUN DEMO

Stop/restore:

```bash
./scripts/stop_ssrf_physical_no_rf_demo.sh
```

Expected final physical state:

```text
SMC RF      = OFF
SMC FM      = OFF
WaveGen OUT = OFF
NMR mask    = 0
ssRF mask   = 3
```


## Consolidated operator workflow

The normal operator screen is `gui/genesis_ssrf.bob`.

After fitter setup/calibration, the deliberate execution pattern is:

```text
ARM
  -> BLAST / BURN
```

In the physical no-RF commissioning profile, BLAST/BURN performs a fresh
ssRF fit, adaptive WaveformPlan generation, 8192-point physical Keysight
ARB load, physical ssRF routing, no-RF dwell, and automatic NMR restore.

The main GUI shows every major execution state/reason and the relevant
fitter, waveform, Keysight, SMC, and switch readbacks.
