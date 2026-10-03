# Deliberate Microwave RF operation

This document describes the integrated supervised Microwave RF-control path in the current Genesis repository. The former RF-operation and deterministic-shutdown development branches are now part of the canonical physical workflow. Physical validation has exercised the real UCA, Vaunix/LabBrick, Supervisor, robot, GUI, and archiver stack. The RF path is not yet considered fully accepted because the remaining physical issues documented below still require correction and retest.

## Operator path and state

Normal operation is Main/Supervisor GUI → `UNH:GEN:MW:SUP:RFEnable` → supervisor → `UNH:GEN:MW:SRC:MW_RF_CMD` → Vaunix IOC → leased TCP connection → shim → vendor LabBrick. `RFEnable` remains the deliberate operator request. There is no second ARM state, calibration gate, or automatic restoration of an earlier request.

| PV suffix | Meaning |
| --- | --- |
| `SUP:RFEnable` | Operator authorization/request; cleared on OFF, ABORT, startup, source-session change, or required communication loss |
| `SUP:RFActual`, `SUP:RFValid`, `SUP:RFState` | Sampled vendor actual, its validity, and UNKNOWN/OFF/ON indication |
| `SUP:RFPermissive`, `SUP:InterlockMessage` | ON-transition readiness and reason; not a continuous 5 V trip |
| `SUP:ControlOwned` | Sampled exclusive source lease status |
| `SUP:RFAbort` | Repeated deliberate RF OFF / ABORT action |
| `SRC:MW_RF_CMD` | RF command; ON requires fresh supervisor request plus independent IOC prerequisites |
| `SRC:MW_RF` | Compatibility OFF-only command, **not** RF readback; raw ON is rejected |
| `SRC:MW_RF_RBV`, `SRC:MW_RF_VALID`, `SRC:MW_RF_STATE` | Vendor-derived actual, validity, and UNKNOWN/OFF/ON |
| `SRC:MW_RF_TIME` | Wall-clock time of last successful vendor RF observation |
| `SRC:MW_RF_DONE_TIME` | Wall-clock time of last RF command callback completion; polling cannot substitute for command execution |
| `SRC:MW_RF_READY` | Safe OFF/sweep-stop baseline established; a failed safe action latches this false until successful OFF/reacquisition |
| `SRC:MW_RF_ERROR` | Last RF operation failure; confirmed RF OFF can coexist with a sweep-stop failure |
| `SRC:MW_CONTROL_OWNED`, `SRC:MW_CONTROL_SESSION` | Remote lease ownership and local successful-acquisition generation |

All names above have prefix `UNH:GEN:MW:`. Validity must be interpreted with live communication and freshness; a disconnected IOC's last value is not proof of hardware state. `RFActual`/`MW_RF_RBV` may retain an older sample while validity is false. UNKNOWN is not confirmed OFF. CA write completion or a zero command value is not hardware confirmation.

The Microwave archive quick view prefers the new UNKNOWN/OFF/ON state over the legacy command alias. Historical archives without the new state retain their old sampled-RF trace; archived data is not rewritten.

## ON-transition prerequisites

The supervisor and source IOC independently check fresh required inputs. The current maximum readback age is 4 seconds, using successful IOC poll timestamps and the vendor RF observation time. Alarms, nonfinite values, invalid flags and unavailable inputs block ON.

Before ON require:

- UCA and Vaunix communications healthy/fresh;
- UCA output readback ON and measured voltage in the existing approximately 5.0 ± 0.1 V transition window;
- a valid RF observation, established safe baseline and exclusive source lease;
- hardware/non-simulation source mode for the physical profile;
- carrier within 11.5–11.8333333333 GHz, corresponding to the existing ×12 output range 138–142 GHz, also constrained by the vendor/shim limits;
- power within the existing configured −40 to +10 dBm envelope, also constrained by vendor limits; configuration may narrow this envelope, not widen it;
- PLL lock, as required by the existing physical readiness procedure;
- fresh supervisor authorization at the source IOC and no outstanding OFF/ABORT/reconnect invalidation.

Calibration validity, Magic constants, robot referencing, joystick commissioning and archiver availability are not RF-enable requirements. Magic actions remain unavailable with unconfigured constants. Generic and local simulation profiles remain explicitly separate from physical mode.

After confirmed ON, deliberate UCA voltage reduction remains possible for attenuation. Neither reduced UCA voltage nor PLL loss creates a new continuous RF trip. PLL loss remains diagnostic and needs physical acceptance of its response policy. The robot remains independent of RF authorization; this change adds no motion controls or robot interlocks.

## RF OFF / ABORT

The RF switch OFF action and `SUP:RFAbort` clear authorization, invalidate pending ON work, cancel software frequency sequencing and serialize a source OFF command. The IOC independently attempts hardware sweep stop and RF OFF even if stopping the sweep fails. It queries vendor RF independently using `RF?`, and verifies sweep stop where supported.

Repeated OFF and ABORT execute the hardware path even when the command already says OFF. Writable-record hardware synchronization uses `process=False`; input records use the installed pySoftIOC input API. Actual readback is never copied into an RF command record.

The supervisor waits for a new RF command completion and fresh vendor observation. Failed confirmation is UNKNOWN with an error. A vendor-confirmed OFF with failed sweep stop is reported as OFF plus an operation error; ON readiness remains blocked. `SweepStopCommand` and `SweepCancel` continue to mean frequency-sequencing control, not RF shutdown.

