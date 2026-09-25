"""
Genesis Phase-I one-shot fitter publisher -> waveform staging runtime.

Default behavior is dry-run:
- connect only to the loopback fitter publisher
- accept one calibrated fit message
- generate and validate the adaptive waveform
- perform no EPICS write

With --stage:
- the only EPICS write permitted by this module is
  UNH:GEN:SSRF:WGEN:WAVEFORM_CMD
- the process exits after one accepted fit message

This module has no authority for LOAD, OUTPUT, SMC, switches, ARM, START,
or burn execution.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import aioca

from ssrf_analysis.fitter_waveform_bridge import (
    DEFAULT_FIT_PUBLISH_HOST,
    DEFAULT_FIT_PUBLISH_PORT,
    FitWaveformBridge,
    iter_fit_messages,
)

from ssrf_analysis.waveform_stager import (
    WAVEFORM_CMD_PV,
    WaveformStager,
)


class EpicsStageWriteError(RuntimeError):
    """Unsafe or failed EPICS waveform staging request."""


class EpicsWaveformWriter:
    """Narrow EPICS writer that can write WAVEFORM_CMD only."""

    def __init__(
        self,
        *,
        timeout_s: float = 3.0,
    ):
        timeout_s = float(timeout_s)

        if timeout_s <= 0:
            raise ValueError(
                "timeout_s must be positive"
            )

        self.timeout_s = timeout_s

    async def __call__(
        self,
        pv,
        value,
    ):
        if pv != WAVEFORM_CMD_PV:
            raise EpicsStageWriteError(
                "runtime staging write rejected: "
                f"{pv}"
            )

        await aioca.caput(
            WAVEFORM_CMD_PV,
            value,
            wait=True,
            timeout=self.timeout_s,
        )


class DryRunWriter:
    """No-op writer used by the default dry-run mode."""

    def __init__(self):
        self.calls = 0

    async def __call__(
        self,
        pv,
        value,
    ):
        if pv != WAVEFORM_CMD_PV:
            raise EpicsStageWriteError(
                "dry-run staging target rejected: "
                f"{pv}"
            )

        self.calls += 1


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Consume one loopback fitter result and "
            "generate/stage one adaptive ssRF waveform."
        )
    )

    parser.add_argument(
        "--host",
        default=DEFAULT_FIT_PUBLISH_HOST,
    )

    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_FIT_PUBLISH_PORT,
    )

    parser.add_argument(
        "--case",
        choices=[
            "Enhancement",
            "Suppression",
            "Zero",
        ],
        required=True,
    )

    parser.add_argument(
        "--polarization",
        type=float,
        default=0.30,
    )

    parser.add_argument(
        "--high-polarization",
        type=float,
        default=0.30,
    )

    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=10.0,
    )

    parser.add_argument(
        "--epics-timeout",
        type=float,
        default=3.0,
    )

    parser.add_argument(
        "--stage",
        action="store_true",
        help=(
            "Stage WAVEFORM_CMD to EPICS. "
            "Without this flag, no EPICS write occurs."
        ),
    )

    return parser


def make_summary(
    *,
    result,
    mode,
):
    return {
        "mode": mode,
        "pv": result.receipt.pv,
        "points": result.receipt.points,
        "minimum": result.receipt.minimum,
        "maximum": result.receipt.maximum,
        "material": result.plan.material,
        "case": result.plan.case,
        "fit_ts": result.plan.fit_ts,
        "fit_type": result.plan.fit_type,
        "used_measured_heights": (
            result.plan.used_measured_heights
        ),
        "scaling_verified_for_hardware": (
            result.plan
            .scaling_verified_for_hardware
        ),
    }


def run_once(args):
    if args.stage:
        writer = EpicsWaveformWriter(
            timeout_s=args.epics_timeout,
        )
        mode = "stage"
    else:
        writer = DryRunWriter()
        mode = "dry-run"

    bridge = FitWaveformBridge(
        stager=WaveformStager(
            write=writer
        ),
        case=args.case,
        polarization=args.polarization,
        high_polarization=(
            args.high_polarization
        ),
    )

    messages = iter_fit_messages(
        host=args.host,
        port=args.port,
        connect_timeout_s=(
            args.connect_timeout
        ),
    )

    try:
        message = next(messages)
    except StopIteration as exc:
        raise RuntimeError(
            "fitter publisher closed before "
            "sending a fit message"
        ) from exc
    finally:
        close = getattr(
            messages,
            "close",
            None,
        )

    result = asyncio.run(
        bridge.process_message(
            message
        )
    )

    if close is not None:
        close()

    summary = make_summary(
        result=result,
        mode=mode,
    )

    print(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
    )

    return result


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    run_once(args)


if __name__ == "__main__":
    main()
