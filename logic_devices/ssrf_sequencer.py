"""
Genesis Phase-I ssRF burn sequencer.

The normal Genesis profile remains read-only.  The dedicated
localhost simulator commissioning profile may expose guarded
START/ABORT controls and execute the frozen burn plan through
the narrow EpicsIO boundary.
"""

from __future__ import annotations

import asyncio

import aioca
from softioc import builder

from logic_devices.ssrf_sequence_core import (
    BurnParameters,
    BurnReadinessInputs,
    evaluate_burn_readiness,
    validate_parameters,
)


from logic_devices.ssrf_epics_io import EpicsIO
from logic_devices.ssrf_sequence_executor import (
    SequenceExecutor,
)
from logic_devices.ssrf_sequence_plan import (
    build_burn_plan,
)

STATE_UNKNOWN = "UNKNOWN"
STATE_INVALID = "INVALID_PARAMETERS"
STATE_BLOCKED = "BLOCKED"
STATE_READY = "READY"

EXEC_STATE_DISABLED = "DISABLED"
EXEC_STATE_IDLE = "IDLE"
EXEC_STATE_RUNNING = "RUNNING"
EXEC_STATE_COMPLETE = "COMPLETE"
EXEC_STATE_FAILED = "FAILED"
EXEC_STATE_ABORTED = "ABORTED"


