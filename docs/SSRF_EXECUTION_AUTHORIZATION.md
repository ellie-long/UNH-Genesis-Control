# ssRF execution authorization

## Static authorization and device permissions

Ordinary `configs/ssrf` profiles remain inactive/read-only. The checked-in
`configs/ssrf_physical` hardware profiles permit writes but never initialize
outputs ON. Its sequencer has `execution_enabled: true`, `execution_mode:
physical`, `required_os_user: shrestha` and `require_prepared: true` for adaptive
burns. Effective POSIX UID must resolve to the configured user; an environment
username does not satisfy this check. Static permission never arms or starts RF.
Simulator authorization remains explicit and never acquires physical locks.

## Explicit runtime arms

Both arms start false and reset on restart/fault:

- `MANUAL_ARM_CMD` authorizes deliberate `RF_ON_CMD`; accepted RF ON consumes it.
- `ARM_CMD` authorizes one prepared adaptive `START_CMD`; accepted START consumes it.

Plain manual RF requires SMC/switch communications and write permissions, valid
carrier/level, safe initial source readbacks, routing and interlock permits.
WaveGen must confirm output OFF; no fitter/calibration/recipe/waveform is required.
Optional manual FM requires selected staged/prepared waveform and verified FM
configuration. Adaptive generation still requires valid calibrated fitter data.
These permissions never substitute for hardware/safety readback checks.

## Universal safe OFF

RF OFF, either DISARM and ABORT revoke both arms and request safe cleanup even
while idle. Cleanup independently attempts sweep disarm, RF OFF, FM OFF and
WaveGen OFF. Metadata-aware EPICS reads require valid status/severity and source
OFF before any safe routing change. It then disconnects ssRF, requires
NMR_CONNECT_PERMIT and restores NMR (RF mask 3 / NMR mask 0).
Failure is reported truthfully and cannot automatically resume RF.

## Local lifecycle and command ownership

Repository-defined Unix file locks are `/tmp/genesis-ssrf-physical/lifecycle.lock`
and `/tmp/genesis-ssrf-physical/control.lock`. START/STOP hold lifecycle across
the whole operation, including owner teardown. The authorized physical
sequencer holds control for its process lifetime. Independently launched
sequencers acquire lifecycle before acquiring control; the validated startup
wrapper already holds lifecycle for its child startup.
The child exemption requires the parent's matching PID/token lease. The lease
is cleared before unlock; a stale inherited environment cannot bypass later STOP.

STOP prefers a healthy sequencer's safe-off path. Only an unavailable sequencer
with exclusively acquired control permits fallback device-IOC cleanup. A live
but unreachable control owner blocks fallback. Concurrent lifecycle requests
fail nonzero before fallback writes; kernel locks release on close/process death.
Lock files are never unlinked. Tests inject isolated directories. These locks
coordinate repository-supported local owners, not arbitrary remote CA clients.

## EPICS access security limitation

Hardware command PVs use GENESIS_SSRF_CMD. The unchanged ACF allows CA identities
`shrestha` and `genesis_ssrf_sequencer`. The latter is a future dedicated service
identity, not this profile's mandatory OS user. Startup supplies a dedicated CA
username for sequencer launch; its actual use by CA clients still requires
physical acceptance. Do not blindly remove either allowed identity.

Direct CA clients with an allowed identity can bypass sequencer ordering. Expert
output/routing widgets are disabled; this does not enforce CA ownership.
Physical acceptance must verify identity/access policy without compromising
emergency safe-off. Service account/path permissions and EPICS endpoints require
explicit review when deploying the exact baseline to Linux/WSL.

See `SSRF_OPERATOR_QUICK_START.md` and `SSRF_FINAL_PHYSICAL_OPERATION.md`.
