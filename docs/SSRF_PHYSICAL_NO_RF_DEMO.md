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
3. CALIBRATE
4. ARM
5. BLAST / BURN

`ssRF MODE` is optional because BLAST/BURN selects the ssRF fitter mode and
performs the fresh fit → WaveformPlan → physical load transaction automatically.

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
## Final integrated main-GUI validation

The physical no-RF commissioning workflow was successfully exercised
from the normal `gui/genesis_ssrf.bob` operator screen.

The operator sequence was:

```text
START FITTER
  -> REPLAY EQ
  -> CALIBRATE
  -> ARM
  -> BLAST / BURN
```

BLAST / BURN automatically performed:

```text
fresh ssRF fit
  -> adaptive WaveformPlan
  -> 8192-point physical Keysight ARB load
  -> NMR switch 0 -> 3
  -> ssRF switch 3 -> 0
  -> no-RF commissioning dwell
  -> ssRF switch 0 -> 3
  -> NMR switch 3 -> 0
  -> COMPLETE
```

Observed final state:

```text
Waveform stage      = LOADED
Keysight ARB points = 8192
Keysight output     = OFF
NMR mask            = 0
ssRF mask           = 3
SMC RF              = OFF
SMC FM              = OFF
ARM                  = consumed / OFF
Fit mode             = ssRF
```

Both physical Mini-Circuits switch boxes visibly actuated and returned.

`gui/genesis_ssrf_demo.bob` was a temporary commissioning harness and
has been removed. The physical no-RF commissioning backend and profile
remain available for future controlled hardware checks.


## Operator entry point

The concise operator procedure is maintained in `docs/SSRF_OPERATOR_QUICK_START.md`. Use that document for the final start → GUI workflow → viewer → safe-stop sequence.

## 2026-09-28 Phase-I acceptance

The final lab acceptance and clean reproducibility run passed. The physical
Keysight reported an 8192-point loaded/ready waveform with output OFF; both
physical switches actuated and restored NMR mask 0 / ssRF mask 3; SMC RF/FM
remained OFF; the workflow reached COMPLETE; and the final session
`ssrf-20260928T173405Z-bd5ae96e` was archived with fit and waveform NPZ data.

The acceptance exercise also motivated three operator hardening changes:
Phoebus resource-forward retry during startup, exact stale physical-upload IOC
cleanup/start/stop handling, and clearer main-GUI command/indicator semantics.
