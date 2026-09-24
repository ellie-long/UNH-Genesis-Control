import asyncio
import unittest

from logic_devices.ssrf_sequence_core import (
    BurnParameters,
)
from logic_devices.ssrf_sequence_executor import (
    STATE_ABORTED,
    STATE_CLEANUP_FAILED,
    STATE_COMPLETE,
    STATE_FAILED,
    SequenceExecutor,
)
from logic_devices.ssrf_sequence_plan import (
    ILK,
    NMR_SW,
    SMC,
    SSRF_SW,
    WGEN,
    build_burn_plan,
    build_cleanup_plan,
)


def default_parameters():
    return BurnParameters(
        carrier_mhz=33.0,
        start_level_dbm=-120.0,
        target_level_dbm=-22.0,
        fm_deviation_khz=10.0,
        fm_period_s=0.010,
        vhigh_v=1.0,
        vlow_v=-1.0,
        fm_repeats=10,
    )


class FakeIO:
    def __init__(self):
        self.state = {
            f"{SMC}:FREQ_RBV": 33.0,
            f"{SMC}:LEVEL_RBV": -120.0,
            f"{SMC}:RF_RBV": 0,
            f"{SMC}:FM_RBV": 0,
            f"{SMC}:FM_SOURCE_RBV": 0,
            f"{SMC}:FM_DEV_RBV": 0.0,
            f"{SMC}:SWEEP_START_RBV": -120.0,
            f"{SMC}:SWEEP_STOP_RBV": -120.0,
            f"{SMC}:SWEEP_STEP_RBV": 0.0,
            f"{SMC}:SWEEP_DWELL_RBV": 10.0,
            f"{SMC}:SWEEP_SHAPE_RBV": 1,
            f"{SMC}:SWEEP_ARM_RBV": 0,

            f"{WGEN}:OUTPUT_RBV": 0,
            f"{WGEN}:PERIOD_RBV": 0.010,
            f"{WGEN}:VLOW_RBV": -0.5,
            f"{WGEN}:VHIGH_RBV": 0.5,
            f"{WGEN}:WAVEFORM_POINTS": 321,
            f"{WGEN}:ARB_POINTS_RBV": 0,
            f"{WGEN}:WAVEFORM_READY": 0,
            f"{WGEN}:WAVEFORM_ID": 9,

            f"{NMR_SW}:MASK_RBV": 0,
            f"{SSRF_SW}:MASK_RBV": 3,

            f"{ILK}:NMR_CONNECT_PERMIT": 1,
            f"{ILK}:SSRF_CONNECT_PERMIT": 0,
            f"{ILK}:RF_ENABLE_PERMIT": 0,
        }

        self.writes = []
        self.sleeps = []
        self.fail_writes = set()

        self.ready = True
        self.ready_reason = "burn ready"

        self.abort_flag = False
        self.abort_after_trigger = False
        self.drop_permit_after_trigger = False

        self._update_permits()

    def _update_permits(self):
        nmr_mask = int(
            self.state[
                f"{NMR_SW}:MASK_RBV"
            ]
        )
        ssrf_mask = int(
            self.state[
                f"{SSRF_SW}:MASK_RBV"
            ]
        )
        rf_on = int(
            self.state[
                f"{SMC}:RF_RBV"
            ]
        )

        self.state[
            f"{ILK}:NMR_CONNECT_PERMIT"
        ] = int(
            rf_on == 0
            and ssrf_mask == 3
        )

        self.state[
            f"{ILK}:SSRF_CONNECT_PERMIT"
        ] = int(
            rf_on == 0
            and nmr_mask == 3
        )

        # Permit remains asserted in both SSRF_READY and BURNING
        # when NMR is protected and the ssRF path is connected.
        self.state[
            f"{ILK}:RF_ENABLE_PERMIT"
        ] = int(
            nmr_mask == 3
            and ssrf_mask == 0
        )

    async def read(self, pv):
        if pv not in self.state:
            raise KeyError(
                f"fake PV missing: {pv}"
            )

        return self.state[pv]

    async def write(self, pv, value):
        self.writes.append(
            (pv, value)
        )

        if pv in self.fail_writes:
            raise OSError(
                f"forced write failure: {pv}"
            )

        direct = {
            f"{SMC}:FREQ_CMD":
                f"{SMC}:FREQ_RBV",
            f"{SMC}:LEVEL_CMD":
                f"{SMC}:LEVEL_RBV",
            f"{SMC}:RF_CMD":
                f"{SMC}:RF_RBV",
            f"{SMC}:FM_CMD":
                f"{SMC}:FM_RBV",
            f"{SMC}:FM_SOURCE_CMD":
                f"{SMC}:FM_SOURCE_RBV",
            f"{SMC}:FM_DEV_CMD":
                f"{SMC}:FM_DEV_RBV",
            f"{SMC}:SWEEP_START_CMD":
                f"{SMC}:SWEEP_START_RBV",
            f"{SMC}:SWEEP_STOP_CMD":
                f"{SMC}:SWEEP_STOP_RBV",
            f"{SMC}:SWEEP_STEP_CMD":
                f"{SMC}:SWEEP_STEP_RBV",
            f"{SMC}:SWEEP_DWELL_CMD":
                f"{SMC}:SWEEP_DWELL_RBV",
            f"{SMC}:SWEEP_SHAPE_CMD":
                f"{SMC}:SWEEP_SHAPE_RBV",
            f"{SMC}:SWEEP_ARM_CMD":
                f"{SMC}:SWEEP_ARM_RBV",

            f"{WGEN}:OUTPUT_CMD":
                f"{WGEN}:OUTPUT_RBV",
            f"{WGEN}:PERIOD_CMD":
                f"{WGEN}:PERIOD_RBV",
            f"{WGEN}:VLOW_CMD":
                f"{WGEN}:VLOW_RBV",
            f"{WGEN}:VHIGH_CMD":
                f"{WGEN}:VHIGH_RBV",
        }

        if pv in direct:
            self.state[
                direct[pv]
            ] = value

        elif pv == f"{WGEN}:LOAD_CMD":
            if int(value) == 1:
                self.state[
                    f"{WGEN}:WAVEFORM_READY"
                ] = 1

                self.state[
                    f"{WGEN}:ARB_POINTS_RBV"
                ] = int(
                    self.state[
                        f"{WGEN}:WAVEFORM_POINTS"
                    ]
                )

                self.state[
                    f"{WGEN}:WAVEFORM_ID"
                ] += 1

        elif pv == f"{NMR_SW}:PORT_CMD":
            self.state[
                f"{NMR_SW}:MASK_RBV"
            ] = (
                0
                if int(value) == 0
                else 3
            )

        elif pv == f"{SSRF_SW}:PORT_CMD":
            self.state[
                f"{SSRF_SW}:MASK_RBV"
            ] = (
                0
                if int(value) == 0
                else 3
            )

        elif pv == f"{SMC}:SWEEP_TRIGGER_CMD":
            if int(value) != 1:
                raise ValueError(
                    "trigger must be momentary 1"
                )

            if self.abort_after_trigger:
                self.abort_flag = True

        else:
            raise KeyError(
                f"unsupported fake write: {pv}"
            )

        self._update_permits()

        if (
            pv
            == f"{SMC}:SWEEP_TRIGGER_CMD"
            and self.drop_permit_after_trigger
        ):
            self.state[
                f"{ILK}:RF_ENABLE_PERMIT"
            ] = 0

    async def sleep(self, seconds):
        self.sleeps.append(
            float(seconds)
        )

    async def readiness(self):
        return (
            self.ready,
            self.ready_reason,
        )

    async def abort_requested(self):
        return self.abort_flag


