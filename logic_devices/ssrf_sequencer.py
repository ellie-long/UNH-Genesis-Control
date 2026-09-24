"""
Genesis Phase-I ssRF burn sequencer.

5B.4 stage:
- publishes burn parameters and derived values
- reads live device/interlock state
- evaluates preflight readiness
- performs NO EPICS writes to hardware

Hardware sequencing is intentionally not implemented yet.
"""

from __future__ import annotations

import asyncio

import aioca
from softioc import builder

from logic_devices.ssrf_sequence_core import (
    BurnParameters,
    PreflightInputs,
    evaluate_preflight,
    validate_parameters,
)


STATE_UNKNOWN = "UNKNOWN"
STATE_INVALID = "INVALID_PARAMETERS"
STATE_BLOCKED = "BLOCKED"
STATE_READY = "READY"


class Device:
    """Read-only Genesis ssRF sequencer/preflight IOC."""

    def __init__(
        self,
        device_name,
        settings,
    ):
        self.device_name = device_name
        self.settings = settings

        self.pvs = {}

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

        # Preflight result.
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

        # Initialize fail-closed.
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

    async def _read_int(
        self,
        pv,
    ):
        value = await aioca.caget(
            pv,
            timeout=2.0,
        )

        return int(value)

    async def _read_preflight_inputs(self):
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

        return PreflightInputs(
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
                await self._read_preflight_inputs()
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

        decision = evaluate_preflight(
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