class Device:
    """Read-only Genesis ssRF sequencer burn-readiness IOC."""

    def __init__(
        self,
        device_name,
        settings,
    ):
        self.device_name = device_name
        self.settings = settings

        self.pvs = {}

        # Execution remains disabled by default.  Only the dedicated
        # localhost simulator profile currently sets execution_enabled.
        self._execution_io = EpicsIO(
            execution_enabled=self.settings.get(
                "execution_enabled",
                False,
            ),
        )

        self._start_requested = False
        self._abort_requested = False
        self._execution_task = None

        self._executor = SequenceExecutor(
            read=self._execution_io.read,
            write=self._execution_io.write,
            sleep=self._execution_io.sleep,
            readiness_check=self._executor_readiness,
            abort_requested=self._executor_abort_requested,
        )

        self._create_pvs()

    # ------------------------------------------------------------
    # PV construction
    # ------------------------------------------------------------

    def _create_pvs(self):
        # User/ML supplied burn parameters.
        # These modify IOC memory only at this stage.
        self.pvs["CARRIER_MHZ"] = builder.aOut(
            "CARRIER_MHZ",
            initial_value=float(
                self.settings.get(
                    "carrier_mhz",
                    33.0,
                )
            ),
            EGU="MHz",
            PREC=6,
        )

        self.pvs["START_LEVEL_DBM"] = builder.aOut(
            "START_LEVEL_DBM",
            initial_value=float(
                self.settings.get(
                    "start_level_dbm",
                    -120.0,
                )
            ),
            EGU="dBm",
            PREC=2,
        )

        self.pvs["TARGET_LEVEL_DBM"] = builder.aOut(
            "TARGET_LEVEL_DBM",
            initial_value=float(
                self.settings.get(
                    "target_level_dbm",
                    -22.0,
                )
            ),
            EGU="dBm",
            PREC=2,
        )

        self.pvs["FM_DEV_KHZ"] = builder.aOut(
            "FM_DEV_KHZ",
            initial_value=float(
                self.settings.get(
                    "fm_deviation_khz",
                    10.0,
                )
            ),
            EGU="kHz",
            PREC=3,
        )

        self.pvs["FM_PERIOD_S"] = builder.aOut(
            "FM_PERIOD_S",
            initial_value=float(
                self.settings.get(
                    "fm_period_s",
                    0.010,
                )
            ),
            EGU="s",
            PREC=6,
        )

        self.pvs["VHIGH_V"] = builder.aOut(
            "VHIGH_V",
            initial_value=float(
                self.settings.get(
                    "vhigh_v",
                    1.0,
                )
            ),
            EGU="V",
            PREC=4,
        )

        self.pvs["VLOW_V"] = builder.aOut(
            "VLOW_V",
            initial_value=float(
                self.settings.get(
                    "vlow_v",
                    -1.0,
                )
            ),
            EGU="V",
            PREC=4,
        )

        self.pvs["FM_REPEATS"] = builder.longOut(
            "FM_REPEATS",
            initial_value=int(
                self.settings.get(
                    "fm_repeats",
                    10,
                )
            ),
            LOPR=1,
            HOPR=100000,
        )

        # Derived sequence parameters.
        self.pvs["SWEEP_STEP_DB"] = builder.aIn(
            "SWEEP_STEP_DB",
            EGU="dB",
            PREC=3,
        )

        self.pvs["SWEEP_DWELL_MS"] = builder.aIn(
            "SWEEP_DWELL_MS",
            EGU="ms",
            PREC=3,
        )

        # Burn readiness result.
        self.pvs["PARAMETERS_OK"] = builder.boolIn(
            "PARAMETERS_OK",
            ZNAM="Invalid",
            ONAM="Valid",
        )

        self.pvs["READY"] = builder.boolIn(
            "READY",
            ZNAM="Blocked",
            ONAM="Ready",
        )

        self.pvs["STATE"] = builder.stringIn(
            "STATE"
        )

        self.pvs["REASON"] = builder.stringIn(
            "REASON"
        )

        # ------------------------------------------------------------
        # Active-execution status.
        #
        # START / ABORT records exist only when execution was explicitly
        # enabled at process startup.  The normal Genesis profile therefore
        # has no operator execution controls at all.
        # ------------------------------------------------------------

        self.pvs["EXECUTION_ENABLED"] = builder.boolIn(
            "EXECUTION_ENABLED",
            ZNAM="Disabled",
            ONAM="Enabled",
        )

        self.pvs["EXECUTION_STATE"] = builder.stringIn(
            "EXECUTION_STATE"
        )

        self.pvs["EXECUTION_REASON"] = builder.stringIn(
            "EXECUTION_REASON"
        )

        execution_enabled = (
            self._execution_io.execution_enabled
        )

        if execution_enabled:
            self.pvs["START_CMD"] = builder.boolOut(
                "START_CMD",
                ZNAM="IDLE",
                ONAM="START",
                always_update=True,
                on_update_name=self._execution_command,
            )

            self.pvs["ABORT_CMD"] = builder.boolOut(
                "ABORT_CMD",
                ZNAM="IDLE",
                ONAM="ABORT",
                always_update=True,
                on_update_name=self._execution_command,
            )

            self.pvs["START_CMD"].set(
                0,
                process=False,
            )
            self.pvs["ABORT_CMD"].set(
                0,
                process=False,
            )

        self.pvs["EXECUTION_ENABLED"].set(
            int(execution_enabled)
        )

        self._set_execution_status(
            EXEC_STATE_IDLE
            if execution_enabled
            else EXEC_STATE_DISABLED,
            "Simulator execution idle"
            if execution_enabled
            else "Inactive: execution disabled",
        )

        self.pvs["PARAMETERS_OK"].set(0)
        self.pvs["READY"].set(0)
        self.pvs["STATE"].set(
            STATE_UNKNOWN
        )
        self.pvs["REASON"].set(
            "not yet evaluated"
        )

    # ------------------------------------------------------------
    # master_ioc compatibility
    # ------------------------------------------------------------

    def connect(self):
        """Logic IOC: no physical device connection is required."""
        return True

    # ------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------

    def _set_execution_status(
        self,
        state,
        reason,
    ):
        """Publish bounded EPICS execution status strings."""

        self.pvs["EXECUTION_STATE"].set(
            str(state)[:39]
        )

        self.pvs["EXECUTION_REASON"].set(
            str(reason)[:39]
        )

    def _reset_momentary_command(
        self,
        name,
    ):
        if name in self.pvs:
            self.pvs[name].set(
                0,
                process=False,
            )

    def _execution_command(
        self,
        new_value,
        pv,
    ):
        """Latch START / ABORT requests without doing EPICS I/O here."""

        pv_name = pv.removeprefix(
            self.device_name + ":"
        )

        if pv_name not in (
            "START_CMD",
            "ABORT_CMD",
        ):
            return

        # Commands are momentary regardless of acceptance.
        self._reset_momentary_command(
            pv_name
        )

        if not bool(new_value):
            return

        if not self._execution_io.execution_enabled:
            self._set_execution_status(
                EXEC_STATE_DISABLED,
                "Execution disabled",
            )
            return

        running = (
            self._execution_task is not None
            and not self._execution_task.done()
        )

        if pv_name == "START_CMD":
            if running or self._start_requested:
                self._set_execution_status(
                    EXEC_STATE_RUNNING,
                    "Sequence already running",
                )
                return

            # Operator START is accepted only from the most recently
            # published READY state.  The executor performs another
            # independent live readiness check immediately before writes.
            if not bool(
                self.pvs["READY"].get()
            ):
                self._set_execution_status(
                    EXEC_STATE_IDLE,
                    "Start blocked: burn not ready",
                )
                return

            self._abort_requested = False
            self._start_requested = True

            self._set_execution_status(
                EXEC_STATE_IDLE,
                "Start requested",
            )
            return

        # ABORT_CMD
        if running or self._start_requested:
            self._abort_requested = True

            self._set_execution_status(
                EXEC_STATE_RUNNING,
                "Abort requested",
            )
        else:
            self._set_execution_status(
                EXEC_STATE_IDLE,
                "No sequence running",
            )

    def _service_execution_request(self):
        """Start one background execution task from the IOC async loop."""

        if not self._start_requested:
            return

        if (
            self._execution_task is not None
            and not self._execution_task.done()
        ):
            return

        self._execution_task = asyncio.create_task(
            self._run_requested_sequence()
        )

    async def _run_requested_sequence(self):
        """Build and execute one frozen burn plan."""

        self._start_requested = False

        try:
            # An abort arriving after START but before the async task begins
            # must cause no writes.
            if self._abort_requested:
                self._set_execution_status(
                    EXEC_STATE_ABORTED,
                    "Aborted before start",
                )
                return

            ready, reason = (
                await self._executor_readiness()
            )

            if not ready:
                self._set_execution_status(
                    EXEC_STATE_IDLE,
                    "Start blocked: "
                    + str(reason),
                )
                return

            parameters = self._parameters()

            waveform_points = int(
                await self._execution_io.read(
                    "UNH:GEN:SSRF:WGEN:"
                    "WAVEFORM_POINTS"
                )
            )

            waveform_id_before = int(
                await self._execution_io.read(
                    "UNH:GEN:SSRF:WGEN:"
                    "WAVEFORM_ID"
                )
            )

            plan = build_burn_plan(
                parameters,
                waveform_points,
                waveform_id_before,
            )

            self._set_execution_status(
                EXEC_STATE_RUNNING,
                "Burn sequence running",
            )

            result = await self._executor.run(
                plan
            )

            final_reason = (
                result.reason
                if result.reason
                else (
                    "Burn sequence complete"
                    if result.ok
                    else "Burn sequence failed"
                )
            )

            self._set_execution_status(
                result.state,
                final_reason,
            )

        except Exception as exc:
            self._set_execution_status(
                EXEC_STATE_FAILED,
                str(exc),
            )

        finally:
            self._abort_requested = False
            self._execution_task = None

    # ------------------------------------------------------------
    # Parameter handling
    # ------------------------------------------------------------

    def _parameters(self):
        return BurnParameters(
            carrier_mhz=float(
                self.pvs[
                    "CARRIER_MHZ"
                ].get()
            ),
            start_level_dbm=float(
                self.pvs[
                    "START_LEVEL_DBM"
                ].get()
            ),
            target_level_dbm=float(
                self.pvs[
                    "TARGET_LEVEL_DBM"
                ].get()
            ),
            fm_deviation_khz=float(
                self.pvs[
                    "FM_DEV_KHZ"
                ].get()
            ),
            fm_period_s=float(
                self.pvs[
                    "FM_PERIOD_S"
                ].get()
            ),
            vhigh_v=float(
                self.pvs[
                    "VHIGH_V"
                ].get()
            ),
            vlow_v=float(
                self.pvs[
                    "VLOW_V"
                ].get()
            ),
            fm_repeats=int(
                self.pvs[
                    "FM_REPEATS"
                ].get()
            ),
        )

    def _publish_parameters(
        self,
        parameters,
    ):
        self.pvs[
            "SWEEP_STEP_DB"
        ].set(
            parameters.sweep_step_db
        )

        self.pvs[
            "SWEEP_DWELL_MS"
        ].set(
            parameters.sweep_dwell_ms
        )

    # ------------------------------------------------------------
    # EPICS reads
    # ------------------------------------------------------------

    async def _executor_readiness(self):
        """Evaluate live burn readiness for the future executor."""

        inputs = await self._read_burn_readiness_inputs()

        decision = evaluate_burn_readiness(
            inputs
        )

        return (
            bool(decision.ready),
            decision.reason,
        )

    async def _executor_abort_requested(self):
        """Return the internal abort flag.

        No operator ABORT control exists in 5B.6C.
        """

        return bool(
            self._abort_requested
        )

    async def _read_int(
        self,
        pv,
    ):
        value = await aioca.caget(
            pv,
            timeout=2.0,
        )

        return int(value)

    async def _read_burn_readiness_inputs(self):
        names = {
            "smc_comm":
                "UNH:GEN:SSRF:GEN:COMM",
            "smc_write_enabled":
                "UNH:GEN:SSRF:GEN:WRITE_ENABLED",
            "smc_rf":
                "UNH:GEN:SSRF:GEN:RF_RBV",
            "smc_fm":
                "UNH:GEN:SSRF:GEN:FM_RBV",
            "smc_sweep_arm":
                "UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV",

            "wavegen_comm":
                "UNH:GEN:SSRF:WGEN:COMM",
            "wavegen_write_enabled":
                "UNH:GEN:SSRF:WGEN:WRITE_ENABLED",
            "wavegen_output":
                "UNH:GEN:SSRF:WGEN:OUTPUT_RBV",
            "waveform_points":
                "UNH:GEN:SSRF:WGEN:WAVEFORM_POINTS",

            "nmr_switch_comm":
                "UNH:GEN:SSRF:NMR_SW:COMM",
            "nmr_switch_write_enabled":
                "UNH:GEN:SSRF:NMR_SW:WRITE_ENABLED",
            "nmr_mask":
                "UNH:GEN:SSRF:NMR_SW:MASK_RBV",

            "ssrf_switch_comm":
                "UNH:GEN:SSRF:RF_SW:COMM",
            "ssrf_switch_write_enabled":
                "UNH:GEN:SSRF:RF_SW:WRITE_ENABLED",
            "ssrf_mask":
                "UNH:GEN:SSRF:RF_SW:MASK_RBV",

            "interlock_inputs_ok":
                "UNH:GEN:SSRF:ILK:INPUTS_OK",
            "interlock_ok":
                "UNH:GEN:SSRF:ILK:INTERLOCK_OK",
        }

        keys = list(names)

        values = await asyncio.gather(
            *[
                self._read_int(
                    names[key]
                )
                for key in keys
            ]
        )

        return BurnReadinessInputs(
            **dict(
                zip(
                    keys,
                    values,
                )
            )
        )

    # ------------------------------------------------------------
    # IOC polling loop
    # ------------------------------------------------------------

    async def do_reads(self):
        # START callbacks only latch a request.  Actual async execution
        # begins here on the IOC dispatcher event loop.
        self._service_execution_request()

        # Parameter validation is entirely local.
        parameters = self._parameters()

        self._publish_parameters(
            parameters
        )

        parameters_ok, reason = (
            validate_parameters(
                parameters
            )
        )

        self.pvs[
            "PARAMETERS_OK"
        ].set(
            int(parameters_ok)
        )

        if not parameters_ok:
            self.pvs["READY"].set(0)
            self.pvs["STATE"].set(
                STATE_INVALID
            )
            self.pvs["REASON"].set(
                str(reason)[:39]
            )

            return True

        # Live readiness fails closed if any source PV
        # cannot be read.
        try:
            inputs = (
                await self._read_burn_readiness_inputs()
            )

        except Exception as exc:
            self.pvs["READY"].set(0)
            self.pvs["STATE"].set(
                STATE_UNKNOWN
            )
            self.pvs["REASON"].set(
                (
                    "EPICS input unavailable: "
                    + str(exc)
                )[:39]
            )

            return True

        decision = evaluate_burn_readiness(
            inputs
        )

        self.pvs["READY"].set(
            int(decision.ready)
        )

        self.pvs["STATE"].set(
            STATE_READY
            if decision.ready
            else STATE_BLOCKED
        )

        self.pvs["REASON"].set(
            str(decision.reason)[:39]
        )

        return True
