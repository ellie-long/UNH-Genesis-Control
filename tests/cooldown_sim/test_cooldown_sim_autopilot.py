"""The autopilot can only do what an operator at the screens can do."""

import asyncio

import pytest

from cooldown_sim.autopilot.ca import OperatorAllowlist, RefusedWrite, RestrictedCA


@pytest.mark.parametrize("pv", [
    "UNH:GEN:CRYO:ARCH:Archive_Enable",
    "UNH:GEN:CRYO:SAFE:Safe_Reset",
    "UNH:GEN:CRYO:STATE:status",
    "UNH:GEN:SSRF:FIT:MODE_CMD",
    "UNH:GEN:SSRF:FIT:UPDATE_CONSTANTS_CMD",
])
def test_operator_command_pvs_permitted(pv):
    assert OperatorAllowlist().permits(pv)


@pytest.mark.parametrize("pv", [
    "UNH:GEN:CRYO:COLDBOARD:Coldboard_VC",
    "UNH:GEN:CRYO:COLDBOARD:Coldboard_Mode",
    "UNH:GEN:SSRF:GEN:RF_CMD",            # SMC100A RF directly
    "UNH:GEN:SSRF:WGEN:OUTPUT_CMD",       # WaveGen output directly
    "UNH:GEN:SSRF:NMR_SW:MASK_CMD",
    "UNH:GEN:MW:SRC:MW_RF",               # Vaunix RF directly
    "UNH:GEN:MW:PS:OUTPUT_CMD",           # UCA supply directly
    "UNH:GEN:CRYO:FRIDGE:F1_TI",
    "SOMETHING:ELSE",
])
def test_device_level_pvs_refused(pv):
    assert not OperatorAllowlist().permits(pv)


def test_refused_write_never_reaches_channel_access():
    class Events:
        rows = []

        def write(self, sim, kind, detail):
            self.rows.append((kind, detail))

    class Clock:
        def now(self):
            return 0.0

    events = Events()
    ca = RestrictedCA(events, Clock())
    with pytest.raises(RefusedWrite):
        asyncio.run(ca.caput("UNH:GEN:CRYO:COLDBOARD:Coldboard_VC", 5.0))
    assert events.rows == [("autopilot-refused", "UNH:GEN:CRYO:COLDBOARD:Coldboard_VC <- 5.0")]
