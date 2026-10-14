import pytest

from cooldown_sim.clock import MAX_SPEED, SimClock, discontinuities, wall_to_sim


class Wall:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_runs_at_speed():
    wall = Wall()
    clock = SimClock(5_000.0, speed=60, wall=wall)
    wall.t += 2.0
    assert clock.now() == pytest.approx(5_120.0)


def test_pause_freezes_and_play_resumes():
    wall = Wall()
    clock = SimClock(0.0, speed=10, wall=wall)
    wall.t += 1
    clock.pause()
    wall.t += 100
    assert clock.now() == pytest.approx(10.0)
    clock.play()
    wall.t += 1
    assert clock.now() == pytest.approx(20.0)


def test_speed_change_is_continuous():
    wall = Wall()
    clock = SimClock(0.0, speed=1, wall=wall)
    wall.t += 10
    clock.set_speed(100)
    assert clock.now() == pytest.approx(10.0)
    wall.t += 1
    assert clock.now() == pytest.approx(110.0)


def test_seek_is_a_recorded_discontinuity():
    wall = Wall()
    clock = SimClock(0.0, speed=2, wall=wall)
    wall.t += 5
    clock.seek(1e9)
    wall.t += 1
    assert clock.now() == pytest.approx(1e9 + 2)
    seeks = discontinuities(clock.checkpoints())
    assert len(seeks) == 1 and seeks[0].sim == 1e9


def test_wall_to_sim_is_piecewise():
    wall = Wall(0.0)
    clock = SimClock(100.0, speed=10, wall=wall)
    wall.t = 5
    clock.pause()
    wall.t = 20
    clock.play()
    cps = clock.checkpoints()
    assert wall_to_sim(cps, 2) == pytest.approx(120.0)     # running
    assert wall_to_sim(cps, 10) == pytest.approx(150.0)    # paused
    assert wall_to_sim(cps, 21) == pytest.approx(160.0)    # resumed
    assert clock.wall_to_sim(21) == pytest.approx(160.0)


@pytest.mark.parametrize("bad", [0, -1, MAX_SPEED * 2, float("nan")])
def test_rejects_bad_speed(bad):
    with pytest.raises(ValueError):
        SimClock(0.0, speed=bad, wall=Wall())
