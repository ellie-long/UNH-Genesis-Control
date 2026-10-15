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
    "UNH:GEN:MW:PS:VSET_CMD",             # UCA voltage, from the UCA screen
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


# ------------------------------------------------- microwave leg: UCA procedure
class _Plant:
    """Stands in for engine + RestrictedCA + the physical microwave IOCs: RF
    enable is accepted only at UCA 5.0 +/- 0.1 V with the output ON (the
    supervisor's ON-edge permissive); read-backs are pushed to the leg."""

    def __init__(self, uca=5.0, uca_out=1):
        from cooldown_sim.autopilot.mw import MicrowaveLeg

        class Clock:
            running = True

            def now(self):
                return 0.0

        class Events:
            def __init__(self):
                self.rows = []

            def write(self, sim, kind, detail):
                self.rows.append((kind, detail))

        class Engine:
            autopilot = {"mw": True}
            provider = object()
            clock, events, timeline = Clock(), Events(), None

        self.engine = Engine()
        self.leg = MicrowaveLeg(self.engine, self)
        self.leg._values.update(rf=0, valid=1, freq=140.0, power=10.0, uca=uca, uca_out=uca_out)
        self.leg._push()
        self.writes = []                   # (pv, value, UCA the chain sees, RF on) at each write

    async def caput(self, pv, value):
        v = self.leg._values
        self.writes.append((pv.rsplit(":", 1)[1], value, self.leg.uca_V(), bool(v["rf"])))
        if pv.endswith(":VSET_CMD"):
            v["uca"] = float(value)
        elif pv.endswith(":RFEnable") and v["uca_out"] and abs(v["uca"] - 5.0) <= 0.1:
            v["rf"] = 1
        elif pv.endswith(":RFAbort"):
            v["rf"] = 0
        self.leg._push()

    def run(self, on):
        self.leg.desired = lambda t: (on, 140.0, 10.0)
        asyncio.run(self.leg.step())


def test_mw_autopilot_lowers_the_uca_only_after_rf_on_and_restores_it_before_rf_off():
    plant = _Plant()
    plant.run(True)
    rf = [w[0] for w in plant.writes]
    assert rf == ["OutputFrequency", "MW_PWRC", "RFEnable", "VSET_CMD"]   # UCA already 5 V
    assert plant.writes[2][2] == 5.0 and plant.writes[3][1:] == (0.0, 5.0, True)
    assert plant.leg.accepted == 1 and plant.leg.uca_V() == 0.0           # full power (history)
    plant.writes.clear()
    plant.run(False)
    assert [(w[0], w[1]) for w in plant.writes] == [("VSET_CMD", 5.0), ("RFAbort", 1)]
    assert plant.writes[1][2:] == (5.0, True)                             # RF still on, UCA back at 5 V
    details = [d for k, d in plant.engine.events.rows if k == "microwave"]
    assert any(d.startswith("autopilot: UCA -> 0.0 V after RF ON confirmed") for d in details)
    assert any(d.startswith("autopilot: UCA -> 5.0 V before RF OFF") for d in details)


def test_mw_autopilot_returns_the_uca_to_5_v_before_rf_enable():
    plant = _Plant(uca=0.0)                # e.g. left at full power by a human
    plant.run(True)
    rf = [(w[0], w[1]) for w in plant.writes if w[0] in ("VSET_CMD", "RFEnable")]
    assert rf == [("VSET_CMD", 5.0), ("RFEnable", 1), ("VSET_CMD", 0.0)]
    assert plant.leg.accepted == 1 and plant.leg.refusals == 0


def test_uca_output_off_reads_as_no_power():
    plant = _Plant(uca=0.0, uca_out=0)     # an OFF Siglent output reads 0 V
    assert plant.leg.uca_V() == 5.0
    plant.leg._values["uca_out"] = float("nan")
    assert plant.leg.uca_V() != plant.leg.uca_V()       # unknown -> NaN (model keeps its last value)
