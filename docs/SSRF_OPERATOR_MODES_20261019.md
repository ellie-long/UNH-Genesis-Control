# ssRF fit-gated, operator reference, and continuous cycles

Development branch: `feature/ssrf-pynmr-only`. These changes are NOT an
accepted physical-burn release until Ubuntu regression and authorized hardware
commissioning succeed. No startup option arms or starts RF hardware.

* `./scripts/genesisctl.sh ssrf-start`: original fit-gated workflow.
* `./scripts/genesisctl.sh ssrf-start --burn-mode operator --material ND3 --polarization 0.30 --center-mhz 32.677988031`: reference-frequency waveform, independent of fit. Requires physics review of reference center and target material before any burn. Existing waveform/voltage and sequencer validation remains intact.
* `./scripts/genesisctl.sh ssrf-continuous --mode operator --cycles 3 --delay-s 10`: dry-run printout; **does not write EPICS**.
* `./scripts/genesisctl.sh ssrf-continuous --mode operator --cycles 3 --delay-s 10 --execute`: physical sequence request, **ONLY after separate authorization and software/bench acceptance**.

Every continuous cycle requests `GENERATE / LOAD`, verifies `READY_TO_ARM`,
uses the existing ARM + START one-shot sequencer interface, waits for COMPLETE,
then verifies RF/FM/WaveGen OFF and receiver-safe switch masks before the delay.
STOP is Ctrl-C or SIGTERM (handled as a graceful event); ABORT is requested on exit and readbacks must be verified.
An invalid fit in FIT mode, any failed preparation, or an unsafe readback stops
continuous operation, not retries indefinitely. The repeat delay is separate
from SMC burn duration, which remains governed by `FM_REPEATS`, `FM_PERIOD_S`
and the validated sequencer plan; Chhetra's 1-second duration is NOT silently
substituted for the SMC sweep.

Operator-reference planning uses Chhetra's 41+41 frequency bins and uniform
per-bin dwell when no trusted NMR heights exist. This intentionally does **not**
pretend bad NMR data are a calibrated fit. Its existing reference MHz-to-V
convention still requires authorized physics/hardware verification.

Chhetra's original direct-PyVISA module is retained externally as a reference,
not installed as a competing device controller. The shared Genesis sequencer
and interlocks remain the only RF execution path.

## Offline numerical comparison

Before adopting Chhetra's updated stepped algorithm:

- All six operator-reference waveforms matched Chhetra numerically.
- Adaptive frequency bins and measured-height weights matched.
- Full adaptive arrays differed because Genesis used interpolation
  while Chhetra used stepped, height-weighted dwell.
- Adaptive mean absolute differences: 0.00542–0.00857 normalized.
- Maximum sample differences: 0.79786–0.90922 normalized.
- This comparison documents the previous implementation, not the final one.

## Chhetra stepped adaptive waveform update (pending offline validation)

- The fitted Genesis waveform now uses Chhetra's updated single cumulative
  41+41-bin height-weighted dwell, searchsorted(..., side="right"), 8192-sample
  period and endpoint pinning at -0.5 V/+0.5 V.
- The previous interpolation difference documented above is historical.
- Retain legacy golden files unchanged for reference; current regression
  assertions test stepped output instead.
- Run the separate numerical comparison against Chhetra's original uploaded
  source for all six material/objective combinations before software commit.
- This is waveform-math equivalence only; physical MHz/V/FM scaling and
  powered RF acceptance remain unverified.
