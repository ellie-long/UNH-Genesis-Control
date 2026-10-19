# Live PyNMR-to-Genesis NMR fitter acceptance — 2026-10-12

## Scope and acceptance

Validated on Ubuntu/WSL DAQ with the target stick and RF output
chain disconnected. The physical NMR Mini-Circuits route was
JLab PyNMR, PORT2/mask 3. The separate RF/burn switch was not
exercised.

Real PyNMR Deuteron acquisition completed events of 640 sweeps,
approximately 15–17 seconds each. The Genesis online ssRF
fitter and Phoebus workflow visualization showed updating
incoming NMR spectra, processed NMR/total-fit traces, and NMR
polarization history.

This validates the NMR acquisition-to-visualization path,
not a physically meaningful polarization measurement or
ssRF burn operation.

## Version and authorization

- Genesis branch: `feature/nmr-dual-source-validation`.
- Genesis baseline before acceptance commit:
  `4aaa4bb46eed037a985ed96d876a714d1e354203`.
- PyNMR local branch: `unh_dev/genesis_fitter_interface`.
- PyNMR local commit:
  `541252ed94f75a04aa806febf309b274fe043adf`.
- PyNMR source is not changed or pushed by this Genesis milestone.
- Local IPC requires explicit UID permission and a
  commissioning-only PyNMR PID/start-ticks pin verified against
  Linux process identity.
- Completed-event metadata is paired with the exact TCP
  spectrum and protected by source-route/epoch checks.
- The PID-based authorization is for controlled local
  commissioning, not cryptographic production authentication.
- Genesis fitter startup uses `GENESIS_PYNMR_PRODUCER_PID`
  and `GENESIS_PYNMR_PRODUCER_START_TICKS`.
- After a PyNMR process restart, route transition or lost
  connection, establish a fresh validated session.
- The local PyNMR Deuteron profile uses
  `genesis_protected: true`, `signal: basesub`,
  TCP `127.0.0.1:8779`, and
  `/run/genesis-nmr-ipc/events.sock`.
- The local PyNMR streamer YAML and acquisition session/history
  files are intentionally excluded from this Genesis commit.

## Acceptance evidence and limitations

- 2026-10-12: actual PyNMR raw, baseline-subtracted and
  fit-subtracted signals were observed.
- Genesis Incoming NMR, Processed NMR/Total Fit, and
  NMR Polarization History plots updated from actual acquisition.
- Protected NMR regression suite rerun before commit.
- The fitter's legacy startup statement about acquisition
  binding remains to be updated separately.
- PyNMR previously reported a `NoneType.write` eventfile
  warning; this needs independent investigation.
- Outstanding: permanent GUI layout and switch labels,
  WaveGen integration, ssRF recipe/burn feedback,
  RF safety acceptance and PyNMR repository review.

This milestone does not certify complete ssRF commissioning.
