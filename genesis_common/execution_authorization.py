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
    Execution additionally requires the sequencer process effective
    POSIX account to appear in an explicit configured allow-list.

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
    required_os_users=None,
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

    if required_os_users is None:
        raise ExecutionAuthorizationError(
            "physical execution requires "
            "non-empty required_os_users"
        )

    if isinstance(required_os_users, str) or not isinstance(
        required_os_users,
        (list, tuple, set, frozenset),
    ):
        raise TypeError(
            "required_os_users must be a sequence "
            "of POSIX usernames"
        )

    allowed = []

    for value in required_os_users:
        if not isinstance(value, str):
            raise TypeError(
                "required_os_users entries must be strings"
            )

        user = value.strip()

        if not user:
            raise ExecutionAuthorizationError(
                "required_os_users may not contain "
                "an empty username"
            )

        if user not in allowed:
            allowed.append(user)

    if not allowed:
        raise ExecutionAuthorizationError(
            "physical execution requires "
            "non-empty required_os_users"
        )

    actual = (
        effective_os_user()
        if os_user is None
        else str(os_user).strip()
    )

    if actual not in allowed:
        raise ExecutionAuthorizationError(
            "physical execution requires OS user "
            "to be one of "
            + ", ".join(repr(user) for user in allowed)
            + f"; current effective user is {actual!r}"
        )

    return MODE_PHYSICAL
