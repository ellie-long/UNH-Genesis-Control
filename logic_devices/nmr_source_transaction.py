"""Sequencer-owned NMR software-source transition.

No direct EPICS imports or hardware access. The caller supplies
read/write operations only after the authorization path is ready.
"""

import asyncio
import math

from logic_devices.nmr_source_ack import (
    SOURCES,
    validate_source_change_ack,
)


async def execute_nmr_source_change(
    *,
    requested,
    sequencer,
    read_fitter,
    write_fitter,
    poll_attempts=10,
    poll_interval_s=0.1,
    before_release=None,
):
    """Change software input under exclusive sequencer reservation.

    Uncertain outcomes retain the reservation. Recovery must be
    performed explicitly; timeouts never release it.
    """
    if type(requested) is not str or requested not in SOURCES:
        raise ValueError("Unsupported NMR software source")

    if (
        type(poll_attempts) is not int
        or not 1 <= poll_attempts <= 100
    ):
        raise ValueError("Invalid acknowledgment attempt count")

    if (
        type(poll_interval_s) not in (int, float)
        or not math.isfinite(poll_interval_s)
        or poll_interval_s < 0
    ):
        raise ValueError("Invalid acknowledgment interval")

    if before_release is not None and not callable(before_release):
        raise TypeError("pre-release verifier must be callable")

    # Reservation includes alarm-aware RF OFF verification.
    await sequencer._acquire_nmr_source_change_reservation()

    # No automatic release below: once reserved, an exception or
    # cancellation leaves the system blocked until reconciliation.
    command_dispatched = False

    try:
        before = await read_fitter()

        if not isinstance(before, dict):
            raise RuntimeError("Invalid initial fitter observation")

        previous_source = before.get("selected_source")
        previous_epoch = before.get("route_epoch")

        if previous_source not in SOURCES:
            raise RuntimeError("Initial fitter source unverified")

        if type(previous_epoch) is not int or previous_epoch < 0:
            raise RuntimeError("Initial acquisition epoch unverified")

        if previous_source == requested:
            raise RuntimeError("Source already selected")

        # Mark dispatch BEFORE awaiting the write. A timeout or
        # cancellation during the write has an uncertain outcome.
        command_dispatched = True
        await write_fitter(requested)

    except BaseException:
        if not command_dispatched:
            sequencer._cancel_nmr_source_change_reservation()
        raise

    for attempt in range(poll_attempts):
        observed = await read_fitter()

        if not isinstance(observed, dict):
            raise RuntimeError("Invalid fitter acknowledgment")

        if validate_source_change_ack(
            requested=requested,
            previous_source=previous_source,
            previous_epoch=previous_epoch,
            observed_source=observed.get("selected_source"),
            observed_epoch=observed.get("route_epoch"),
        ):
            # The authorized IPC adapter verifies alarm-aware RF OFF
            # immediately before releasing the reservation. A failed
            # verification leaves the reservation in place.
            if before_release is not None:
                await before_release()
            sequencer._complete_nmr_source_change_reservation(
                confirmed=True
            )
            return observed

        if attempt + 1 < poll_attempts:
            await asyncio.sleep(poll_interval_s)

    raise TimeoutError(
        "NMR source change unconfirmed; "
        "sequencer reservation retained"
    )
