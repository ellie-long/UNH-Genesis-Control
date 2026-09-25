"""
Genesis Phase-I fitter-to-waveform bridge.

Consumes fit-result messages from the existing loopback-only online fitter
publisher, converts each calibrated fit to a validated FitSnapshot,
generates a WaveformPlan, and passes that plan to the safe WaveformStager.

This module does NOT:
- issue LOAD_CMD
- modify WaveGen scalar hardware settings
- enable WaveGen output
- operate the SMC100A
- operate RF/NMR switches
- ARM or START a burn
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import socket
from typing import Any, Mapping

from .waveform_planner import (
    FitSnapshot,
    WaveformPlan,
    build_waveform_plan,
)


DEFAULT_FIT_PUBLISH_HOST = "127.0.0.1"
DEFAULT_FIT_PUBLISH_PORT = 8778

_LOOPBACK_HOSTS = frozenset(
    {
        "127.0.0.1",
        "::1",
        "localhost",
    }
)


class FitBridgeError(RuntimeError):
    """Invalid or unusable fitter-publisher message."""


def validate_loopback_host(host: str) -> str:
    host = str(host).strip()

    if host not in _LOOPBACK_HOSTS:
        raise ValueError(
            "fit publisher must be loopback-only; "
            f"got {host!r}"
        )

    return host


def decode_fit_line(
    raw_line: bytes | str,
) -> Mapping[str, Any]:
    """Decode and minimally validate one NDJSON fit-publisher line."""

    if isinstance(raw_line, bytes):
        text = raw_line.decode("utf-8")
    else:
        text = str(raw_line)

    text = text.strip()

    if not text:
        raise FitBridgeError(
            "empty fit-publisher line"
        )

    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FitBridgeError(
            "invalid fit-publisher JSON"
        ) from exc

    if not isinstance(message, dict):
        raise FitBridgeError(
            "fit-publisher message must be a JSON object"
        )

    if message.get("type") != "fit":
        raise FitBridgeError(
            "unsupported publisher message type "
            f"{message.get('type')!r}"
        )

    return message


@dataclass(frozen=True)
class BridgeResult:
    """Result of one successful fit -> plan -> stage operation."""

    snapshot: FitSnapshot
    plan: WaveformPlan
    receipt: Any


class FitWaveformBridge:
    """Pure orchestration boundary from fitter message to safe stager."""

    def __init__(
        self,
        *,
        stager,
        case: str,
        polarization: float,
        high_polarization: float,
    ):
        if not hasattr(stager, "stage"):
            raise TypeError(
                "stager must provide stage(plan)"
            )

        self.stager = stager
        self.case = str(case)
        self.polarization = float(polarization)
        self.high_polarization = float(
            high_polarization
        )

    async def process_message(
        self,
        message: Mapping[str, Any],
    ) -> BridgeResult:
        """Validate one fit message, create a plan, and stage it."""

        if not isinstance(message, Mapping):
            raise FitBridgeError(
                "fit message must be a mapping"
            )

        if message.get("type") != "fit":
            raise FitBridgeError(
                "only type='fit' messages are accepted"
            )

        snapshot = FitSnapshot.from_message(
            message
        )

        plan = build_waveform_plan(
            snapshot,
            case=self.case,
            polarization=self.polarization,
            high_polarization=self.high_polarization,
        )

        receipt = await self.stager.stage(
            plan
        )

        return BridgeResult(
            snapshot=snapshot,
            plan=plan,
            receipt=receipt,
        )


def iter_fit_messages(
    *,
    host: str = DEFAULT_FIT_PUBLISH_HOST,
    port: int = DEFAULT_FIT_PUBLISH_PORT,
    connect_timeout_s: float = 10.0,
):
    """
    Yield decoded fit messages from the existing NDJSON publisher.

    The subscriber connection is deliberately restricted to loopback.
    """

    host = validate_loopback_host(host)
    port = int(port)

    if not 1 <= port <= 65535:
        raise ValueError(
            "publisher port must be between 1 and 65535"
        )

    timeout = float(connect_timeout_s)

    if timeout <= 0:
        raise ValueError(
            "connect_timeout_s must be positive"
        )

    with socket.create_connection(
        (host, port),
        timeout=timeout,
    ) as sock:
        reader = sock.makefile("rb")

        for raw_line in reader:
            raw_line = raw_line.strip()

            if not raw_line:
                continue

            yield decode_fit_line(
                raw_line
            )


async def process_one_message(
    bridge: FitWaveformBridge,
    message: Mapping[str, Any],
) -> BridgeResult:
    return await bridge.process_message(
        message
    )


def process_one_message_sync(
    bridge: FitWaveformBridge,
    message: Mapping[str, Any],
) -> BridgeResult:
    return asyncio.run(
        process_one_message(
            bridge,
            message,
        )
    )
