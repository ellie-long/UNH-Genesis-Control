"""
Genesis execution-mode authorization.

This module contains no EPICS logic.

Modes
-----
inactive
    execution_enabled is false.

simulator
    Execution is permitted only for the dedicated simulator
    commissioning profile.  Network/device isolation is validated
    separately by the simulator-profile validator.

physical
    Execution additionally requires the sequencer process to run as
    the configured POSIX service account.

The physical account check uses the effective OS uid, not environment
variables such as USER or LOGNAME.
"""

from __future__ import annotations

import os
import pwd


MODE_INACTIVE = "inactive"
MODE_SIMULATOR = "simulator"
MODE_PHYSICAL = "physical"

VALID_ACTIVE_MODES = frozenset(
    {
        MODE_SIMULATOR,
        MODE_PHYSICAL,
    }
)


class ExecutionAuthorizationError(RuntimeError):
    """Raised when active execution is not authorized."""


def effective_os_user():
    """Return the username owning the current effective uid."""

    return pwd.getpwuid(
        os.geteuid()
    ).pw_name


def validate_execution_authorization(
    *,
    execution_enabled,
    execution_mode=None,
    required_os_user=None,
    os_user=None,
):
    """
    Validate one sequencer execution configuration.

    Returns the resolved mode.

    No EPICS access and no hardware access occurs here.
    """

    if not isinstance(
        execution_enabled,
        bool,
    ):
        raise TypeError(
            "execution_enabled must be bool"
        )

    if not execution_enabled:
        return MODE_INACTIVE

    mode = str(
        execution_mode or ""
    ).strip().lower()

    if mode not in VALID_ACTIVE_MODES:
        raise ExecutionAuthorizationError(
            "active execution requires "
            "execution_mode=simulator or physical"
        )

    if mode == MODE_SIMULATOR:
        return MODE_SIMULATOR

    required = str(
        required_os_user or ""
    ).strip()

    if not required:
        raise ExecutionAuthorizationError(
            "physical execution requires "
            "required_os_user"
        )

    actual = (
        effective_os_user()
        if os_user is None
        else str(os_user).strip()
    )

    if actual != required:
        raise ExecutionAuthorizationError(
            "physical execution requires OS user "
            f"{required!r}; current effective user "
            f"is {actual!r}"
        )

    return MODE_PHYSICAL
