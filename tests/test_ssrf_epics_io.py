import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from logic_devices.ssrf_epics_io import (
    ALLOWED_COMMAND_PVS,
    EpicsExecutionDisabled,
    EpicsIO,
    EpicsWriteNotAllowed,
    SMC,
    WGEN,
    NMR_SW,
    SSRF_SW,
)


class TestEpicsIO(unittest.TestCase):
    def test_default_execution_disabled(self):
        io = EpicsIO()

        self.assertFalse(
            io.execution_enabled
        )

    def test_execution_enabled_requires_bool(self):
        with self.assertRaises(
            TypeError
        ):
            EpicsIO(
                execution_enabled="false"
            )

    def test_read_allowed_while_disabled(self):
        async def run():
            io = EpicsIO()

            with patch(
                "logic_devices.ssrf_epics_io.aioca.caget",
                new=AsyncMock(
                    return_value=17
                ),
            ) as caget:
                value = await io.read(
                    f"{SMC}:RF_RBV"
                )

                self.assertEqual(
                    value,
                    17,
                )

                caget.assert_awaited_once_with(
                    f"{SMC}:RF_RBV",
                    timeout=2.0,
                )

        asyncio.run(run())

    def test_write_blocked_by_default(self):
        async def run():
            io = EpicsIO()

            with patch(
                "logic_devices.ssrf_epics_io.aioca.caput",
                new=AsyncMock(),
            ) as caput:
                with self.assertRaises(
                    EpicsExecutionDisabled
                ):
                    await io.write(
                        f"{SMC}:RF_CMD",
                        1,
                    )

                caput.assert_not_awaited()

        asyncio.run(run())

    def test_enabled_allowed_write_calls_caput(self):
        async def run():
            io = EpicsIO(
                execution_enabled=True
            )

            with patch(
                "logic_devices.ssrf_epics_io.aioca.caput",
                new=AsyncMock(),
            ) as caput:
                await io.write(
                    f"{SMC}:RF_CMD",
                    0,
                )

                caput.assert_awaited_once_with(
                    f"{SMC}:RF_CMD",
                    0,
                    wait=True,
                    timeout=2.0,
                )

        asyncio.run(run())

    def test_enabled_rejects_unapproved_pv(self):
        async def run():
            io = EpicsIO(
                execution_enabled=True
            )

            with patch(
                "logic_devices.ssrf_epics_io.aioca.caput",
                new=AsyncMock(),
            ) as caput:
                with self.assertRaises(
                    EpicsWriteNotAllowed
                ):
                    await io.write(
                        "UNH:GEN:MW:PS:OUTPUT_CMD",
                        1,
                    )

                caput.assert_not_awaited()

        asyncio.run(run())

    def test_expected_command_pvs_are_allowed(self):
        expected = {
            f"{SMC}:RF_CMD",
            f"{SMC}:FM_CMD",
            f"{SMC}:SWEEP_TRIGGER_CMD",
            f"{WGEN}:OUTPUT_CMD",
            f"{WGEN}:LOAD_CMD",
            f"{NMR_SW}:PORT_CMD",
            f"{SSRF_SW}:PORT_CMD",
        }

        self.assertTrue(
            expected.issubset(
                ALLOWED_COMMAND_PVS
            )
        )

    def test_readback_pvs_not_allowed_for_write(self):
        forbidden = {
            f"{SMC}:RF_RBV",
            f"{SMC}:COMM",
            f"{WGEN}:WAVEFORM_READY",
            f"{NMR_SW}:MASK_RBV",
            f"{SSRF_SW}:MASK_RBV",
        }

        self.assertTrue(
            forbidden.isdisjoint(
                ALLOWED_COMMAND_PVS
            )
        )

    def test_timeout_must_be_positive(self):
        with self.assertRaises(
            ValueError
        ):
            EpicsIO(
                timeout_s=0
            )

    def test_sleep(self):
        async def run():
            io = EpicsIO()

            with patch(
                "logic_devices.ssrf_epics_io.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep:
                await io.sleep(
                    0.125
                )

                sleep.assert_awaited_once_with(
                    0.125
                )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