Normal operation uses supervisor software sweeps. Physical raw hardware-sweep start is not exposed/authorized by the IOC because it has no supervised operational command path; sweep stop remains available. Vendor hardware-sweep RF behavior remains a physical-acceptance item, not an assumption about the library.

## Startup and recovery

Shim device open, control acquisition and IOC connection/reconnection establish safe OFF. Sweep-stop failure never suppresses the RF-OFF attempt. The source is not ready if the safe baseline cannot be verified. The supervisor initially disables RF and explicitly confirms OFF before allowing ON. Startup source preparation also verifies a completed OFF operation before setting frequency/power.

Recovery clears source commands. A source-session change clears supervisor authorization; actual OFF also clears an old ON request. Restarting the supervisor requests OFF. No restart, reconnect, lease reacquisition or communication recovery replays ON. The system-start wrapper refuses to reuse an existing stack with ON/unconfirmed RF instead of treating it as startup-ready.

## Remote control lease

The physical Vaunix IOC is the normal connection-bound owner. All mutating shim commands require ownership; diagnostic queries remain available and do not renew the lease or initiate safety writes.

| Request | Response / effect |
| --- | --- |
| `CONTROL ACQUIRE` | `OK <timeout-seconds>` only after safe OFF; competing owners receive ERR |
| `CONTROL HEARTBEAT` | Owner-only renewal, `OK <timeout-seconds>` |
| `CONTROL?` | `1` only for the querying connection with an unexpired lease |
| `CONTROL RELEASE` | Owner-only release and safe action, or ERR on failure |
| `RF 0` | Owner-only independent sweep-stop/mute/verification safe action |

`--lease-timeout` configures the remote monotonic timeout; default and checked-in service value are **6 seconds**. The IOC renews during its normal healthy polling loop. Ordinary diagnostic reads never renew ownership. Owner disconnect or lease expiry clears ownership and initiates sweep stop, RF OFF and vendor verification. Failed cleanup is retried by the remote watchdog. Reacquisition always starts with safe OFF, never the previous ON state.

The watchdog checks at most every 0.1 second, subject to serialization and vendor call completion. Six seconds is a lease-expiry threshold, **not a guaranteed hardware mute deadline**. A stuck shim process/vendor call, failed USB interface or hardware unable to accept OFF cannot be protected by this software lease. Independent hardware protection remains necessary for such failures.

The lease is exclusive ownership, not authentication. A client with network reachability can explicitly acquire a vacant lease, but cannot steal an active lease. Low-level maintenance clients remain outside the normal GUI path and must acquire the same lease and obey vendor limits. The IOC rechecks supervisor request and safety inputs for raw `MW_RF_CMD` ON; CA host/user identities alone are not the authorization mechanism. Arbitrary malicious writes to supervisor records are outside this software boundary.

## Communication loss and manual protections

Required communication loss clears supervisor authorization and requests OFF wherever the local source path remains usable. If confirmation is unavailable, state is UNKNOWN. Source-side loss of supervisor heartbeat also requests OFF. Laptop/network/client loss is handled independently by the remote lease watchdog while the shim and hardware remain functional.

The apparatus procedure in `MICROWAVE_PHYSICAL_OPERATION.md` remains authoritative: VDI green light before USB/source control, UCA at 5 V before RF enable or robot/stand motion, SIG visible and negative, reflected power below 10%, and the initial reflected-signal acceptance procedure's attenuation limits. VDI and SIG are not instrumented here. An approximately-5-V physical permissive was validated, but the older documentation's certification caveat for the precise ±0.1 V tolerance still needs experiment-owner resolution.

## Current integration status and remaining physical work

The supervised RF path and deterministic Microwave shutdown are integrated into
the canonical physical workflow. `tools/microwave_shutdown.py` now verifies
fresh vendor RF OFF state, RF validity, safe-source readiness, inactive hardware
sweep state, Supervisor OFF state, UCA safe state, robot state, and local process
ownership before terminating dedicated physical owners.

The canonical operator path is:

- `genesis mw-start`
- `genesis mw-status`
- `genesis mw-rf-ready`
- `genesis mw-stop`

Physical validation has exercised supervised RF ON/OFF, frequency control,
UCA attenuation, bounded robot motion, archiving, GUI operation, and the
required physical shutdown order. Two physical defects remain open:

1. **Carrier persistence after RF OFF.** A 140 GHz operating point has
   intermittently changed to approximately 144 GHz after RF OFF or during the
   shutdown sequence. Software tests protect the intended carrier-preservation
   behavior, but the physical trigger and final correction are not yet closed.
   Final RF acceptance requires repeated 140 GHz OFF -> ON -> OFF cycles to
   remain at the commanded carrier.

2. **Local cleanup completion after safe shutdown.** The dedicated stop
   workflow has successfully established the physical safe state and then
   aborted during local process cleanup when a process-inspection command
   returned a normal nonzero result for an already-absent process. Shutdown
   cleanup must treat already-stopped owners as stopped, continue cleanup of
   remaining owners, and still report any genuine survivor.

The generic combined Mac shutdown path has been retired. The canonical
Microwave shutdown path is `scripts/microwave_physical.sh` together with
`tools/microwave_shutdown.py`.

Before the Microwave subsystem is treated as a fully accepted physical
baseline, repeat the physical RF/SIG procedure after the carrier issue is
corrected, verify complete shutdown cleanup, confirm the final PLL-loss policy
and exact UCA transition tolerance with the experiment owners, and retain the
manual VDI/SIG protections until equivalent hardware readbacks are instrumented.
