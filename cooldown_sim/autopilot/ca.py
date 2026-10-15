"""Restricted Channel Access writes for the autopilot."""

from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

ALLOW_FILE = Path(__file__).with_name("operator_pvs.yaml")


class RefusedWrite(PermissionError):
    pass


class OperatorAllowlist:
    def __init__(self, path: Path = ALLOW_FILE):
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        self.allow = list(data.get("allow", []))
        self.deny = list(data.get("deny", []))

    def permits(self, pv: str) -> bool:
        # an exact (non-wildcard) allow entry is a deliberate, reviewed exception
        if pv in self.allow:
            return True
        if any(fnmatch.fnmatchcase(pv, pat) for pat in self.deny):
            return False
        return any(fnmatch.fnmatchcase(pv, pat) for pat in self.allow)


class RestrictedCA:
    """caput limited to operator command PVs; every attempt is logged."""

    def __init__(self, events=None, clock=None, allowlist: OperatorAllowlist | None = None):
        self.allowlist = allowlist or OperatorAllowlist()
        self.events = events
        self.clock = clock

    def _log(self, kind: str, detail: str) -> None:
        if self.events is not None and self.clock is not None:
            self.events.write(self.clock.now(), kind, detail)

    async def caput(self, pv: str, value, *, timeout: float = 5.0, wait: bool = True):
        if not self.allowlist.permits(pv):
            self._log("autopilot-refused", f"{pv} <- {value!r}")
            raise RefusedWrite(f"autopilot may not write {pv}")
        from aioca import caput
        await caput(pv, value, timeout=timeout, wait=wait)
        self._log("autopilot", f"{pv} <- {value!r}")
