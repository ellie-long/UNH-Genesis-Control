"""'Magic' microwave frequencies: the DAQ's quick tune to the best P-up / P-down
frequency at the present field, from Sim Control through the MW leg."""

import asyncio

import numpy as np
import pytest

from cooldown_sim import channel_map, run_dir
from cooldown_sim.autopilot.mw import SUP, MicrowaveLeg
from cooldown_sim.clock import SimClock
from cooldown_sim.data.records import SlowTable
from cooldown_sim.data.timeline import Timeline
from cooldown_sim.engine import SimEngine
from cooldown_sim.model import spin1
from cooldown_sim.model.provider import ModelProvider
from cooldown_sim.profile import GeneratedStack
from cooldown_sim.timeparse import parse_lab_time

T0 = parse_lab_time("2026-06-17 11:00")
B = 4.939


def test_magic_ratios():
    assert spin1.magic_ratio("+") == pytest.approx(28.025)          # the LabVIEW DAQ's value
    assert spin1.magic_ratio("-") > spin1.magic_ratio("+")
    assert spin1.magic_ratio("+", "tempo_d_butanol") == pytest.approx(28.052)


@pytest.fixture
def engine(tmp_path):
    run = run_dir.create("interactive", "test", root=tmp_path)
    t = np.array([T0 - 60.0, T0 + 3600.0])
    table = SlowTable(t=t, numeric={"central_field": np.full(2, B), "T1": np.full(2, 1.0),
                                    "online_p": np.full(2, 1.0)})
    tl = Timeline(table)
    clock = SimClock(T0, 60, running=False)
    cmap = channel_map.load()
    prov = ModelProvider(tl, cmap, clock, t0=T0, cooldown="2026-06")
    eng = SimEngine(run, GeneratedStack(run.path), tl, cmap, clock, provider=prov)
    eng.autopilot["mw"] = True
    yield eng


def test_magic_queues_the_frequency_and_takes_the_microwaves(engine):
    engine.handle({"cmd": "magic", "sign": "+"})
    assert engine.mw_requests.popleft() == ("frequency", round(28.025 * B, 4))
    assert engine.autopilot["mw"] is False                          # the operator took over
    engine.handle({"cmd": "magic", "sign": "-"})
    kind, f = engine.mw_requests.popleft()
    assert f == pytest.approx(spin1.magic_ratio("-", "irradiated_d_butanol") * B, abs=1e-4)


def test_mw_leg_applies_queued_requests_through_operator_ca(engine):
    class FakeCA:
        def __init__(self):
            self.puts = []

        async def caput(self, pv, value):
            self.puts.append((pv, value))

    ca = FakeCA()
    leg = MicrowaveLeg(engine, ca)
    engine.handle({"cmd": "magic", "sign": "+"})
    asyncio.run(leg.apply_requests())
    assert ca.puts == [(f"{SUP}:OutputFrequency", round(28.025 * B, 4))]
    assert not engine.mw_requests
