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

    def run(self, on, uca=0.0):
        self.leg.desired = lambda t: (on, 140.0, 10.0, uca if on else 5.0)
        asyncio.run(self.leg.step())


def test_mw_autopilot_lowers_the_uca_only_after_rf_on_and_restores_it_before_rf_off():
    plant = _Plant()
    plant.run(True)
    rf = [w[0] for w in plant.writes]
    assert rf == ["OutputFrequency", "MW_PWRC", "RFEnable", "VSET_CMD"]   # UCA already 5 V
    assert plant.writes[2][2] == 5.0 and plant.writes[3][1:] == (0.0, 5.0, True)
    assert plant.leg.accepted == 1 and plant.leg.uca_V() == 0.0           # history says full power
    plant.writes.clear()
    plant.run(False)
    assert [(w[0], w[1]) for w in plant.writes] == [("VSET_CMD", 5.0), ("RFAbort", 1)]
    assert plant.writes[1][2:] == (5.0, True)                             # RF still on, UCA back at 5 V
    details = [d for k, d in plant.engine.events.rows if k == "microwave"]
    assert any(d.startswith("autopilot: UCA -> 0.0 V") for d in details)
    assert any(d.startswith("autopilot: UCA -> 5.0 V before RF OFF") for d in details)


def test_mw_autopilot_returns_the_uca_to_5_v_before_rf_enable():
    plant = _Plant(uca=0.0)                # e.g. left at full power by a human
    plant.run(True)
    rf = [(w[0], w[1]) for w in plant.writes if w[0] in ("VSET_CMD", "RFEnable")]
    assert rf == [("VSET_CMD", 5.0), ("RFEnable", 1), ("VSET_CMD", 0.0)]
    assert plant.leg.accepted == 1 and plant.leg.refusals == 0


def test_mw_autopilot_follows_the_history_uca_timeline():
    plant = _Plant()
    plant.run(True, uca=5.0)               # RF ON; the logbook has not lowered the UCA yet
    assert [w[0] for w in plant.writes] == ["OutputFrequency", "MW_PWRC", "RFEnable"]
    assert plant.leg.uca_V() == 5.0
    plant.writes.clear()
    plant.run(True, uca=5.0)               # nothing new: no writes
    assert plant.writes == []
    for v in (0.0, 2.5, 5.0, 0.0):         # each logged setting, in turn, RF staying on
        plant.run(True, uca=v)
    assert [(w[0], w[1]) for w in plant.writes] == [("VSET_CMD", v) for v in (0.0, 2.5, 5.0, 0.0)]
    assert all(w[3] for w in plant.writes)                                # RF on throughout
    plant.writes.clear()
    plant.run(False)
    assert [(w[0], w[1]) for w in plant.writes] == [("VSET_CMD", 5.0), ("RFAbort", 1)]


def test_mw_autopilot_never_lowers_the_uca_after_a_refused_rf_on():
    plant = _Plant(uca_out=0)              # output OFF: the supervisor refuses RF ON
    plant.leg._confirm_on = lambda: _false()
    plant.run(True, uca=0.0)
    assert ("VSET_CMD", 0.0) not in [(w[0], w[1]) for w in plant.writes]
    assert plant.leg._values["rf"] == 0


async def _false():
    return False


def test_mw_autopilot_desired_state_comes_from_the_record_and_the_logbook():
    import datetime as dt

    import numpy as np

    from cooldown_sim.autopilot.mw import MicrowaveLeg
    from cooldown_sim.data import records
    from cooldown_sim.data.records import SlowTable
    from cooldown_sim.data.timeline import Timeline

    def at(hhmm):
        h, m = map(int, hhmm.split(":"))
        return dt.datetime(2024, 12, 17, h, m, tzinfo=records.lab_tz()).timestamp()

    t = np.arange(at("16:00"), at("23:59"), 20.0)
    tl = Timeline(SlowTable(t=t, numeric={"mw_rf_status": (t >= at("16:34")).astype(float),
                                          "mw_power_db": np.full(len(t), -4.0),
                                          "mw_freq_ghz": np.full(len(t), 139.2666)}), max_hold_s=60)

    class Engine:
        provider, timeline = object(), tl

    leg = MicrowaveLeg(Engine(), None)
    assert leg.desired(at("16:20"))[0] is False and leg.desired(at("16:20"))[3] == 5.0
    on, _f, p, uca = leg.desired(at("16:40"))                 # RF on; logbook lowers it at 16:44
    assert on and p == -4.0 and uca == 5.0 and "5 V until the logbook" in leg._uca_why
    assert leg.desired(at("16:50"))[3] == 0.0 and "logbook setting" in leg._uca_why
    assert leg.desired(at("17:11") + 30)[3] == 5.0                        # brief check at 5 V
    assert leg.desired(at("17:30"))[3] == 0.0


def test_uca_output_off_reads_as_no_power():
    plant = _Plant(uca=0.0, uca_out=0)     # an OFF Siglent output reads 0 V
    assert plant.leg.uca_V() == 5.0
    plant.leg._values["uca_out"] = float("nan")
    assert plant.leg.uca_V() != plant.leg.uca_V()       # unknown -> NaN (model keeps its last value)
