"""
Genesis Phase-I ssRF burn sequencer.

Execution is disabled by default.  Authorized profiles may expose guarded
PREPARE/ARM/START/ABORT controls and execute only through the narrow EpicsIO
boundary.  The final physical profile requires a verified source-preparation
phase before one-shot ARM + BURN.
"""

from __future__ import annotations

import asyncio
from logic_devices.ssrf_manual_rf import ManualRF
from logic_devices.nmr_source_ipc_transaction import (
    execute_nmr_source_change_via_ipc,
    validate_ipc_configuration,
)
from logic_devices.nmr_source_recovery import NMRSourceRecovery
from logic_devices.nmr_source_ipc_client import request_source_snapshot
from logic_devices.nmr_source_readbacks import (
    read_fitter_source_snapshot, verify_ssrf_rf_off_fresh,
)
from logic_devices.ssrf_lifecycle import OwnershipLock, startup_lifecycle_held

import aioca
from softioc import builder

from logic_devices.ssrf_sequence_core import (
    BurnParameters,
    BurnReadinessInputs,
    evaluate_burn_readiness,
    validate_parameters,
)


from genesis_common.execution_authorization import (
    validate_execution_authorization,
)
from genesis_common.ssrf_waveform_contract import (
    WAVEFORM_POINTS,
    WAVEGEN_WAVEFORM_CMD_PV,
    WAVEGEN_WAVEFORM_POINTS_PV,
    WAVEGEN_WAVEFORM_STAGE_ID_PV,
    validate_normalized_waveform,
)

