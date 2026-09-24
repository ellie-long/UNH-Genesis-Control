# ssRF simulator fault-injection validation

## Scope

This validation applies to the Genesis Phase-I localhost ssRF
commissioning simulator.

It does not constitute physical RF timing, hardware damage-protection,
or final experimental commissioning.

## Safety invariant

For any detected fault after execution has begun, the sequencer attempts
to reach the safest achievable state:

- SMC RF OFF;
- SMC FM OFF;
- SMC sweep disarmed;
- Keysight WaveGen output OFF;
- ssRF path disconnected;
- NMR path restored only when its IOC and the NMR-connect permit are
  available.

Any loss of burn readiness also revokes the one-shot execution arm.
Recovery never restores authorization granted before a fault.

## Validated fault cases

### A — WaveGen backend communication loss

A staged and armed sequence lost communication with the WaveGen
simulator backend.

Validated behavior:

- burn readiness removed;
- one-shot arm revoked;
- all energy-producing outputs remained OFF;
- reconnect did not restore the old arm.

### B — WaveGen IOC disappearance

The WaveGen simulator backend remained available while its complete
EPICS IOC was stopped.

Validated behavior:

- required WaveGen PVs became unavailable;
- readiness and arm were removed;
- IOC restart cleared in-memory waveform staging;
- recovery required fresh waveform staging and a new arm.

### C — simultaneous NMR and ssRF path conflict

The localhost switch simulators were intentionally placed in a
conflicting state with the NMR path and ssRF path both connected.

Validated behavior:

- interlock entered a conflict state;
- hazardous-transition permits were removed;
- readiness and arm were revoked;
- RF/FM/WaveGen remained OFF;
- correcting the routing did not restore the previous arm.

### D — RF permit loss during an active simulated burn

During an active burn, an NMR/ssRF routing conflict was introduced,
removing `RF_ENABLE_PERMIT`.

Validated behavior:

- executor detected the monitored permit loss;
- execution ended as FAILED;
- fail-closed cleanup turned RF/FM/WaveGen OFF;
- sweep was disarmed;
- ssRF path was disconnected;
- NMR path was restored;
- no execution authorization survived.

Observed response times in this simulator are not physical timing claims.

### E — operator ABORT during an active simulated burn

ABORT was requested while RF/FM/WaveGen and the SMC sweep were active.

Validated behavior:

- sequence ended as ABORTED;
- cleanup turned all energy-producing outputs OFF;
- ssRF path was disconnected;
- NMR path was restored;
- execution remained disarmed afterward.

Observed response times in this simulator are not physical timing claims.

### F — required NMR IOC unavailable during cleanup

The NMR switch IOC was deliberately removed during an active burn.

Validated behavior:

- loss of the NMR IOC invalidated the live interlock condition;
- executor attempted fail-closed cleanup;
- RF/FM/WaveGen were turned OFF;
- sweep was disarmed;
- ssRF path was disconnected;
- automatic NMR restoration could not be completed while its IOC was
  absent;
- execution explicitly reported CLEANUP_FAILED rather than falsely
  reporting successful cleanup;
- after the IOC returned, the disconnected NMR state was confirmed;
- NMR was reconnected only after the NMR-connect permit was confirmed;
- recovery did not restore the previous arm.

## Result

The localhost execution system now fails closed across communication
loss, IOC loss, switch conflicts, live interlock-permit loss, operator
abort, and incomplete cleanup.

Physical commissioning remains a separate milestone.
