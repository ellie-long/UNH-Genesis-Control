"""Validators for a finished run directory.

Each check returns :class:`Check` results with a verdict:

    PASS     behaved as required
    FAIL     did not
    KNOWN    matches a live-Genesis behaviour listed in known_issues.yaml
    SKIPPED  not applicable on this platform / leg not run (with reason)
    INFO     recorded for the operator, no verdict
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERDICTS = ("PASS", "FAIL", "KNOWN", "SKIPPED", "INFO")


@dataclass
class Check:
    name: str
    verdict: str
    summary: str
    details: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.verdict not in VERDICTS:
            raise ValueError(self.verdict)
