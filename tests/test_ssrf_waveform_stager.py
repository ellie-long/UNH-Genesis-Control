import asyncio
import unittest

import numpy as np

from ssrf_analysis.waveform_stager import (
    ALLOWED_STAGE_PVS,
    WAVEFORM_CMD_PV,
    WaveformStageError,
    WaveformStager,
)


class DummyPlan:

    def __init__(
        self,
        samples=None,
    ):
        if samples is None:
            samples = np.linspace(
                -1.0,
                1.0,
                8192,
                dtype=np.float32,
            )

        self.normalized_samples = (
            np.asarray(
                samples,
                dtype=np.float32,
            )
        )

        self.material = "ND3"
        self.case = "Enhancement"
        self.fit_ts = "test"

    def validate(self):
        return None


class InvalidPlan(
    DummyPlan
):

    def validate(self):
        raise ValueError(
            "invalid plan"
        )


class TestWaveformStager(
    unittest.TestCase
):

    def test_allowlist_contains_only_memory_stage_pv(
        self
    ):

        self.assertEqual(
            ALLOWED_STAGE_PVS,
            frozenset(
                {
                    WAVEFORM_CMD_PV,
                }
            ),
        )

    def test_stage_writes_only_waveform_cmd(
        self
    ):

        writes = []

        async def writer(
            pv,
            value,
        ):
            writes.append(
                (
                    pv,
                    np.asarray(
                        value
                    ).copy(),
                )
            )

        stager = WaveformStager(
            write=writer,
        )

        receipt = asyncio.run(
            stager.stage(
                DummyPlan()
            )
        )

        self.assertEqual(
            len(writes),
            1,
        )

        self.assertEqual(
            writes[0][0],
            WAVEFORM_CMD_PV,
        )

        self.assertEqual(
            writes[0][1].size,
            8192,
        )

        self.assertEqual(
            receipt.points,
            8192,
        )

    def test_invalid_plan_never_calls_writer(
        self
    ):

        writes = []

        async def writer(
            pv,
            value,
        ):
            writes.append(
                (
                    pv,
                    value,
                )
            )

        stager = WaveformStager(
            write=writer,
        )

        with self.assertRaises(
            ValueError
        ):
            asyncio.run(
                stager.stage(
                    InvalidPlan()
                )
            )

        self.assertEqual(
            writes,
            [],
        )

    def test_wrong_point_count_is_rejected(
        self
    ):

        writes = []

        async def writer(
            pv,
            value,
        ):
            writes.append(
                (
                    pv,
                    value,
                )
            )

        stager = WaveformStager(
            write=writer,
        )

        with self.assertRaises(
            WaveformStageError
        ):
            asyncio.run(
                stager.stage(
                    DummyPlan(
                        samples=np.zeros(
                            100,
                            dtype=np.float32,
                        )
                    )
                )
            )

        self.assertEqual(
            writes,
            [],
        )

    def test_out_of_range_samples_are_rejected(
        self
    ):

        values = np.zeros(
            8192,
            dtype=np.float32,
        )

        values[100] = 1.1

        stager = WaveformStager(
            write=lambda pv, value: None,
        )

        with self.assertRaises(
            WaveformStageError
        ):
            asyncio.run(
                stager.stage(
                    DummyPlan(
                        values
                    )
                )
            )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
