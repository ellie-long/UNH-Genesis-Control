"""
Genesis Phase-I ssRF/NMR hardware-protection interlock.

This logic IOC owns no hardware.

It observes CONFIRMED readbacks from:
- NMR Mini-Circuits switch
- ssRF Mini-Circuits switch
- SMC100A RF output

and publishes permits used by the future burn sequencer.

Physical interpretation
-----------------------
Mini-Circuits masks:
    0 = both internal switches on PORT1
    3 = both internal switches on PORT2
    1/2 = MIXED / unsafe

For the Genesis installation:

NMR switch
    PORT1 / mask 0 = NMR Q-meter connected
    PORT2 / mask 3 = NMR path disconnected / protected

ssRF switch
    PORT1 / mask 0 = ssRF burn path connected
    PORT2 / mask 3 = ssRF burn path disconnected

Safety principles
-----------------
1. Unknown communication or unknown switch state never grants a permit.
2. Mixed switch masks never grant a permit.
3. The two target paths must never be connected simultaneously.
4. RF must be OFF before either RF switch is moved.
5. SMC RF may be enabled only when:
       NMR path is confirmed OFF
       ssRF path is confirmed ON

This milestone is deliberately READ-ONLY.
It publishes decisions but does not caput any device PV.

Sequencing, waveform readiness, write authorization and active enforcement
belong to later integration stages.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aioca
from softioc import builder


log = logging.getLogger(__name__)


UNKNOWN = "UNKNOWN"
SAFE_IDLE = "SAFE_IDLE"
NMR_LIVE = "NMR_LIVE"
SSRF_READY = "SSRF_READY"
BURNING = "BURNING"
RF_BLOCKED = "RF_BLOCKED"
MIXED_SWITCH = "MIXED_SWITCH"
CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class InterlockDecision:
    """Result of one pure interlock evaluation."""

    inputs_ok: int
    interlock_ok: int

    nmr_path_off: int
    ssrf_path_off: int
    ssrf_path_on: int
    rf_off: int

    nmr_connect_permit: int
    ssrf_connect_permit: int
    rf_enable_permit: int

    state: str
    reason: str


def _blocked(
    state: str,
    reason: str,
    *,
    inputs_ok: int = 0,
    nmr_path_off: int = 0,
    ssrf_path_off: int = 0,
    ssrf_path_on: int = 0,
    rf_off: int = 0,
) -> InterlockDecision:
    """Return a fail-closed decision."""

    return InterlockDecision(
        inputs_ok=inputs_ok,
        interlock_ok=0,
        nmr_path_off=nmr_path_off,
        ssrf_path_off=ssrf_path_off,
        ssrf_path_on=ssrf_path_on,
        rf_off=rf_off,
        nmr_connect_permit=0,
        ssrf_connect_permit=0,
        rf_enable_permit=0,
        state=state,
        reason=reason,
    )


def evaluate_interlock(
    *,
    nmr_comm: int | None,
    nmr_mask: int | None,
    ssrf_comm: int | None,
    ssrf_mask: int | None,
    smc_comm: int | None,
    smc_rf: int | None,
) -> InterlockDecision:
    """
    Pure Genesis ssRF safety decision.

    No EPICS access occurs here. This makes every safety state directly
    unit-testable.
    """

    # ------------------------------------------------------------
    # Communication/readback availability
    # ------------------------------------------------------------

    comms = {
        "NMR switch": nmr_comm,
        "ssRF switch": ssrf_comm,
        "SMC100A": smc_comm,
    }

    for name, value in comms.items():
        if value is None:
            return _blocked(
                UNKNOWN,
                f"{name} COMM unavailable",
            )

        if int(value) != 1:
            return _blocked(
                UNKNOWN,
                f"{name} disconnected",
            )

    if nmr_mask is None:
        return _blocked(
            UNKNOWN,
            "NMR switch mask unavailable",
        )

    if ssrf_mask is None:
        return _blocked(
            UNKNOWN,
            "ssRF switch mask unavailable",
        )

    if smc_rf is None:
        return _blocked(
            UNKNOWN,
            "SMC RF readback unavailable",
        )

    nmr_mask = int(nmr_mask)
    ssrf_mask = int(ssrf_mask)
    rf_on = bool(int(smc_rf))

    # ------------------------------------------------------------
    # Switch-state validity
    # ------------------------------------------------------------

    if nmr_mask not in (0, 3):
        return _blocked(
            MIXED_SWITCH,
            f"NMR switch mixed/invalid mask {nmr_mask}",
            inputs_ok=1,
        )

    if ssrf_mask not in (0, 3):
        return _blocked(
            MIXED_SWITCH,
            f"ssRF switch mixed/invalid mask {ssrf_mask}",
            inputs_ok=1,
        )


    nmr_on = nmr_mask == 0
    nmr_off = nmr_mask == 3

    ssrf_on = ssrf_mask == 0
    ssrf_off = ssrf_mask == 3

    rf_off = not rf_on

    # ------------------------------------------------------------
    # Hard conflicts
    # ------------------------------------------------------------

    if nmr_on and ssrf_on:
        return _blocked(
            CONFLICT,
            "NMR and ssRF paths both connected",
            inputs_ok=1,
            nmr_path_off=int(nmr_off),
            ssrf_path_off=int(ssrf_off),
            ssrf_path_on=int(ssrf_on),
            rf_off=int(rf_off),
        )

    if nmr_on and rf_on:
        return _blocked(
            CONFLICT,
            "SMC RF enabled while NMR path is connected",
            inputs_ok=1,
            nmr_path_off=int(nmr_off),
            ssrf_path_off=int(ssrf_off),
            ssrf_path_on=int(ssrf_on),
            rf_off=0,
        )

    # ------------------------------------------------------------
    # Permits
    #
    # Never move either switch while burn RF is active.
    # ------------------------------------------------------------

    nmr_connect_permit = int(
        ssrf_off and rf_off
    )

    ssrf_connect_permit = int(
        nmr_off and rf_off
    )

    rf_enable_permit = int(
        nmr_off and ssrf_on
    )

    # ------------------------------------------------------------
    # State description
    # ------------------------------------------------------------

    if nmr_on:
        state = NMR_LIVE
        reason = "NMR path connected"

    elif ssrf_on and rf_on:
        state = BURNING
        reason = "ssRF path connected; RF active"

    elif ssrf_on:
        state = SSRF_READY
        reason = "ssRF path connected; RF off"

    elif rf_on:
        state = RF_BLOCKED
        reason = "RF active while both target paths are disconnected"

    else:
        state = SAFE_IDLE
        reason = "both target paths disconnected; RF off"

    return InterlockDecision(
        inputs_ok=1,
        interlock_ok=1,
        nmr_path_off=int(nmr_off),
        ssrf_path_off=int(ssrf_off),
        ssrf_path_on=int(ssrf_on),
        rf_off=int(rf_off),
        nmr_connect_permit=nmr_connect_permit,
        ssrf_connect_permit=ssrf_connect_permit,
        rf_enable_permit=rf_enable_permit,
        state=state,
        reason=reason,
    )

class Device:
    """EPICS-facing Genesis ssRF safety interlock."""

    def __init__(self, device_name, settings):
        self.device_name = device_name
        self.settings = settings

        self.nmr_comm_pv = settings.get(
            "nmr_comm_pv",
            "UNH:GEN:SSRF:NMR_SW:COMM",
        )
        self.nmr_mask_pv = settings.get(
            "nmr_mask_pv",
            "UNH:GEN:SSRF:NMR_SW:MASK_RBV",
        )

        self.ssrf_comm_pv = settings.get(
            "ssrf_comm_pv",
            "UNH:GEN:SSRF:RF_SW:COMM",
        )
        self.ssrf_mask_pv = settings.get(
            "ssrf_mask_pv",
            "UNH:GEN:SSRF:RF_SW:MASK_RBV",
        )

        self.smc_comm_pv = settings.get(
            "smc_comm_pv",
            "UNH:GEN:SSRF:GEN:COMM",
        )
        self.smc_rf_pv = settings.get(
            "smc_rf_pv",
            "UNH:GEN:SSRF:GEN:RF_RBV",
        )

        self.pvs = {}
        self._create_pvs()

    def _create_pvs(self):
        self.pvs["INPUTS_OK"] = builder.boolIn(
            "INPUTS_OK",
            ZNAM="Invalid",
            ONAM="Valid",
        )

        self.pvs["INTERLOCK_OK"] = builder.boolIn(
            "INTERLOCK_OK",
            ZNAM="BLOCKED",
            ONAM="OK",
        )

        self.pvs["NMR_PATH_OFF"] = builder.boolIn(
            "NMR_PATH_OFF",
            ZNAM="Not confirmed",
            ONAM="Confirmed OFF",
        )

        self.pvs["SSRF_PATH_OFF"] = builder.boolIn(
            "SSRF_PATH_OFF",
            ZNAM="Not confirmed",
            ONAM="Confirmed OFF",
        )

        self.pvs["SSRF_PATH_ON"] = builder.boolIn(
            "SSRF_PATH_ON",
            ZNAM="Not confirmed",
            ONAM="Confirmed ON",
        )

        self.pvs["RF_OFF"] = builder.boolIn(
            "RF_OFF",
            ZNAM="RF active/unknown",
            ONAM="RF OFF",
        )

        self.pvs["NMR_CONNECT_PERMIT"] = builder.boolIn(
            "NMR_CONNECT_PERMIT",
            ZNAM="Blocked",
            ONAM="Permit",
        )

        self.pvs["SSRF_CONNECT_PERMIT"] = builder.boolIn(
            "SSRF_CONNECT_PERMIT",
            ZNAM="Blocked",
            ONAM="Permit",
        )

        self.pvs["RF_ENABLE_PERMIT"] = builder.boolIn(
            "RF_ENABLE_PERMIT",
            ZNAM="Blocked",
            ONAM="Permit",
        )

        self.pvs["STATE"] = builder.stringIn(
            "STATE"
        )

        self.pvs["REASON"] = builder.stringIn(
            "REASON"
        )

        self._publish(
            _blocked(
                UNKNOWN,
                "interlock starting",
            )
        )

    def connect(self):
        """
        No hardware connection.

        Start fail-closed until confirmed input PVs have been read.
        """

        self._publish(
            _blocked(
                UNKNOWN,
                "waiting for confirmed readbacks",
            )
        )

    async def _read_int(self, pv_name):
        try:
            value = await aioca.caget(
                pv_name,
                timeout=1.0,
            )
            return int(value)

        except Exception as exc:
            log.debug(
                "interlock could not read %s: %s",
                pv_name,
                exc,
            )
            return None

    def _publish(self, decision):
        self.pvs["INPUTS_OK"].set(
            decision.inputs_ok
        )
        self.pvs["INTERLOCK_OK"].set(
            decision.interlock_ok
        )

        self.pvs["NMR_PATH_OFF"].set(
            decision.nmr_path_off
        )
        self.pvs["SSRF_PATH_OFF"].set(
            decision.ssrf_path_off
        )
        self.pvs["SSRF_PATH_ON"].set(
            decision.ssrf_path_on
        )
        self.pvs["RF_OFF"].set(
            decision.rf_off
        )

        self.pvs["NMR_CONNECT_PERMIT"].set(
            decision.nmr_connect_permit
        )
        self.pvs["SSRF_CONNECT_PERMIT"].set(
            decision.ssrf_connect_permit
        )
        self.pvs["RF_ENABLE_PERMIT"].set(
            decision.rf_enable_permit
        )

        self.pvs["STATE"].set(
            decision.state[:39]
        )
        self.pvs["REASON"].set(
            decision.reason[:39]
        )

    async def do_reads(self):
        (
            nmr_comm,
            nmr_mask,
            ssrf_comm,
            ssrf_mask,
            smc_comm,
            smc_rf,
        ) = await asyncio.gather(
            self._read_int(self.nmr_comm_pv),
            self._read_int(self.nmr_mask_pv),
            self._read_int(self.ssrf_comm_pv),
            self._read_int(self.ssrf_mask_pv),
            self._read_int(self.smc_comm_pv),
            self._read_int(self.smc_rf_pv),
        )

        decision = evaluate_interlock(
            nmr_comm=nmr_comm,
            nmr_mask=nmr_mask,
            ssrf_comm=ssrf_comm,
            ssrf_mask=ssrf_mask,
            smc_comm=smc_comm,
            smc_rf=smc_rf,
        )

        self._publish(decision)

        return True
