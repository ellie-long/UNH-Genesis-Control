import asyncio
import unittest

from logic_devices.ssrf_sequence_executor import (
    SequenceExecutor,
    STATE_ABORTED,
)
from logic_devices.ssrf_sequence_plan import (
    OP_BEST_EFFORT_WRITE,
    OP_WRITE_VERIFY,
    SequenceStep,
)


class TestAbortBoundary(unittest.TestCase):

    def test_abort_blocks_next_nominal_write(self):
        asyncio.run(
            self._run_abort_boundary_test()
        )

    async def _run_abort_boundary_test(self):
        state = {
            "PREP_RBV": 0,
            "ENERGIZE_RBV": 0,
        }

        writes = []
        abort = {
            "requested": False,
        }

        async def read(pv):
            return state[pv]

        async def write(pv, value):
            writes.append(
                (pv, value)
            )

            if pv == "PREP_CMD":
                state["PREP_RBV"] = value

                # Simulate operator ABORT becoming visible
                # immediately after the current nominal step
                # has issued its command.
                abort["requested"] = True

            elif pv == "ENERGIZE_CMD":
                state["ENERGIZE_RBV"] = value

        async def sleep(seconds):
            await asyncio.sleep(0)

        async def abort_requested():
            return abort["requested"]

        executor = SequenceExecutor(
            read=read,
            write=write,
            sleep=sleep,
            abort_requested=abort_requested,
        )

        plan = (
            SequenceStep(
                name="prepare",
                operation=OP_WRITE_VERIFY,
                command_pv="PREP_CMD",
                command_value=1,
                verify_pv="PREP_RBV",
                verify_rule="eq",
                verify_value=1,
            ),
            SequenceStep(
                name="energize",
                operation=OP_WRITE_VERIFY,
                command_pv="ENERGIZE_CMD",
                command_value=1,
                verify_pv="ENERGIZE_RBV",
                verify_rule="eq",
                verify_value=1,
            ),
            SequenceStep(
                name="cleanup_output_off",
                operation=OP_BEST_EFFORT_WRITE,
                command_pv="CLEANUP_CMD",
                command_value=0,
            ),
        )

        result = await executor.run(
            plan
        )

        self.assertEqual(
            result.state,
            STATE_ABORTED,
        )

        self.assertTrue(
            result.cleanup_attempted
        )

        self.assertTrue(
            result.cleanup_ok
        )

        # Critical property:
        # no new nominal energizing command after ABORT.
        self.assertNotIn(
            ("ENERGIZE_CMD", 1),
            writes,
        )

        # Cleanup must still run.
        self.assertIn(
            ("CLEANUP_CMD", 0),
            writes,
        )


if __name__ == "__main__":
    unittest.main()