from logic_devices.ssrf_epics_io import EpicsIO
from logic_devices.ssrf_sequence_executor import (
    SequenceExecutor,
)
from logic_devices.ssrf_sequence_plan import (
    build_burn_plan,
    build_prepare_cleanup_plan,
    build_prepare_plan,
    build_prepared_burn_plan,
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
    """Genesis ssRF readiness, preparation, and one-shot burn sequencer."""

    def __init__(
        self,
        device_name,
        settings,
    ):
        self.device_name = device_name
        self.settings = settings

        self.pvs = {}

        # Internal reservation for coordinated NMR software-source changes.
        # No external command can acquire it until authorization
        # and release handling are implemented and tested.
        self._nmr_source_change_reserved = False
        self._nmr_source_request = None
        self._nmr_source_task = None
        self._nmr_recovery_required = (
            settings.get("nmr_source_selection_enabled", False) is True
            or bool(settings.get("nmr_source_recovery_path"))
        )
        self._nmr_recovery = None
        self._nmr_recovery_error = None
        self._nmr_recovery_attempted = False
        if self._nmr_recovery_required:
            # Journal I/O failure inhibits source/burn authorization but must
            # not prevent ABORT and safe-OFF controls from coming online.
            try:
                self._nmr_recovery = NMRSourceRecovery(
                    settings.get("nmr_source_recovery_path"),
                    fitter_uid=settings.get("nmr_source_control_fitter_uid"),
                    fitter_pid=settings.get("nmr_source_control_fitter_pid"),
                )
            except Exception as exc:
                self._nmr_recovery_error = str(exc)

        # Execution remains disabled by default.  Only an explicitly
        # authorized simulator or physical profile may enable it.
        execution_enabled = bool(
            self.settings.get(
                "execution_enabled",
                False,
            )
        )

        self._execution_mode = (
            validate_execution_authorization(
                execution_enabled=execution_enabled,
                execution_mode=self.settings.get(
                    "execution_mode"
                ),
                required_os_users=self.settings.get(
                    "required_os_users"
                ),
            )
        )

        # The normal physical sequencer holds command ownership for its lifetime.
        # Simulator profiles never take physical locks.
        self._control_lock = None
        if self._execution_mode == "physical":
            directory = self.settings.get("ownership_lock_dir")
            if startup_lifecycle_held(directory):
                # Started by the lifecycle wrapper, whose parent holds the lock
                # until startup checks finish. Only control ownership is needed.
                self._control_lock = OwnershipLock("control", directory)
            else:
                # Independently launched sequencers must exclude an entire STOP,
                # including the interval between verification and owner teardown.
                with OwnershipLock("lifecycle", directory):
                    self._control_lock = OwnershipLock("control", directory)

        self._execution_io = EpicsIO(
            execution_enabled=execution_enabled,
        )

        self._manual = ManualRF(self._execution_io, self._executor_abort_requested)
        self._manual_request = None
        self._manual_task = None
        self._safe_off_requested = False
        self._safe_off_id = 0
        self._start_requested = False
        self._abort_requested = False
        self._execution_task = None

        # Final physical workflow separates source preparation/load from the
        # one-shot ARM + BURN authorization.  Simulator/legacy profiles may
        # leave require_prepared false for backwards-compatible regression.
        self._require_prepared = bool(
            self.settings.get("require_prepared", False)
        )
        self._prepare_requested = False
        self._prepare_task = None
        self._prepared = False
        self._prepared_waveform_id = 0
        self._prepared_signature = None

        # Analysis -> sequencer -> protected WaveGen staging boundary.
        #
        # A request callback validates/copies data only.  EPICS forwarding
        # occurs later on the IOC asyncio dispatcher through EpicsIO.
        self._waveform_stage_values = None
        self._waveform_stage_requested = False
        self._waveform_stage_task = None
        self._waveform_stage_id = 0

        # Runtime execution authorization is one-shot.
        #
        # It is deliberately independent of:
        #   - static execution_enabled
        #   - device WRITE_ENABLED
        #   - hardware/interlock readiness
        #
        # IOC startup always begins DISARMED.
        self._execution_armed = False

        self._executor = SequenceExecutor(
            read=self._execution_io.read_safety,
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
        # Manual RF and user/ML supplied burn parameters.
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

        self.pvs["MANUAL_LEVEL_DBM"] = builder.aOut(
            "MANUAL_LEVEL_DBM",
            initial_value=float(self.settings.get("manual_level_dbm", -120.0)),
            EGU="dBm",
            PREC=2,
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

        # ------------------------------------------------------------
        # Authorized waveform-staging request interface.
        #
        # WAVEFORM_STAGE_REQ is NOT a hardware/device command PV.
        # It only submits normalized samples to this sequencer.
        #
        # The sequencer performs a second independent validation before
        # forwarding to the protected WaveGen WAVEFORM_CMD via EpicsIO.
        # ------------------------------------------------------------

        self.pvs["WAVEFORM_STAGE_ENABLED"] = builder.boolIn(
            "WAVEFORM_STAGE_ENABLED",
            ZNAM="Disabled",
            ONAM="Enabled",
        )

        self.pvs["WAVEFORM_STAGE_REQ"] = builder.WaveformOut(
            "WAVEFORM_STAGE_REQ",
            length=WAVEFORM_POINTS,
            datatype="float32",
            always_update=True,
            on_update_name=self._waveform_stage_command,
        )

        self.pvs["WAVEFORM_STAGE_POINTS"] = builder.longIn(
            "WAVEFORM_STAGE_POINTS",
            LOPR=0,
            HOPR=WAVEFORM_POINTS,
        )

        self.pvs["WAVEFORM_STAGE_ID"] = builder.longIn(
            "WAVEFORM_STAGE_ID",
            LOPR=0,
        )

        self.pvs["WAVEFORM_STAGE_STATE"] = builder.stringIn(
            "WAVEFORM_STAGE_STATE"
        )

        self.pvs["WAVEFORM_STAGE_REASON"] = builder.stringIn(
            "WAVEFORM_STAGE_REASON"
        )

        self.pvs["EXECUTION_ENABLED"] = builder.boolIn(
            "EXECUTION_ENABLED",
            ZNAM="Disabled",
            ONAM="Enabled",
        )

        self.pvs["EXECUTION_ARMED"] = builder.boolIn(
            "EXECUTION_ARMED",
            ZNAM="Disarmed",
            ONAM="Armed",
        )

        self.pvs["EXECUTION_STATE"] = builder.stringIn(
            "EXECUTION_STATE"
        )

        self.pvs["EXECUTION_REASON"] = builder.stringIn(
            "EXECUTION_REASON"
        )

        self.pvs["PREPARED"] = builder.boolIn(
            "PREPARED",
            ZNAM="Not prepared",
            ONAM="Prepared",
        )

        self.pvs["PREPARED_WAVEFORM_ID"] = builder.longIn(
            "PREPARED_WAVEFORM_ID",
            LOPR=0,
        )

        self.pvs["PREPARE_STATE"] = builder.stringIn(
            "PREPARE_STATE"
        )

        self.pvs["PREPARE_REASON"] = builder.stringIn(
            "PREPARE_REASON"
        )

        execution_enabled = (
            self._execution_io.execution_enabled
        )

        if execution_enabled:
            self.pvs["PREPARE_CMD"] = builder.boolOut(
                "PREPARE_CMD",
                ZNAM="IDLE",
                ONAM="PREPARE",
                always_update=True,
                on_update_name=self._prepare_command,
            )

            self.pvs["ARM_CMD"] = builder.boolOut(
                "ARM_CMD",
                ZNAM="IDLE",
                ONAM="ARM",
                always_update=True,
                on_update_name=self._execution_command,
            )

            self.pvs["DISARM_CMD"] = builder.boolOut(
                "DISARM_CMD",
                ZNAM="IDLE",
                ONAM="DISARM",
                always_update=True,
                on_update_name=self._execution_command,
            )

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

            self.pvs["PREPARE_CMD"].set(
                0,
                process=False,
            )
            self.pvs["ARM_CMD"].set(
                0,
                process=False,
            )
            self.pvs["DISARM_CMD"].set(
                0,
                process=False,
            )
            self.pvs["START_CMD"].set(
                0,
                process=False,
            )
            self.pvs["ABORT_CMD"].set(
                0,
                process=False,
            )

        for name in ("MANUAL_STATE", "MANUAL_REASON", "MANUAL_FAULT"):
            self.pvs[name] = builder.stringIn(name)
        self.pvs["MANUAL_ARMED"] = builder.boolIn("MANUAL_ARMED")
        self.pvs["SAFE_OFF_ID"] = builder.longIn("SAFE_OFF_ID")
        self.pvs["SAFE_OFF_ID"].set(0)
        if execution_enabled:
            for name in ("MANUAL_ARM_CMD", "MANUAL_DISARM_CMD", "RF_ON_CMD",
                         "RF_OFF_CMD", "FM_ON_CMD", "FM_OFF_CMD"):
                self.pvs[name] = builder.boolOut(
                    name, always_update=True, on_update_name=self._manual_command)
                self.pvs[name].set(0, process=False)
        self._publish_manual()

        self.pvs["EXECUTION_ENABLED"].set(
            int(execution_enabled)
        )

        self.pvs["WAVEFORM_STAGE_ENABLED"].set(
            int(execution_enabled)
        )

        self.pvs["WAVEFORM_STAGE_POINTS"].set(0)
        self.pvs["WAVEFORM_STAGE_ID"].set(0)

        self._set_waveform_stage_status(
            "IDLE"
            if execution_enabled
            else "DISABLED",
            "Sequencer staging enabled"
            if execution_enabled
            else "Sequencer execution disabled",
        )

        self.pvs["EXECUTION_ARMED"].set(0)
        self.pvs["PREPARED"].set(0)
        self.pvs["PREPARED_WAVEFORM_ID"].set(0)
        self._set_prepare_status(
            "IDLE" if execution_enabled else "DISABLED",
            "Generate/load required" if execution_enabled else "Execution disabled",
        )

        self._set_execution_status(
            EXEC_STATE_IDLE
            if execution_enabled
            else EXEC_STATE_DISABLED,
            (
                "Physical execution idle"
                if self._execution_mode == "physical"
                else "Simulator execution idle"
            )
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

        # Source-control status exists even when the write capability is off.
        self.pvs["NMR_SOURCE_STATE"] = builder.stringIn("NMR_SOURCE_STATE")
        self.pvs["NMR_SOURCE_REASON"] = builder.stringIn("NMR_SOURCE_REASON")
        self.pvs["NMR_SOURCE_SELECTED_RBV"] = builder.stringIn(
            "NMR_SOURCE_SELECTED_RBV"
        )
        self.pvs["NMR_SOURCE_EPOCH_RBV"] = builder.longIn(
            "NMR_SOURCE_EPOCH_RBV"
        )
        self.pvs["NMR_SOURCE_SELECTED_RBV"].set("UNVERIFIED")
        self.pvs["NMR_SOURCE_EPOCH_RBV"].set(-1)

        if self.settings.get("nmr_source_selection_enabled", False) is True:
            # No command is published for disabled or unconfigured profiles.
            self._validated_nmr_source_configuration()
            self.pvs["NMR_SOURCE_REQUEST"] = builder.stringOut(
                "NMR_SOURCE_REQUEST",
                always_update=True,
                on_update_name=self._nmr_source_command,
            )
            self.pvs["NMR_SOURCE_REQUEST"].set("", process=False)
            self._set_nmr_source_status(
                "RECOVERY_REQUIRED", "Startup reconciliation required"
            )
        else:
            self._set_nmr_source_status("DISABLED", "Source selection disabled")

    # ------------------------------------------------------------
    # master_ioc compatibility
    # ------------------------------------------------------------

    def connect(self):
        """Logic IOC: no physical device connection is required."""
        return True

    def _set_prepare_status(self, state, reason):
        self.pvs["PREPARE_STATE"].set(str(state)[:39])
        self.pvs["PREPARE_REASON"].set(str(reason)[:39])

    def _set_prepared(self, prepared, *, waveform_id=0, signature=None):
        self._prepared = bool(prepared)
        self._prepared_waveform_id = int(waveform_id) if prepared else 0
        self._prepared_signature = signature if prepared else None
        self.pvs["PREPARED"].set(int(self._prepared))
        self.pvs["PREPARED_WAVEFORM_ID"].set(self._prepared_waveform_id)
        if not prepared:
            self._set_execution_armed(False)

    def _parameter_signature(self, parameters=None):
        p = parameters or self._parameters()
        return (
            round(float(p.carrier_mhz), 12),
            round(float(p.start_level_dbm), 12),
            round(float(p.target_level_dbm), 12),
            round(float(p.fm_deviation_khz), 12),
            round(float(p.fm_period_s), 12),
            round(float(p.vhigh_v), 12),
            round(float(p.vlow_v), 12),
            int(p.fm_repeats),
            int(self.pvs["WAVEFORM_STAGE_ID"].get()),
        )

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

    def _set_execution_armed(
        self,
        armed,
    ):
        """Set the one-shot runtime execution arm."""

        self._execution_armed = bool(
            armed
        )

        if "EXECUTION_ARMED" in self.pvs:
            self.pvs[
                "EXECUTION_ARMED"
            ].set(
                int(
                    self._execution_armed
                )
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

    def _validated_nmr_source_configuration(self):
        """Validate full opt-in configuration; never infer trusted PIDs."""
        settings = self.settings
        config = {
            "path": settings.get("nmr_source_control_path"),
            "uid": settings.get("nmr_source_control_fitter_uid"),
            "pid": settings.get("nmr_source_control_fitter_pid"),
            "timeout_s": settings.get("nmr_source_control_timeout_s", 2.0),
        }
        validate_ipc_configuration(
            path=config["path"], uid=config["uid"], pid=config["pid"],
            read_fitter=read_fitter_source_snapshot,
            timeout_s=config["timeout_s"],
        )
        freshness = settings.get("nmr_source_readback_max_age_s", 3.0)
        if (type(freshness) not in (int, float)
                or not 0.1 <= freshness <= 10.0):
            raise ValueError("Invalid NMR source readback freshness limit")
        config["max_age_s"] = float(freshness)
        return config

    def _set_nmr_source_status(self, state, reason):
        self.pvs["NMR_SOURCE_STATE"].set(str(state)[:39])
        self.pvs["NMR_SOURCE_REASON"].set(str(reason)[:39])

    def _nmr_source_activity(self):
        if getattr(self, "_nmr_recovery_required", False):
            recovery = getattr(self, "_nmr_recovery", None)
            if recovery is None or not recovery.ready:
                return True
            try:
                if recovery.record["identity"] != recovery.process_identity():
                    recovery.ready = False
                    return True
            except (OSError, RuntimeError, PermissionError, ValueError):
                recovery.ready = False
                return True
        return (self._nmr_source_change_reserved
                or self._nmr_source_request is not None
                or (self._nmr_source_task is not None
                    and not self._nmr_source_task.done()))

    def _nmr_source_command(self, value, pv):
        """Only latch selection. Never perform socket/EPICS I/O here."""
        self._reset_momentary_command("NMR_SOURCE_REQUEST")
        if not isinstance(value, str):
            self._set_nmr_source_status("BLOCKED", "Invalid source request")
            return
        requested = value.strip().lower()
        if requested not in ("labview", "pynmr"):
            self._set_nmr_source_status("BLOCKED", "Use LABVIEW or PYNMR")
            return
        if self.settings.get("nmr_source_selection_enabled", False) is not True:
            self._set_nmr_source_status("DISABLED", "Selection not authorized")
            return
        if (self._nmr_source_activity() or self._manual_busy()
                or self._other_busy()):
            self._set_nmr_source_status("BLOCKED", "Sequencer busy")
            return
        if self._manual.state != "DISARMED" or self._manual.fault:
            self._set_nmr_source_status("BLOCKED", "Manual RF baseline unsafe")
            return
        self._nmr_source_request = requested
        self._set_nmr_source_status("PENDING", "Request awaiting verification")

    async def _reconcile_nmr_snapshot(self, config):
        """Use fresh EPICS + authenticated IPC + RF OFF; no state writes to fitter."""
        recovery = self._nmr_recovery
        if recovery is None:
            raise RuntimeError("NMR recovery journal unavailable")
        await verify_ssrf_rf_off_fresh(max_age_s=config["max_age_s"])
        authenticated = await request_source_snapshot(
            path=config["path"], allowed_fitter_uid=config["uid"],
            allowed_fitter_pid=config["pid"], timeout_s=config["timeout_s"],
        )
        epics = await read_fitter_source_snapshot(
            prefix=self.settings.get("nmr_source_fitter_prefix", "UNH:GEN:SSRF:FIT"),
            max_age_s=config["max_age_s"],
        )
        if epics != authenticated:
            raise RuntimeError("Authenticated IPC and fresh EPICS source disagree")
        await verify_ssrf_rf_off_fresh(max_age_s=config["max_age_s"])
        identity_override = self.settings.get("nmr_source_recovery_rebaseline_identity")
        if identity_override is not None:
            # One-time operator-controlled profile, bound to the exact /proc
            # identity of the commissioned fitter. Remove after recovery.
            recovery.operator_rebaseline(epics, expected_identity=identity_override)
        elif recovery.record is None:
            if self.settings.get("nmr_source_recovery_initial_commissioning") is not True:
                raise RuntimeError("Initial NMR recovery commissioning required")
            recovery.commission_initial(epics)
        else:
            recovery.reconcile(epics)
        self.pvs["NMR_SOURCE_SELECTED_RBV"].set(epics["selected_source"].upper())
        self.pvs["NMR_SOURCE_EPOCH_RBV"].set(epics["route_epoch"])
        self._set_execution_armed(False)
        self._set_prepared(False)
        return epics

    async def _service_nmr_recovery(self):
        """Single startup attempt; failed reconciliation requires controlled restart."""
        if not self._nmr_recovery_required or self._nmr_recovery_attempted:
            return
        self._nmr_recovery_attempted = True
        if self._nmr_recovery is None:
            self._set_nmr_source_status("RECOVERY_REQUIRED", "Recovery journal unavailable")
            return
        try:
            config = self._validated_nmr_source_configuration()
            await self._reconcile_nmr_snapshot(config)
        except (Exception, asyncio.CancelledError) as exc:
            self._set_nmr_source_status("RECOVERY_REQUIRED", str(exc))
        else:
            self._set_nmr_source_status("RECONCILED", "Fitter state verified")

    async def _run_nmr_source_request(self, source):
        """Only the IOC dispatcher starts this transaction task."""

        async def fresh_fitter():
            result = await read_fitter_source_snapshot(
                prefix=self.settings.get(
                    "nmr_source_fitter_prefix", "UNH:GEN:SSRF:FIT"
                ),
                max_age_s=config["max_age_s"],
            )
            self.pvs["NMR_SOURCE_SELECTED_RBV"].set(
                result["selected_source"].upper()
            )
            self.pvs["NMR_SOURCE_EPOCH_RBV"].set(result["route_epoch"])
            return result

        async def fresh_rf():
            return await verify_ssrf_rf_off_fresh(
                max_age_s=config["max_age_s"]
            )

        try:
            # Configuration must fail through the same FAULT reporting
            # path as readback or IPC failures.
            config = self._validated_nmr_source_configuration()

            # Update the clean baseline if a trusted physical route update
            # advanced the fitter epoch since the previous reconciliation.
            # This is read-only and still requires RF OFF + IPC/EPICS agreement.
            await fresh_rf()
            if (getattr(self, "_nmr_recovery_required", False)
                    and self._nmr_recovery is not None
                    and self._nmr_recovery.ready):
                await self._reconcile_nmr_snapshot(config)
            result = await execute_nmr_source_change_via_ipc(
                requested=source,
                sequencer=self,
                read_fitter=fresh_fitter,
                path=config["path"],
                allowed_fitter_uid=config["uid"],
                allowed_fitter_pid=config["pid"],
                timeout_s=config["timeout_s"],
                read_rf_off=fresh_rf,
            )
        except asyncio.CancelledError:
            self._set_nmr_source_status("FAULT", "Source selection interrupted")
            raise
        except Exception as exc:
            # A lost acknowledgment is uncertain. Independently query the
            # authenticated live fitter and cross-check fresh EPICS and RF OFF.
            recovery = getattr(self, "_nmr_recovery", None)
            if (recovery is not None and recovery.record is not None
                    and recovery.record["state"] == "dispatching"
                    and self._nmr_source_change_reserved):
                try:
                    confirmed = await self._reconcile_nmr_snapshot(config)
                    if confirmed["selected_source"] != source:
                        raise RuntimeError("Unexpected recovery source")
                    self._complete_nmr_source_change_reservation(confirmed=True)
                except (Exception, asyncio.CancelledError):
                    self._set_nmr_source_status("FAULT", str(exc))
                else:
                    self._set_nmr_source_status("RECONCILED", "Lost IPC ack verified")
            else:
                self._set_nmr_source_status("FAULT", str(exc))
        else:
            self.pvs["NMR_SOURCE_SELECTED_RBV"].set(
                result["selected_source"].upper()
            )
            self.pvs["NMR_SOURCE_EPOCH_RBV"].set(result["route_epoch"])
            self._set_nmr_source_status("CONFIRMED", "Fitter source changed")

    def _service_nmr_source_request(self):
        if self._nmr_source_request is None:
            return
        requested = self._nmr_source_request
        self._nmr_source_request = None
        self._nmr_source_task = asyncio.create_task(
            self._run_nmr_source_request(requested)
        )

    async def _acquire_nmr_source_change_reservation(self):
        """Reserve idle sequencer and verify RF OFF before source selection.

        No fitter command is sent by this method.
        """
        if self._nmr_source_change_reserved:
            raise RuntimeError("NMR source change already reserved")

        # Software-only NMR source selection has an independent,
        # explicitly enabled capability. It never enables RF execution.
        if self.settings.get(
            "nmr_source_selection_enabled", False
        ) is not True:
            raise RuntimeError("NMR source selection not authorized")

        # Ignore this transaction's own pending task, but not other work.
        if self._manual_busy() or self._other_busy(include_nmr=False):
            raise RuntimeError("Sequencer operation pending or active")

        if self._manual.state != "DISARMED" or self._manual.fault:
            raise RuntimeError("Manual RF safe baseline unverified")

        if getattr(self, "_nmr_recovery_required", False):
            recovery = self._nmr_recovery
            if recovery is None or not recovery.ready:
                raise RuntimeError("NMR startup reconciliation not complete")

        # Set before the first await. Command admission must block
        # competing operations throughout RF verification.
        self._nmr_source_change_reserved = True

        try:
            rf = await self._execution_io.read_safety(
                "UNH:GEN:SSRF:GEN:RF_RBV"
            )
            if isinstance(rf, bool) or rf != 0:
                raise RuntimeError(
                    f"Cannot change NMR source: SMC RF readback={rf!r}"
                )
            if getattr(self, "_nmr_recovery_required", False):
                self._nmr_recovery.reserve()
        except BaseException:
            # Preflight only: no fitter command was issued.
            self._nmr_source_change_reserved = False
            raise

    def _cancel_nmr_source_change_reservation(self):
        """Cancel a reservation before any fitter write is attempted.

        The transaction coordinator alone must establish that no
        write has begun. Never use this for uncertain outcomes.
        """
        if not self._nmr_source_change_reserved:
            raise RuntimeError("No NMR source-change reservation")

        recovery = getattr(self, "_nmr_recovery", None)
        if recovery is not None:
            recovery.cancel_before_dispatch()
        self._nmr_source_change_reserved = False

    def _complete_nmr_source_change_reservation(
        self, *, confirmed
    ):
        """Release only after the fitter transaction is confirmed."""
        if not self._nmr_source_change_reserved:
            raise RuntimeError("No NMR source-change reservation")

        if confirmed is not True:
            raise RuntimeError(
                "NMR source-change outcome unverified; "
                "reservation remains active"
            )

        if getattr(self, "_nmr_recovery_required", False):
            if self._nmr_recovery is None or not self._nmr_recovery.ready:
                raise RuntimeError("Durable NMR acknowledgment unverified")
        self._nmr_source_change_reserved = False

    def _manual_busy(self):
        return (self._manual_request is not None or self._safe_off_requested
                or (self._manual_task is not None and not self._manual_task.done())
                or self._manual.armed
                or self._manual.state in ("RF_ON", "RF_FM_ACTIVE", "STOPPING", "CLEANUP_FAILED"))

    def _other_busy(self, *, include_nmr=True):
        return ((self._nmr_source_activity() if include_nmr else False)
                or self._start_requested or self._prepare_requested
                or self._waveform_stage_requested or self._execution_armed
                or any(t is not None and not t.done() for t in
                       (self._execution_task, self._prepare_task, self._waveform_stage_task)))

    def _publish_manual(self):
        self.pvs["MANUAL_FAULT"].set(self._manual.fault[:39])
        self.pvs["MANUAL_STATE"].set(self._manual.state[:39])
        self.pvs["MANUAL_REASON"].set(self._manual.reason[:39])
        self.pvs["MANUAL_ARMED"].set(int(self._manual.armed))

    def _request_safe_off(self):
        self._abort_requested = True
        self._safe_off_requested = True
        self._manual.armed = False
        self._set_execution_armed(False)
        self._publish_manual()

    def _manual_command(self, value, pv):
        name = pv.removeprefix(self.device_name + ":")
        self._reset_momentary_command(name)
        if not value or not self._execution_io.execution_enabled:
            return
        if name in ("RF_OFF_CMD", "MANUAL_DISARM_CMD"):
            self._request_safe_off()
            return
        if self._other_busy() or self._safe_off_requested or self._manual_request or (
                self._manual_task is not None and not self._manual_task.done()):
            self._manual.reason = "Conflicting operation active"
        elif name == "MANUAL_ARM_CMD" and self._manual.state not in ("DISARMED", "BLOCKED"):
            self._manual.reason = "RF OFF required before ARM"
        elif name == "RF_ON_CMD" and not self._manual.armed:
            self._manual.reason = "Manual RF not armed"
        else:
            self._manual_request = name
        self._publish_manual()

    async def _run_manual_request(self, name):
        try:
            if self._abort_requested:
                return
            if name == "MANUAL_ARM_CMD":
                await self._manual.arm()
            elif name == "RF_ON_CMD":
                await self._manual.rf_on(
                    float(self.pvs["CARRIER_MHZ"].get()),
                    float(self.pvs["MANUAL_LEVEL_DBM"].get()),
                )
            elif name == "FM_ON_CMD":
                if not self._prepared or self._prepared_signature != self._parameter_signature():
                    raise RuntimeError("Prepare selected waveform before manual ARM")
                await self._manual.fm_on(self._parameters(), self._prepared_waveform_id)
            elif name == "FM_OFF_CMD":
                await self._manual.fm_off()
        except Exception as exc:
            self._manual.fault = str(exc)
            self._manual.reason = str(exc)
            self._manual.armed = False
            self._request_safe_off()
        finally:
            self._publish_manual()
            self._manual_task = None

    async def _service_manual(self):
        # A single owner executes transitions/cleanup; ABORT never cancels OFF writes.
        tasks = (self._execution_task, self._prepare_task, self._waveform_stage_task,
                 self._manual_task)
        if any(t is not None and not t.done() for t in tasks):
            return
        if self._safe_off_requested:
            self._manual_request = None
            self._start_requested = self._prepare_requested = False
            self._waveform_stage_requested = False
            ok = await self._manual.safe_off()
            self._safe_off_id += 1
            self.pvs["SAFE_OFF_ID"].set(self._safe_off_id)
            self._safe_off_requested = False
            self._abort_requested = False
            self._set_prepared(False)
            self._set_execution_status("ABORTED" if ok else "CLEANUP_FAILED", self._manual.reason)
        elif self._manual_request:
            name, self._manual_request = self._manual_request, None
            self._abort_requested = False
            self._manual_task = asyncio.create_task(self._run_manual_request(name))
        elif self._manual.state in ("RF_ON", "RF_FM_ACTIVE") or self._manual.armed:
            try:
                if self._manual.armed:
                    await self._manual.idle_safety()
                else:
                    await self._manual.monitor()
            except Exception as exc:
                self._manual.fault = str(exc)
                self._manual.reason = str(exc)
                self._request_safe_off()
                await self._service_manual()
        self._publish_manual()

    def _prepare_command(self, new_value, pv):
        """Latch one pre-burn source configuration/load request."""
        self._reset_momentary_command("PREPARE_CMD")
        if not bool(new_value):
            return

        if not self._execution_io.execution_enabled:
            self._set_prepare_status("DISABLED", "Execution disabled")
            return

        running = (
            (self._execution_task is not None and not self._execution_task.done())
            or (self._prepare_task is not None and not self._prepare_task.done())
            or self._start_requested
            or self._prepare_requested
        )
        staging = (
            self._waveform_stage_requested
            or (self._waveform_stage_task is not None and not self._waveform_stage_task.done())
        )

        if running or self._manual_busy() or self._nmr_source_activity():
            self._set_prepare_status("BLOCKED", "Sequence already active")
            return
        if staging:
            self._set_prepare_status("BLOCKED", "Waveform staging active")
            return
        if int(self.pvs["WAVEFORM_STAGE_POINTS"].get()) != WAVEFORM_POINTS:
            self._set_prepare_status("BLOCKED", "8192-point waveform not staged")
            return

        self._set_prepared(False)
        self._abort_requested = False
        self._prepare_requested = True
        self._set_prepare_status("REQUESTED", "Preparation requested")

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
            "ARM_CMD",
            "DISARM_CMD",
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
            (self._execution_task is not None and not self._execution_task.done())
            or (self._prepare_task is not None and not self._prepare_task.done())
        )

        if pv_name == "ABORT_CMD":
            self._request_safe_off()
        elif pv_name in ("ARM_CMD", "START_CMD") and (
            self._manual_busy() or self._nmr_source_activity()
        ):
            self._set_execution_status(EXEC_STATE_IDLE, "Manual operation or cleanup active")
            return

        if pv_name == "ARM_CMD":
            if self._require_prepared and not self._prepared:
                self._set_execution_status(
                    EXEC_STATE_IDLE,
                    "Arm blocked: generate/load first",
                )
                return
            if running or self._start_requested or self._prepare_requested:
                self._set_execution_status(
                    EXEC_STATE_RUNNING,
                    "Cannot arm while sequence running",
                )
                return

            self._set_execution_armed(
                True
            )

            self._set_execution_status(
                EXEC_STATE_IDLE,
                "Execution armed",
            )
            return

        if pv_name == "DISARM_CMD":
            self._request_safe_off()
            self._set_execution_armed(
                False
            )

            # A disarm during or immediately before execution
            # also requests the existing graceful abort path.
            if running or self._start_requested or self._prepare_requested:
                self._abort_requested = True

                self._set_execution_status(
                    EXEC_STATE_RUNNING,
                    "Disarmed; abort requested",
                )
            else:
                self._set_execution_status(
                    EXEC_STATE_IDLE,
                    "Execution disarmed",
                )

            return

        if pv_name == "START_CMD":
            if self._require_prepared and not self._prepared:
                self._set_execution_status(
                    EXEC_STATE_IDLE,
                    "Start blocked: source not prepared",
                )
                return

            staging_active = (
                self._waveform_stage_requested
                or (
                    self._waveform_stage_task is not None
                    and not self._waveform_stage_task.done()
                )
            )

            if staging_active:
                self._set_execution_status(
                    EXEC_STATE_IDLE,
                    "Start blocked: waveform staging active",
                )
                return

            if running or self._start_requested or self._prepare_requested:
                self._set_execution_status(
                    EXEC_STATE_RUNNING,
                    "Sequence already running",
                )
                return

            if not self._execution_armed:
                self._set_execution_status(
                    EXEC_STATE_IDLE,
                    "Start blocked: execution not armed",
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

            # One successful START request consumes the arm.
            # Another sequence always requires an explicit re-arm.
            self._set_execution_armed(
                False
            )

            self._abort_requested = False
            self._start_requested = True

            self._set_execution_status(
                EXEC_STATE_IDLE,
                "Start requested",
            )
            return

        # ABORT_CMD
        #
        # Abort is always available and always removes any
        # authorization for a subsequent sequence.
        self._set_execution_armed(
            False
        )

        if running or self._start_requested or self._prepare_requested:
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

    def _set_waveform_stage_status(
        self,
        state,
        reason,
    ):
        """Publish bounded staging status strings."""

        self.pvs["WAVEFORM_STAGE_STATE"].set(
            str(state)[:39]
        )

        self.pvs["WAVEFORM_STAGE_REASON"].set(
            str(reason)[:39]
        )

    def _waveform_stage_command(
        self,
        new_value,
        pv,
    ):
        """
        Validate/latch one staging request.

        This callback performs no EPICS I/O.
        """

        if not self._execution_io.execution_enabled:
            self.pvs["WAVEFORM_STAGE_POINTS"].set(0)
            self._set_waveform_stage_status(
                "DISABLED",
                "Sequencer execution disabled",
            )
            return

        burn_running = (
            self._execution_task is not None
            and not self._execution_task.done()
        )

        stage_running = (
            self._waveform_stage_task is not None
            and not self._waveform_stage_task.done()
        )

        if (
            burn_running
            or self._manual_busy()
            or self._nmr_source_activity()
            or self._prepare_requested
            or (self._prepare_task is not None and not self._prepare_task.done())
            or self._start_requested
        ):
            self._set_waveform_stage_status(
                "REJECTED",
                "Burn execution active",
            )
            return

        if (
            stage_running
            or self._waveform_stage_requested
        ):
            self._set_waveform_stage_status(
                "REJECTED",
                "Waveform staging already active",
            )
            return

        try:
            values = validate_normalized_waveform(
                new_value
            )
        except Exception as exc:
            self.pvs["WAVEFORM_STAGE_POINTS"].set(0)
            self._set_waveform_stage_status(
                "REJECTED",
                str(exc),
            )
            return

        self._set_prepared(False)

        self._waveform_stage_values = (
            values.copy()
        )

        self._waveform_stage_requested = True

        self.pvs["WAVEFORM_STAGE_POINTS"].set(
            int(values.size)
        )

        self._set_waveform_stage_status(
            "REQUESTED",
            "Validated request queued",
        )

    def _service_waveform_stage_request(self):
        """Start one staging task on the IOC asyncio loop."""

        if not self._waveform_stage_requested:
            return

        if (
            self._waveform_stage_task is not None
            and not self._waveform_stage_task.done()
        ):
            return

        self._waveform_stage_task = (
            asyncio.create_task(
                self._run_waveform_stage_request()
            )
        )

    async def _run_waveform_stage_request(self):
        """
        Forward one validated request through controlled EpicsIO.

        Staging is complete only after the WaveGen IOC explicitly
        acknowledges a NEW IOC-memory staging event.

        This path writes WAVEFORM_CMD and WAVEFORM_STAGE_CMD only.
        It never issues LOAD_CMD or OUTPUT_CMD.
        """

        self._waveform_stage_requested = False

        values = self._waveform_stage_values

        try:
            if values is None:
                raise RuntimeError(
                    "No validated waveform request"
                )

            baseline_stage_id = int(
                await self._execution_io.read_safety(
                    WAVEGEN_WAVEFORM_STAGE_ID_PV
                )
            )

            self._set_waveform_stage_status(
                "STAGING",
                "Waiting for WaveGen IOC acknowledgment",
            )

            await self._execution_io.write(
                WAVEGEN_WAVEFORM_CMD_PV,
                values.copy(),
            )

            # Commit IOC-memory staging explicitly after the array write.
            # This does not invoke physical LOAD_CMD or enable RF output.
            await self._execution_io.write(
                WAVEGEN_WAVEFORM_CMD_PV.replace(
                    ":WAVEFORM_CMD", ":WAVEFORM_STAGE_CMD"
                ),
                1,
            )

            acknowledged = False
            last_stage_id = baseline_stage_id
            last_points = 0

            for _attempt in range(80):

                last_stage_id = int(
                    await self._execution_io.read_safety(
                        WAVEGEN_WAVEFORM_STAGE_ID_PV
                    )
                )

                last_points = int(
                    await self._execution_io.read_safety(
                        WAVEGEN_WAVEFORM_POINTS_PV
                    )
                )

                if (
                    last_stage_id > baseline_stage_id
                    and
                    last_points == WAVEFORM_POINTS
                ):
                    acknowledged = True
                    break

                await self._execution_io.sleep(
                    0.05
                )

            if not acknowledged:
                raise RuntimeError(
                    "WaveGen staging acknowledgment timeout: "
                    f"stage_id {baseline_stage_id}"
                    f"->{last_stage_id}, "
                    f"points={last_points}"
                )

            self._waveform_stage_id += 1

            self.pvs["WAVEFORM_STAGE_ID"].set(
                self._waveform_stage_id
            )

            self.pvs["WAVEFORM_STAGE_POINTS"].set(
                last_points
            )

            self._set_waveform_stage_status(
                "STAGED",
                f"{last_points} points staged in IOC memory",
            )

        except Exception as exc:
            self._set_waveform_stage_status(
                "FAILED",
                str(exc),
            )

        finally:
            self._waveform_stage_values = None
            self._waveform_stage_task = None

    def _service_prepare_request(self):
        if not self._prepare_requested:
            return
        if self._prepare_task is not None and not self._prepare_task.done():
            return
        self._prepare_task = asyncio.create_task(self._run_prepare_request())

    async def _run_prepare_request(self):
        self._prepare_requested = False
        self._set_prepared(False)

        try:
            if self._abort_requested:
                self._set_prepare_status("ABORTED", "Aborted before preparation")
                return

            ready, reason = await self._executor_readiness()
            if not ready:
                self._set_prepare_status("BLOCKED", str(reason))
                return

            parameters = self._parameters()
            waveform_points = int(await self._execution_io.read_safety(
                "UNH:GEN:SSRF:WGEN:WAVEFORM_POINTS"
            ))
            waveform_id_before = int(await self._execution_io.read_safety(
                "UNH:GEN:SSRF:WGEN:WAVEFORM_ID"
            ))

            plan = build_prepare_plan(parameters, waveform_points, waveform_id_before)
            recovery = build_prepare_cleanup_plan()
            self._set_prepare_status("RUNNING", "Configuring source and loading ARB")
            result = await self._executor.run_preparation(plan, recovery)

            if not result.ok:
                self._set_prepare_status(result.state, result.reason)
                return

            waveform_ready = int(await self._execution_io.read_safety(
                "UNH:GEN:SSRF:WGEN:WAVEFORM_READY"
            ))
            arb_points = int(await self._execution_io.read_safety(
                "UNH:GEN:SSRF:WGEN:ARB_POINTS_RBV"
            ))
            waveform_id = int(await self._execution_io.read_safety(
                "UNH:GEN:SSRF:WGEN:WAVEFORM_ID"
            ))

            if waveform_ready != 1 or arb_points != WAVEFORM_POINTS or waveform_id <= waveform_id_before:
                raise RuntimeError("WaveGen preparation verification failed")

            signature = self._parameter_signature(parameters)
            self._set_prepared(True, waveform_id=waveform_id, signature=signature)
            self._set_prepare_status("PREPARED", "Source loaded; ready to ARM")

        except Exception as exc:
            self._set_prepared(False)
            self._set_prepare_status("FAILED", str(exc))
        finally:
            self._abort_requested = False
            self._prepare_task = None

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
                await self._execution_io.read_safety(
                    "UNH:GEN:SSRF:WGEN:"
                    "WAVEFORM_POINTS"
                )
            )

            waveform_id_before = int(
                await self._execution_io.read_safety(
                    "UNH:GEN:SSRF:WGEN:"
                    "WAVEFORM_ID"
                )
            )

            if self._require_prepared:
                if not self._prepared:
                    raise RuntimeError("Source is not prepared")
                if self._prepared_signature != self._parameter_signature(parameters):
                    self._set_prepared(False)
                    raise RuntimeError("Prepared recipe changed; generate/load again")
                if waveform_id_before != self._prepared_waveform_id:
                    self._set_prepared(False)
                    raise RuntimeError("Loaded waveform changed; generate/load again")
                plan = build_prepared_burn_plan(
                    parameters,
                    waveform_points,
                    self._prepared_waveform_id,
                )
            else:
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
            if self._require_prepared:
                self._set_prepared(False)
                self._set_prepare_status("IDLE", "Generate/load required for next burn")
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
        """Return the latched operator/system abort request."""

        return bool(
            self._abort_requested
        )

    async def _read_int(
        self,
        pv,
    ):
        value = await self._execution_io.read_safety(pv)

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
        # Command callbacks only latch validated requests. Actual EPICS
        # forwarding/execution begins here on the IOC dispatcher loop.
        await self._service_manual()
        await self._service_nmr_recovery()
        if self._nmr_recovery_required and self._nmr_source_activity():
            # Never preserve a pre-restart or pre-fault burn authorization.
            self._set_execution_armed(False)
            self._set_prepared(False)
        self._service_nmr_source_request()

        # Any queued/active NMR request inhibits burn readiness.
        # ABORT and safe-off handling remain available above.
        if self._nmr_source_activity():
            self.pvs["READY"].set(0)
            self.pvs["STATE"].set(STATE_BLOCKED)
            self.pvs["REASON"].set(
                "NMR software source change reserved"
            )
            return True

        if self._safe_off_requested or self._manual_busy():
            self.pvs["READY"].set(0)
            self.pvs["STATE"].set("MANUAL_OPERATION")
            self.pvs["REASON"].set("Manual operation or safe-off active")
            return True
        self._service_waveform_stage_request()
        self._service_prepare_request()
        self._service_execution_request()

        # Parameter validation is entirely local.
        parameters = self._parameters()

        self._publish_parameters(
            parameters
        )

        if self._prepared and self._prepared_signature != self._parameter_signature(parameters):
            self._set_prepared(False)
            self._set_prepare_status("STALE", "Recipe changed; generate/load again")

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
            # Any loss of burn readiness invalidates a previously
            # granted one-shot authorization.  A new ARM is required
            # after the fault is corrected.
            self._set_execution_armed(
                False
            )

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
            # Communication/input loss is fail-closed.  Do not allow
            # an arm granted before the fault to survive recovery.
            self._set_execution_armed(
                False
            )

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

        if not decision.ready:
            # Includes communication faults, unsafe initial outputs,
            # invalid switch state and interlock/readiness loss.
            # Recovery never restores a previously granted arm.
            self._set_execution_armed(
                False
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