def make_executor(io):
    return SequenceExecutor(
        read=io.read,
        write=io.write,
        sleep=io.sleep,
        readiness_check=io.readiness,
        abort_requested=io.abort_requested,
        verify_timeout_s=0.2,
        verify_interval_s=0.01,
        monitor_interval_s=0.02,
    )


def make_plan():
    return build_burn_plan(
        default_parameters(),
        waveform_points=321,
        waveform_id_before=9,
    )


class TestSequenceExecutor(unittest.TestCase):
    def test_nominal_burn_and_cleanup(self):
        async def run():
            io = FakeIO()
            ex = make_executor(io)

            result = await ex.run(
                make_plan()
            )

            self.assertTrue(result.ok)
            self.assertEqual(
                result.state,
                STATE_COMPLETE,
            )
            self.assertTrue(
                result.cleanup_ok
            )

            self.assertEqual(
                io.state[
                    f"{SMC}:RF_RBV"
                ],
                0,
            )
            self.assertEqual(
                io.state[
                    f"{SMC}:FM_RBV"
                ],
                0,
            )
            self.assertEqual(
                io.state[
                    f"{SMC}:SWEEP_ARM_RBV"
                ],
                0,
            )
            self.assertEqual(
                io.state[
                    f"{WGEN}:OUTPUT_RBV"
                ],
                0,
            )
            self.assertEqual(
                io.state[
                    f"{SSRF_SW}:MASK_RBV"
                ],
                3,
            )
            self.assertEqual(
                io.state[
                    f"{NMR_SW}:MASK_RBV"
                ],
                0,
            )

            self.assertIn(
                (
                    f"{SMC}:SWEEP_TRIGGER_CMD",
                    1,
                ),
                io.writes,
            )

        asyncio.run(run())

    def test_waveform_load_advances_id(self):
        async def run():
            io = FakeIO()
            ex = make_executor(io)

            result = await ex.run(
                make_plan()
            )

            self.assertTrue(result.ok)

            self.assertEqual(
                io.state[
                    f"{WGEN}:WAVEFORM_READY"
                ],
                1,
            )
            self.assertEqual(
                io.state[
                    f"{WGEN}:ARB_POINTS_RBV"
                ],
                321,
            )
            self.assertGreater(
                io.state[
                    f"{WGEN}:WAVEFORM_ID"
                ],
                9,
            )

        asyncio.run(run())

    def test_voltage_window_low_first(self):
        async def run():
            io = FakeIO()

            io.state[
                f"{WGEN}:VLOW_RBV"
            ] = 0.0
            io.state[
                f"{WGEN}:VHIGH_RBV"
            ] = 1.0

            ex = make_executor(io)

            await ex._safe_voltage_window(
                -2.0,
                -1.0,
            )

            voltage_writes = [
                item
                for item in io.writes
                if item[0] in (
                    f"{WGEN}:VLOW_CMD",
                    f"{WGEN}:VHIGH_CMD",
                )
            ]

            self.assertEqual(
                voltage_writes[0],
                (
                    f"{WGEN}:VLOW_CMD",
                    -2.0,
                ),
            )

        asyncio.run(run())

    def test_voltage_window_high_first(self):
        async def run():
            io = FakeIO()

            io.state[
                f"{WGEN}:VLOW_RBV"
            ] = -1.0
            io.state[
                f"{WGEN}:VHIGH_RBV"
            ] = 0.0

            ex = make_executor(io)

            await ex._safe_voltage_window(
                0.5,
                1.0,
            )

            voltage_writes = [
                item
                for item in io.writes
                if item[0] in (
                    f"{WGEN}:VLOW_CMD",
                    f"{WGEN}:VHIGH_CMD",
                )
            ]

            self.assertEqual(
                voltage_writes[0],
                (
                    f"{WGEN}:VHIGH_CMD",
                    1.0,
                ),
            )

        asyncio.run(run())

    def test_readiness_failure_causes_no_writes(self):
        async def run():
            io = FakeIO()

            io.ready = False
            io.ready_reason = (
                "writes disabled"
            )

            ex = make_executor(io)

            result = await ex.run(
                make_plan()
            )

            self.assertFalse(result.ok)
            self.assertEqual(
                result.state,
                STATE_FAILED,
            )
            self.assertFalse(
                result.cleanup_attempted
            )
            self.assertEqual(
                io.writes,
                [],
            )

        asyncio.run(run())

    def test_trigger_failure_runs_cleanup(self):
        async def run():
            io = FakeIO()

            io.fail_writes.add(
                f"{SMC}:SWEEP_TRIGGER_CMD"
            )

            ex = make_executor(io)

            result = await ex.run(
                make_plan()
            )

            self.assertFalse(result.ok)
            self.assertEqual(
                result.state,
                STATE_FAILED,
            )
            self.assertTrue(
                result.cleanup_attempted
            )
            self.assertTrue(
                result.cleanup_ok
            )

            self.assertEqual(
                io.state[
                    f"{SMC}:RF_RBV"
                ],
                0,
            )
            self.assertEqual(
                io.state[
                    f"{SSRF_SW}:MASK_RBV"
                ],
                3,
            )
            self.assertEqual(
                io.state[
                    f"{NMR_SW}:MASK_RBV"
                ],
                0,
            )

        asyncio.run(run())

    def test_permit_loss_after_trigger_runs_cleanup(self):
        async def run():
            io = FakeIO()

            io.drop_permit_after_trigger = True

            ex = make_executor(io)

            result = await ex.run(
                make_plan()
            )

            self.assertFalse(result.ok)
            self.assertTrue(
                result.cleanup_attempted
            )
            self.assertTrue(
                result.cleanup_ok
            )

            self.assertEqual(
                io.state[
                    f"{SMC}:RF_RBV"
                ],
                0,
            )

        asyncio.run(run())

    def test_abort_after_trigger_runs_cleanup(self):
        async def run():
            io = FakeIO()

            io.abort_after_trigger = True

            ex = make_executor(io)

            result = await ex.run(
                make_plan()
            )

            self.assertFalse(result.ok)
            self.assertEqual(
                result.state,
                STATE_ABORTED,
            )
            self.assertTrue(
                result.cleanup_ok
            )

            self.assertEqual(
                io.state[
                    f"{SSRF_SW}:MASK_RBV"
                ],
                3,
            )
            self.assertEqual(
                io.state[
                    f"{NMR_SW}:MASK_RBV"
                ],
                0,
            )

        asyncio.run(run())

    def test_cleanup_attempts_all_off_commands(self):
        async def run():
            io = FakeIO()

            io.state[
                f"{NMR_SW}:MASK_RBV"
            ] = 3
            io.state[
                f"{SSRF_SW}:MASK_RBV"
            ] = 0
            io.state[
                f"{SMC}:RF_RBV"
            ] = 1
            io.state[
                f"{SMC}:FM_RBV"
            ] = 1
            io.state[
                f"{SMC}:SWEEP_ARM_RBV"
            ] = 1
            io.state[
                f"{WGEN}:OUTPUT_RBV"
            ] = 1

            io._update_permits()

            io.fail_writes.add(
                f"{SMC}:SWEEP_ARM_CMD"
            )

            ex = make_executor(io)

            ok, _ = await ex._run_cleanup(
                build_cleanup_plan()
            )

            self.assertFalse(ok)

            attempted = [
                pv
                for pv, value in io.writes
                if value == 0
            ]

            self.assertIn(
                f"{SMC}:SWEEP_ARM_CMD",
                attempted,
            )
            self.assertIn(
                f"{SMC}:RF_CMD",
                attempted,
            )
            self.assertIn(
                f"{SMC}:FM_CMD",
                attempted,
            )
            self.assertIn(
                f"{WGEN}:OUTPUT_CMD",
                attempted,
            )

        asyncio.run(run())

    def test_cleanup_barrier_blocks_switch_motion(self):
        async def run():
            io = FakeIO()

            io.state[
                f"{NMR_SW}:MASK_RBV"
            ] = 3
            io.state[
                f"{SSRF_SW}:MASK_RBV"
            ] = 0
            io.state[
                f"{SMC}:RF_RBV"
            ] = 1

            io._update_permits()

            io.fail_writes.add(
                f"{SMC}:RF_CMD"
            )

            ex = make_executor(io)

            ok, _ = await ex._run_cleanup(
                build_cleanup_plan()
            )

            self.assertFalse(ok)

            self.assertNotIn(
                (
                    f"{SSRF_SW}:PORT_CMD",
                    1,
                ),
                io.writes,
            )

            self.assertNotIn(
                (
                    f"{NMR_SW}:PORT_CMD",
                    0,
                ),
                io.writes,
            )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
