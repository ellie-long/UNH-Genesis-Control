"""
Genesis EPICS Access Security configuration helper.

Access Security is optional.  When general.access_security_file is absent,
the IOC startup path is unchanged.

When configured, the policy filename is resolved to an absolute path and
registered with EPICS before iocInit().
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

from softioc import imports


class AccessSecurityConfigurationError(RuntimeError):
    """Raised when an IOC access-security policy cannot be configured."""


def _configure_function(
    function,
    argtypes,
):
    function.argtypes = argtypes
    function.restype = ctypes.c_int
    return function


def configure_access_security(
    settings,
    *,
    project_root=None,
    dbcore=None,
):
    """
    Register the optional IOC Access Security configuration.

    This function deliberately does not call asInit().  Initial Access
    Security initialization is performed by EPICS during iocInit() after
    asSetFilename() has supplied the policy filename.

    Returns:
        Absolute policy filename as a string, or None when disabled.
    """

    general = settings.get(
        "general",
        {},
    )

    configured_file = general.get(
        "access_security_file"
    )

    if configured_file in (
        None,
        "",
        "None",
    ):
        return None

    configured_file = str(
        configured_file
    )

    path = Path(
        configured_file
    )

    if not path.is_absolute():
        root = Path(
            project_root
            if project_root is not None
            else os.getcwd()
        )

        path = root / path

    path = path.resolve()

    if not path.is_file():
        raise FileNotFoundError(
            "Access Security file does not exist: "
            f"{path}"
        )

    core = (
        dbcore
        if dbcore is not None
        else imports.dbCore
    )

    set_filename = _configure_function(
        core.asSetFilename,
        [
            ctypes.c_char_p,
        ],
    )

    status = set_filename(
        os.fsencode(path)
    )

    if int(status) != 0:
        raise AccessSecurityConfigurationError(
            "asSetFilename failed with "
            f"status {status}: {path}"
        )

    substitutions = general.get(
        "access_security_substitutions"
    )

    if substitutions not in (
        None,
        "",
        "None",
    ):
        set_substitutions = (
            _configure_function(
                core.asSetSubstitutions,
                [
                    ctypes.c_char_p,
                ],
            )
        )

        status = set_substitutions(
            str(substitutions).encode(
                "utf-8"
            )
        )

        if int(status) != 0:
            raise AccessSecurityConfigurationError(
                "asSetSubstitutions failed "
                f"with status {status}"
            )

    return str(path)
