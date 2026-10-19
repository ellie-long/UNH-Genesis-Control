# Genesis Phase I — dual NMR read-only monitor (2026-10-13)

**Status:** deployed and browser-accessible on Ubuntu/WSL; legacy LabVIEW publisher transport validated. PyNMR subscriber connected, but a new PyNMR spectrum has not yet been observed by this monitor. Physical acquisition freshness and Phoebus GUI integration remain pending.

## Purpose and data flow

- **JLab PyNMR** is already fitted by the protected Genesis online fitter. This monitor *subscribes* to its existing fit-result broadcast `127.0.0.1:8778`.
- **Legacy LabVIEW 2018 DAQ** (`DESKTOP-RQV4GBI`, `192.168.1.10`) uses its own independently running local fitter with fit publisher `127.0.0.1:8778` on **that Windows host**. A separate SSH reverse tunnel forwards that publisher to Genesis Ubuntu `127.0.0.1:18778`.
- This monitoring sidecar subscribes to both publishers and renders frequency-axis, raw/processed signal, total fit, and component curves. Data are held in memory only; it does not persist spectra or write any PVs.
- Both publisher sockets and the dashboard listen only on loopback. The monitor does **not** use Genesis's protected raw NMR input socket `127.0.0.1:8777` nor its PyNMR socket `127.0.0.1:8779`, and cannot affect source selection, interlocks, hardware switches, RF, or fitting.
- Fit-publisher messages with `ts="test"`, mode `baseline`, or `P_fit=null` must not be presented as proof of physical real-time acquisition or of calibrated polarization.

## Files

- `scripts/genesis_nmr_dual_monitor.py` — standalone Python 3 / standard library read-only monitor and browser dashboard.
- `tests/test_genesis_nmr_dual_monitor.py` — offline fixture and local mock publisher tests. No DAQ network access.

## Run

1. Preserve the Windows tunnel: `ssh -N -T -R 127.0.0.1:18778:127.0.0.1:8778 -o ExitOnForwardFailure=yes utsav@192.168.1.6` from `DESKTOP-RQV4GBI`.
2. Ubuntu, Genesis repository: `python -m pytest -q tests/test_genesis_nmr_dual_monitor.py`.
3. Ubuntu, Genesis repository: `python scripts/genesis_nmr_dual_monitor.py --port 18880` (or run under a dedicated screen session to survive SSH drops).
4. On a Mac in a *separate* terminal: `ssh -N -L 18880:127.0.0.1:18880 -o ExitOnForwardFailure=yes utsav@192.168.1.6`.
5. Open `http://127.0.0.1:18880` on the Mac. Both views are labeled independently. If a source stops, the view is marked stale after 60 seconds and reconnects automatically when the publisher is available.

## Acceptance checks

- The LabVIEW view must show `451` points and reflect consecutive new fit-publisher messages; compare legacy source timestamps and spectra before calling it *live physical NMR*.
- The PyNMR view must show its accepted fitted messages after completion of an independently authorized real acquisition; it may remain stale if no new sweeps are running.
- Confirm no EPICS writes, no raw input message generation, no restarts of fitters, and no changes to source-selection or physical switching.
- Protect all existing tracked local changes and untracked `test_data/` when committing.

## Live transport acceptance — October 13, 2026

- Three offline monitor regressions passed.
- Standalone monitor started on Ubuntu loopback port 18880.
- Mac browser successfully opened both monitoring panels.
- Legacy LabVIEW publisher was received over the authenticated
  Windows-to-Ubuntu SSH reverse tunnel on port 18778.
- First observed monitor state: LabVIEW connected, two messages
  received, fresh, with a 451-point spectrum.
- PyNMR publisher connected on port 8778; no newly published
  PyNMR spectrum had been received by the monitor.
- Legacy publisher sample reported `ts=test`, baseline mode,
  and null polarization fit values. Physical acquisition
  freshness is therefore not established by this test.
- No modification to either acquisition system, protected fitter,
  hardware switching, RF controls, or EPICS PVs.

## Outstanding

- This is an independent dual-spectrum browser monitor, not yet a widget embedded in the Phoebus `.bob` operator screen.
- Persist the Windows SSH tunnel using an approved service/user workflow if continuous operations require it.
- Determine whether the legacy publisher's `ts=test` represents actual acquisition output or a simulation/replay before reporting physics acceptance.
