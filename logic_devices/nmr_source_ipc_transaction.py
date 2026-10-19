"""Authorized local-IPC adapter for sequencer-owned software NMR selection.

No EPICS writes, physical Mini-Circuits switching, or ssRF RF commands.
This is a callable adapter, NOT an installed IOC/GUI command handler.
The caller must provide an alarm/freshness-validated fitter readback.
"""

import math
from pathlib import Path

from .nmr_source_ipc_client import request_source_change
from .nmr_source_transaction import execute_nmr_source_change


RF_OUTPUT_RBV = 'UNH:GEN:SSRF:GEN:RF_RBV'


def validate_ipc_configuration(*, path, uid, pid, read_fitter, timeout_s):
    """Validate runtime configuration before attempting a reservation."""
    if type(path) is not str or not Path(path).is_absolute():
        raise ValueError('Absolute NMR source-control socket path required')
    if type(uid) is not int or uid < 0 or type(pid) is not int or pid <= 0:
        raise ValueError('Explicit trusted fitter UID and PID required')
    if not callable(read_fitter):
        raise TypeError('Fitter source and epoch readback callback required')
    if (type(timeout_s) not in (int, float)
            or not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError('Positive finite IPC timeout required')


async def execute_nmr_source_change_via_ipc(
    *,
    requested,
    sequencer,
    read_fitter,
    path,
    allowed_fitter_uid,
    allowed_fitter_pid,
    timeout_s=2.0,
    poll_attempts=10,
    poll_interval_s=0.1,
    read_rf_off=None,
):
    """Run a reservation-protected, mutually authenticated IPC selection.

    read_fitter must return {'selected_source': str, 'route_epoch': int},
    using independently verified, alarm-aware EPICS observations. The IPC
    result is additionally required to be observable before release.

    The socket client pins the FITTER kernel UID/PID. The fitter server
    separately pins the SEQUENCER UID/PID. Neither identity is learned from
    an untrusted message. Both identity mappings must be configured by the
    controlled startup lifecycle and refreshed after process restarts.
    """
    validate_ipc_configuration(
        path=path, uid=allowed_fitter_uid, pid=allowed_fitter_pid,
        read_fitter=read_fitter, timeout_s=timeout_s,
    )

    if read_rf_off is not None and not callable(read_rf_off):
        raise TypeError("Fresh RF readback callback must be callable")

    expected_ipc_epoch = None

    async def verify_rf_off():
        # EpicsIO.read_safety must reject invalid/stale/alarmed CA metadata;
        # the numeric zero check alone is NOT sufficient for commissioning.
        if read_rf_off is not None:
            await read_rf_off()
            return
        rf = await sequencer._execution_io.read_safety(RF_OUTPUT_RBV)
        if (type(rf) not in (int, float)
                or not math.isfinite(rf) or rf != 0):
            raise RuntimeError('SMC100A RF OFF is not verified')

    async def guarded_fitter_readback():
        observed = await read_fitter()
        if type(observed) is not dict:
            raise RuntimeError('Invalid fitter source/epoch readback')
        # Avoid completing against a lagging EPICS readback whose epoch is
        # older than the FITTER-ACKNOWLEDGED source-change epoch.
        if expected_ipc_epoch is not None:
            epoch = observed.get('route_epoch')
            if type(epoch) is not int or epoch < expected_ipc_epoch:
                return {'selected_source': None, 'route_epoch': -1}
        return observed

    async def authorized_ipc_write(source):
        nonlocal expected_ipc_epoch
        await verify_rf_off()  # second check, immediately before dispatch
        acknowledged = await request_source_change(
            path=path,
            requested=source,
            allowed_fitter_uid=allowed_fitter_uid,
            allowed_fitter_pid=allowed_fitter_pid,
            timeout_s=timeout_s,
        )
        if (type(acknowledged) is not dict
                or acknowledged.get('selected_source') != source
                or type(acknowledged.get('route_epoch')) is not int
                or acknowledged['route_epoch'] < 0):
            raise RuntimeError('Unverified source-control IPC reply')
        expected_ipc_epoch = acknowledged['route_epoch']

    return await execute_nmr_source_change(
        requested=requested,
        sequencer=sequencer,
        read_fitter=guarded_fitter_readback,
        write_fitter=authorized_ipc_write,
        poll_attempts=poll_attempts,
        poll_interval_s=poll_interval_s,
        before_release=verify_rf_off,  # third check, immediately before release
    )
