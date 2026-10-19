"""
Genesis Phase-I one-shot fitter publisher -> waveform staging runtime.

Default behavior is dry-run:
- connect only to the loopback fitter publisher
- accept one calibrated fit message
- generate and validate the adaptive waveform
- perform no EPICS write

With --stage:
- the only EPICS write permitted by this module is the
  sequencer staging-request PV
- the analysis process never writes WaveGen WAVEFORM_CMD directly
- the process exits after one accepted fit message

Structured event logging is enabled by default and writes append-only JSONL
metadata under archive/events/ssrf.

Scientific session-data persistence is also enabled by default and writes
numeric NPZ products under archive/sessions/ssrf. Event records reference
those files rather than embedding large NMR/waveform arrays.

Neither logging layer has EPICS authority.

This module has no authority for LOAD, OUTPUT, SMC, switches, ARM, START,
or burn execution.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

import aioca

from archive.data_paths import ssrf_session_dir
from ssrf_analysis.event_logger import (
    DEFAULT_EVENT_DIR,
    JsonlEventLogger,
    new_session_id,
)
from ssrf_analysis.session_data import (
    DEFAULT_SESSION_DATA_ROOT,
    SessionDataWriter,
)
from ssrf_analysis.fitter_waveform_bridge import (
    DEFAULT_FIT_PUBLISH_HOST,
    DEFAULT_FIT_PUBLISH_PORT,
    FitWaveformBridge,
    iter_fit_messages,
)
from ssrf_analysis.waveform_stager import (
    WAVEFORM_STAGE_REQUEST_PV,
    WaveformStager,
)


class EpicsStageWriteError(RuntimeError):
    """Unsafe or failed EPICS waveform staging request."""


class EpicsWaveformWriter:
    """Submit a waveform only to the sequencer staging request PV."""

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
        if pv != WAVEFORM_STAGE_REQUEST_PV:
            raise EpicsStageWriteError(
                "runtime staging write rejected: "
                f"{pv}"
            )

        await aioca.caput(
            WAVEFORM_STAGE_REQUEST_PV,
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
        if pv != WAVEFORM_STAGE_REQUEST_PV:
            raise EpicsStageWriteError(
                "dry-run staging target rejected: "
                f"{pv}"
            )

        self.calls += 1


def _safe_len(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(len(value))
    except TypeError:
        return None


def _fit_event_data(
    message: Mapping[str, Any],
) -> dict[str, Any]:
    constants = message.get(
        "constants"
    )

    if not isinstance(
        constants,
        Mapping,
    ):
        constants = {}

    return {
        "fit_ts": message.get("ts"),
        "fit_type": message.get("fit_type"),
        "mode": message.get("mode"),
        "material": message.get("material"),
        "model": message.get("model"),
        "npts": message.get("npts"),
        "constants_good": (
            message.get("constants_good")
        ),
        "constants_source": (
            constants.get("source")
        ),
        "omegD_mhz": (
            constants.get("omegD")
        ),
        "r": constants.get("r"),
        "red_chisq": (
            message.get(
                "red_chisq",
                constants.get("red_chisq"),
            )
        ),
        "processed_points": _safe_len(
            message.get("data")
        ),
        "raw_points": _safe_len(
            message.get("raw")
        ),
    }


def _plan_event_data(
    plan,
) -> dict[str, Any]:
    return {
        "material": plan.material,
        "case": plan.case,
        "fit_ts": plan.fit_ts,
        "fit_type": plan.fit_type,
        "omegD_mhz": plan.omegD_mhz,
        "frequency_shift_mhz": (
            plan.frequency_shift_mhz
        ),
        "waveform_points": (
            plan.waveform_points
        ),
        "peak_time_fraction": (
            plan.peak_time_fraction
        ),
        "period_s": plan.period_s,
        "vhigh_v": plan.vhigh_v,
        "vlow_v": plan.vlow_v,
        "used_measured_heights": (
            plan.used_measured_heights
        ),
        "scaling_verified_for_hardware": (
            plan.scaling_verified_for_hardware
        ),
    }


class EventLoggingStager:
    """
    Decorate the validated WaveformStager with metadata-only event records.

    This wrapper does not gain any additional EPICS authority.
    """

    def __init__(
        self,
        *,
        stager,
        logger: JsonlEventLogger | None,
        session_id: str,
        mode: str,
        session_data_writer: SessionDataWriter | None = None,
    ):
        self.stager = stager
        self.logger = logger
        self.session_id = session_id
        self.mode = mode
        self.session_data_writer = session_data_writer
        self.waveform_data_path = None

    def _emit(
        self,
        event_type: str,
        data: Mapping[str, Any],
    ):
        if self.logger is None:
            return None

        return self.logger.append(
            event_type=event_type,
            source="fitter_stage_runtime",
            session_id=self.session_id,
            data=data,
        )

    async def stage(
        self,
        plan,
    ):
        waveform_path = None

        if self.session_data_writer is not None:
            waveform_path = (
                self.session_data_writer
                .write_waveform_data(
                    session_id=self.session_id,
                    plan=plan,
                )
            )

            self.waveform_data_path = str(
                waveform_path
            )

        planned_data = _plan_event_data(
            plan
        )

        planned_data[
            "waveform_data_path"
        ] = (
            str(waveform_path)
            if waveform_path is not None
            else None
        )

        self._emit(
            "waveform_planned",
            planned_data,
        )

        receipt = await self.stager.stage(
            plan
        )

        event_type = (
            "waveform_staged"
            if self.mode == "stage"
            else "dry_run_completed"
        )

        self._emit(
            event_type,
            {
                "pv": receipt.pv,
                "points": receipt.points,
                "minimum": receipt.minimum,
                "maximum": receipt.maximum,
                "material": receipt.material,
                "case": receipt.case,
                "fit_ts": receipt.fit_ts,
                "waveform_data_path": (
                    self.waveform_data_path
                ),
            },
        )

        return receipt


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
        required=True,
        help=(
            "Explicit vector polarization as a fraction; "
            "for example 0.30 for 30 percent."
        ),
    )

    parser.add_argument(
        "--high-polarization",
        type=float,
        required=True,
        help=(
            "Explicit high-polarization reference as a fraction; "
            "for example 0.30 for 30 percent."
        ),
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
            "Submit one waveform to the Genesis sequencer "
            "staging-request PV. The analysis process never writes "
            "WaveGen WAVEFORM_CMD directly. Without this flag, no "
            "EPICS write occurs."
        ),
    )

    parser.add_argument(
        "--event-dir",
        default=str(DEFAULT_EVENT_DIR),
        help=(
            "Directory for append-only ssRF JSONL "
            "event records."
        ),
    )

    parser.add_argument(
        "--session-id",
        default=None,
        help=(
            "Optional explicit correlation/session ID. "
            "Generated automatically when omitted."
        ),
    )

    parser.add_argument(
        "--session-data-root",
        default=str(ssrf_session_dir()),
        help=(
            "Root directory for numeric ssRF session "
            "data products."
        ),
    )

    parser.add_argument(
        "--no-session-data",
        action="store_true",
        help=(
            "Disable NPZ scientific session-data "
            "persistence for this run."
        ),
    )

    parser.add_argument(
        "--no-event-log",
        action="store_true",
        help=(
            "Disable JSONL event logging for this run."
        ),
    )

    return parser


def make_summary(
    *,
    result,
    mode,
    session_id,
    event_logging,
    event_dir,
    session_data_enabled,
    session_data_root,
    fit_data_path,
    waveform_data_path,
):
    return {
        "mode": mode,
        "session_id": session_id,
        "event_logging": bool(
            event_logging
        ),
        "event_dir": (
            str(event_dir)
            if event_logging
            else None
        ),
        "session_data_enabled": bool(
            session_data_enabled
        ),
        "session_data_root": (
            str(session_data_root)
            if session_data_enabled
            else None
        ),
        "fit_data_path": (
            str(fit_data_path)
            if fit_data_path is not None
            else None
        ),
        "waveform_data_path": (
            str(waveform_data_path)
            if waveform_data_path is not None
            else None
        ),
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
    session_id = (
        str(args.session_id).strip()
        if args.session_id is not None
        else new_session_id()
    )

    if not session_id:
        raise ValueError(
            "session_id must not be empty"
        )

    event_logging = (
        not bool(args.no_event_log)
    )

    logger = (
        JsonlEventLogger(
            directory=Path(
                args.event_dir
            )
        )
        if event_logging
        else None
    )

    session_data_enabled = (
        not bool(
            args.no_session_data
        )
    )

    session_data_writer = (
        SessionDataWriter(
            root=Path(
                args.session_data_root
            )
        )
        if session_data_enabled
        else None
    )

    fit_data_path = None

    mode = (
        "stage"
        if args.stage
        else "dry-run"
    )

    def emit(
        event_type,
        data,
        *,
        level="INFO",
    ):
        if logger is None:
            return None

        return logger.append(
            event_type=event_type,
            source="fitter_stage_runtime",
            session_id=session_id,
            level=level,
            data=data,
        )

    emit(
        "session_started",
        {
            "mode": mode,
            "case": args.case,
            "publisher_host": args.host,
            "publisher_port": args.port,
            "polarization": args.polarization,
            "high_polarization": (
                args.high_polarization
            ),
            "session_data_enabled": (
                session_data_enabled
            ),
            "session_data_root": (
                str(args.session_data_root)
                if session_data_enabled
                else None
            ),
        },
    )

    messages = None

    try:
        if args.stage:
            writer = EpicsWaveformWriter(
                timeout_s=args.epics_timeout,
            )
        else:
            writer = DryRunWriter()

        stager = EventLoggingStager(
            stager=WaveformStager(
                write=writer
            ),
            logger=logger,
            session_id=session_id,
            mode=mode,
            session_data_writer=(
                session_data_writer
            ),
        )

        bridge = FitWaveformBridge(
            stager=stager,
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

        if session_data_writer is not None:
            fit_path = (
                session_data_writer
                .write_fit_data(
                    session_id=session_id,
                    message=message,
                )
            )

            fit_data_path = (
                str(fit_path)
                if fit_path is not None
                else None
            )

        fit_event_data = _fit_event_data(
            message
        )

        fit_event_data[
            "fit_data_path"
        ] = fit_data_path

        emit(
            "fit_received",
            fit_event_data,
        )

        result = asyncio.run(
            bridge.process_message(
                message
            )
        )

        emit(
            "session_completed",
            {
                "status": "success",
                "mode": mode,
                "material": (
                    result.plan.material
                ),
                "case": result.plan.case,
                "points": (
                    result.receipt.points
                ),
                "fit_data_path": (
                    fit_data_path
                ),
                "waveform_data_path": (
                    stager.waveform_data_path
                ),
            },
        )

        summary = make_summary(
            result=result,
            mode=mode,
            session_id=session_id,
            event_logging=event_logging,
            event_dir=args.event_dir,
            session_data_enabled=(
                session_data_enabled
            ),
            session_data_root=(
                args.session_data_root
            ),
            fit_data_path=(
                fit_data_path
            ),
            waveform_data_path=(
                stager.waveform_data_path
            ),
        )

        print(
            json.dumps(
                summary,
                indent=2,
                sort_keys=True,
            )
        )

        return result

    except Exception as exc:
        if logger is not None:
            try:
                emit(
                    "session_failed",
                    {
                        "status": "failed",
                        "mode": mode,
                        "error_type": (
                            type(exc).__name__
                        ),
                        "error": str(exc),
                    },
                    level="ERROR",
                )
            except Exception as log_exc:
                raise RuntimeError(
                    "runtime failed and failure "
                    "event could not be written"
                ) from log_exc

        raise

    finally:
        if messages is not None:
            close = getattr(
                messages,
                "close",
                None,
            )

            if close is not None:
                close()


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    run_once(args)


if __name__ == "__main__":
    main()
