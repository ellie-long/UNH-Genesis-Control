# Genesis Phase-I NMR dual-source commissioning

Status: software transport validated offline; hardware
acceptance and authenticated production activation pending.

## 1. Configuration and ownership

- Genesis Ubuntu/WSL: development and hardware acceptance.
- Mac: development and source control, not runtime acceptance.
- Genesis branch: feature/nmr-dual-source-validation.
- PyNMR branch: unh_dev/genesis_fitter_interface.
- PyNMR shared upstream must not be pushed or merged
  without the repository owner's review.
- LabVIEW receiver: loopback TCP 8777.
- PyNMR receiver: loopback TCP 8779.
- PyNMR protected IPC: disabled unless explicitly requested.
- IPC UID allowlist and optional shared group restrict
  access, but do not authenticate an acquisition session.
- Physical source selection must not be inferred from
  software source selection.

## 2. Verified software evidence

- Genesis: 173 tests and 28 subtests passed before
  the startup-CLI extension.
- PyNMR: 57 tests passed, 2 skipped; existing telnetlib
  deprecation warning in JLab hardware/daq.py.
- Cross-repository PyNMR worker to Genesis IPC/TCP:
  one matching completed event accepted, one simulated
  fitting callback invoked, process exit code 0.
- Startup-CLI validation: record the new test results
  in the commissioning log before acceptance.
- No physical switch test, real numerical fitting
  accuracy test, or live acquisition authorization
  has yet passed.

## 3. Operational boundaries

Protected startup flags:
- --nmr-protected --nmr-source pynmr
- --pynmr-port 8779
- --pynmr-ipc-path ABSOLUTE_SOCKET_PATH
- --pynmr-ipc-allowed-uid VERIFIED_UID (repeatable)
- --pynmr-ipc-shared-gid VERIFIED_GID (optional)

These configure a receiver, NOT a verified producer.
The event adapter remains unarmed without independently
authorized acquisition-session evidence. Do not bypass
that restriction to obtain live fitting.

PyNMR's shipped protected setting remains disabled.
The TestSim baseline-subtracted `basesub` signal is
used for Genesis; `fitsub` is retained diagnostically.

## 4. Physical Mini-Circuits switch acceptance

All steps require an operator at the rack and documented
approval. Do not change switch state during production
NMR acquisition, polarization measurement, ssRF burning,
or unknown RF conditions.

Stage A: Read-only, no output commands
- Identify both physical switch units/channels, wiring,
  serial/model, addresses, and firmware if available.
- Verify the actual controller endpoint and connectivity.
- Record present switch positions and hardware readbacks.
- Record present EPICS PV readbacks and alarm states.
- Confirm observed routing/masks against the rack diagram.
- Confirm the selected source and actual path are not
  conflated in software.

Stage B: Supervised no-RF switching
- Verify all relevant RF sources/output stages are OFF.
- Confirm target/feed cable isolation and the permitted
  test configuration with the responsible operator.
- Preserve initial positions and a known-safe rollback.
- Test the LabVIEW routing candidate mask 0.
- Test the PyNMR routing candidate mask 3.
- Verify both switch channels, actual output path and
  independent readback after every command.
- Check that invalid masks 1 and 2 are rejected by the
  Genesis control contract, not simply assumed invalid.
- Confirm disconnection/failure response is fail-closed.
- Restore the operator-approved idle routing.
- Do not energize RF for this stage.

Stage C: Hardware and application acceptance
- Verify a physically isolated test signal using the
  actual cable path and independent measurement.
- Check PyNMR TestSim and then actual NMR acquisition
  only when hardware and safety approvals are complete.
- Verify correct source-mask agreement, GUI indicators,
  EPICS status, archive and fitter results.
- Demonstrate stop, reconnect, source reversal and
  rollback procedures.
- Obtain operator/integrator acceptance signatures.

## 5. Commissioning log template

Date/time and timezone:
Operator(s):
System and Git SHA:
Switch model/address/serial:
Rack cabling diagram/reference:
Initial physical state:
RF-off and isolation confirmation:
Read-only identity/readbacks:
Approved test mask and observed actual routing:
Independent measurement:
EPICS readbacks and alarms:
Failure/reconnection result:
Restored safe state:
Pass/fail and unresolved issue:
Evidence/log path:
Reviewer/approval:

No hardware acceptance may be marked PASS merely
because simulation tests passed.

## 6. Release gate

Before merging or tagging an accepted deployment:
- Finish producer-session authorization and verify
  spoof/replay/disconnect rejection.
- Check actual numerical deuteron fits and spectra.
- Complete approved switch hardware acceptance.
- Validate startup and operator GUI on Ubuntu.
- Run the relevant full cross-subsystem regressions.
- Review both Git diffs, commit SHAs, ownership and
  preserved untracked test_data.
- Record accepted SHAs and create a timestamped
  release/acceptance tag after operator approval.
