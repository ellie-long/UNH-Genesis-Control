"""Fake-only manual operation, IOC arbitration and partial-stack shutdown."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import yaml

from logic_devices.ssrf_manual_rf import ManualRF
from logic_devices.ssrf_sequence_core import BurnParameters
from logic_devices.ssrf_sequence_plan import SMC, WGEN, NMR_SW, SSRF_SW, ILK, build_burn_plan
from logic_devices.ssrf_sequencer import Device
from logic_devices.ssrf_shutdown import establish_safe_shutdown


class IO:
    execution_enabled = True

    def __init__(self):
        self.values = {}
        self.writes = []
        self.fail = set()
        self.stuck = set()
        self.abort = False
        self.abort_after = None
        for prefix in (SMC, WGEN, NMR_SW, SSRF_SW):
            self.values[f"{prefix}:COMM"] = 1
            self.values[f"{prefix}:WRITE_ENABLED"] = 1
        self.values.update({
            f"{SMC}:RF_RBV": 0, f"{SMC}:FM_RBV": 0,
            f"{SMC}:SWEEP_ARM_RBV": 0, f"{WGEN}:OUTPUT_RBV": 0,
            f"{NMR_SW}:MASK_RBV": 0, f"{SSRF_SW}:MASK_RBV": 3,
            f"{ILK}:INPUTS_OK": 1, f"{ILK}:INTERLOCK_OK": 1,
            f"{SMC}:FM_SOURCE_RBV": 1, f"{SMC}:FM_DEV_RBV": 10.,
            f"{WGEN}:PERIOD_RBV": .01, f"{WGEN}:VHIGH_RBV": .7,
            f"{WGEN}:VLOW_RBV": -.7, f"{WGEN}:WAVEFORM_READY": 1,
            f"{WGEN}:WAVEFORM_ID": 5, f"{WGEN}:ARB_POINTS_RBV": 8192,
        })

    async def read(self, pv):
        if pv in self.fail:
            raise RuntimeError(f"unavailable {pv}")
        if pv in self.values:
            return self.values[pv]
        rf = self.values[f"{SMC}:RF_RBV"]
        nmr = self.values[f"{NMR_SW}:MASK_RBV"]
        ssrf = self.values[f"{SSRF_SW}:MASK_RBV"]
        if pv == f"{ILK}:RF_ENABLE_PERMIT":
            return int(nmr == 3 and ssrf == 0)
        if pv == f"{ILK}:SSRF_CONNECT_PERMIT":
            return int(nmr == 3 and rf == 0)
        if pv == f"{ILK}:NMR_CONNECT_PERMIT":
            return int(ssrf == 3 and rf == 0)
        raise RuntimeError(f"unavailable {pv}")

    async def read_safety(self, pv):
        value = await self.read(pv)
        if getattr(value, "severity", 0):
            raise RuntimeError(f"{pv}: alarmed")
        return value

    async def write(self, pv, value):
        self.writes.append((pv, value))
        if pv in self.fail:
            raise RuntimeError(f"failed {pv}")
        if pv not in self.stuck:
            rbv = pv.replace('_CMD', '_RBV')
            if pv.endswith(':PORT_CMD'):
                rbv = pv.replace('PORT_CMD', 'MASK_RBV')
                value = 3 if value else 0
            self.values[rbv] = value
        if pv == self.abort_after:
            self.abort = True

    async def sleep(self, _seconds):
        await asyncio.sleep(0)


class PV:
    def __init__(self, value=0): self.value = value
    def get(self): return self.value
    def set(self, value, **_kw): self.value = value


class Builder:
    def __getattr__(self, _name):
        def record(*args, **kw):
            pv = PV(kw.get('initial_value', 0))
            pv.metadata = kw
            return pv
        return record


def device(io, **settings):
    with patch('logic_devices.ssrf_sequencer.builder', Builder()):
        d = Device('UNH:GEN:SSRF:SEQ', {
            'execution_enabled': True, 'execution_mode': 'simulator',
            'require_prepared': True, **settings,
        })
    d._execution_io = io
    d._manual = ManualRF(io, d._executor_abort_requested)
    d._manual.executor.verify_timeout_s = .001
    return d


class ManualTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lock_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.lock_tmp.cleanup)
        self.lock_patch = patch('logic_devices.ssrf_lifecycle.DEFAULT_LOCK_DIR', Path(self.lock_tmp.name))
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.io = IO()
        self.m = ManualRF(self.io, lambda: self.io.abort)
        self.m.executor.verify_timeout_s = .001
        self.params = BurnParameters(33., -120., -22., 10., .01, .7, -.7, 10)

    async def on(self):
        await self.m.arm()
        await self.m.rf_on(33., -22.)

    async def test_no_analysis_dependencies(self):
        for case in ('absent', 'stale', 'uncalibrated', 'no_recipe', 'no_waveform'):
            with self.subTest(case=case):
                self.io.values[f"{WGEN}:WAVEFORM_READY"] = 0
                await self.on()
                self.assertEqual(self.m.state, 'RF_ON')
                self.assertEqual(await self.io.read(f"{SMC}:FM_RBV"), 0)
                self.assertEqual(await self.io.read(f"{WGEN}:OUTPUT_RBV"), 0)
                self.assertEqual(await self.io.read(f"{SMC}:SWEEP_ARM_RBV"), 0)
                self.assertTrue(await self.m.safe_off())

    async def test_unarmed_never_writes(self):
        with self.assertRaises(RuntimeError): await self.m.rf_on(33., -22.)
        self.assertEqual(self.io.writes, [])

    async def test_hard_gates_fail_closed(self):
        for pv in (f"{SMC}:COMM", f"{SMC}:WRITE_ENABLED", f"{NMR_SW}:COMM",
                   f"{SSRF_SW}:COMM", f"{ILK}:INPUTS_OK", f"{ILK}:INTERLOCK_OK"):
            with self.subTest(pv=pv):
                self.io.values[pv] = 0
                with self.assertRaises(RuntimeError): await self.m.arm()
                self.assertEqual(self.io.writes, [])
                self.io.values[pv] = 1

    async def test_invalid_frequency_and_level(self):
        for freq, level in ((float('nan'), -22.), (33., 11.)):
            await self.m.arm()
            with self.assertRaises(ValueError): await self.m.rf_on(freq, level)
            self.assertEqual(self.io.writes, [])

    async def test_order_and_permit(self):
        await self.on()
        writes = self.io.writes
        self.assertLess(writes.index((f"{NMR_SW}:PORT_CMD", 1)), writes.index((f"{SSRF_SW}:PORT_CMD", 0)))
        self.assertLess(writes.index((f"{SSRF_SW}:PORT_CMD", 0)), writes.index((f"{SMC}:RF_CMD", 1)))
        await self.m.safe_off()
        self.io.values[f"{ILK}:RF_ENABLE_PERMIT"] = 0
        await self.m.arm()
        self.io.writes.clear()
        with self.assertRaises(RuntimeError): await self.m.rf_on(33., -22.)
        self.assertNotIn((f"{SMC}:RF_CMD", 1), self.io.writes)

    async def test_rf_readback_verified(self):
        self.io.stuck.add(f"{SMC}:RF_CMD")
        await self.m.arm()
        with self.assertRaises(RuntimeError): await self.m.rf_on(33., -22.)
        self.assertNotEqual(self.m.state, 'RF_ON')

    async def test_optional_fm_no_calibration(self):
        await self.on()
        await self.m.fm_on(self.params, 5)
        self.assertEqual(self.m.state, 'RF_FM_ACTIVE')
        self.assertFalse(any('TRIGGER' in pv for pv, _ in self.io.writes))
        await self.m.monitor()
        await self.m.fm_off()
        await self.m.monitor()
        self.assertEqual(self.m.state, 'RF_ON')

    async def test_fm_configuration_required(self):
        await self.on()
        self.io.values[f"{WGEN}:WAVEFORM_READY"] = 0
        with self.assertRaises(RuntimeError): await self.m.fm_on(self.params, 5)
        self.assertNotIn((f"{SMC}:FM_CMD", 1), self.io.writes)
        self.assertNotIn((f"{WGEN}:OUTPUT_CMD", 1), self.io.writes)

    async def test_off_attempts_all_and_barrier(self):
        await self.on()
        self.io.fail.add(f"{SMC}:RF_CMD")
        self.io.writes.clear()
        self.assertFalse(await self.m.safe_off())
        for pv in (f"{SMC}:SWEEP_ARM_CMD", f"{SMC}:RF_CMD", f"{SMC}:FM_CMD", f"{WGEN}:OUTPUT_CMD"):
            self.assertIn((pv, 0), self.io.writes)
        self.assertFalse(any('PORT_CMD' in pv for pv, _ in self.io.writes))
        self.assertEqual(self.m.state, 'CLEANUP_FAILED')

    async def test_idle_off_establishes_baseline(self):
        self.io.values[f"{SMC}:RF_RBV"] = 1
        self.io.values[f"{SMC}:FM_RBV"] = 1
        self.io.values[f"{WGEN}:OUTPUT_RBV"] = 1
        self.assertTrue(await self.m.safe_off())
        self.assertEqual(await self.io.read(f"{SSRF_SW}:MASK_RBV"), 3)
        self.assertEqual(await self.io.read(f"{NMR_SW}:MASK_RBV"), 0)
        self.assertFalse(self.m.armed)

    async def test_abort_at_each_transition(self):
        for boundary in (f"{SMC}:FREQ_CMD", f"{SMC}:LEVEL_CMD", f"{NMR_SW}:PORT_CMD", f"{SSRF_SW}:PORT_CMD"):
            self.io.abort = False
            self.io.abort_after = boundary
            await self.m.arm()
            self.io.writes.clear()
            with self.assertRaises(RuntimeError): await self.m.rf_on(33., -22.)
            self.assertNotIn((f"{SMC}:RF_CMD", 1), self.io.writes)
            self.io.abort = False
            self.io.abort_after = None
            await self.m.safe_off()

    async def test_shutdown_without_sequencer(self):
        await self.on()
        await establish_safe_shutdown(self.io)
        self.assertEqual(await self.io.read(f"{SMC}:RF_RBV"), 0)
        self.assertEqual(await self.io.read(f"{NMR_SW}:MASK_RBV"), 0)

    async def test_partial_shutdown_refuses_unverified(self):
        self.io.fail.add(f"{WGEN}:OUTPUT_RBV")
        with self.assertRaisesRegex(RuntimeError, 'OUTPUT_RBV'):
            await establish_safe_shutdown(self.io)
        self.assertFalse(any('PORT_CMD' in pv for pv, _ in self.io.writes))

    async def test_ioc_idle_abort_and_rf_off(self):
        for cmd in ('ABORT_CMD', 'RF_OFF_CMD'):
            d = device(self.io)
            if cmd == 'ABORT_CMD': d._execution_command(1, d.device_name+':'+cmd)
            else: d._manual_command(1, d.device_name+':'+cmd)
            self.io.writes.clear()
            await d._service_manual()
            self.assertIn((f"{SMC}:RF_CMD", 0), self.io.writes)
            self.assertEqual(d._manual.state, 'DISARMED')
            self.assertFalse(d._execution_armed)

    async def test_ioc_monitor_fault_no_resume(self):
        for fm in (False, True):
            d = device(self.io)
            await d._manual.arm()
            await d._manual.rf_on(33., -22.)
            if fm: await d._manual.fm_on(self.params, 5)
            self.io.values[f"{SMC}:COMM"] = 0
            await d._service_manual()
            self.assertFalse(d._safe_off_requested)
            self.assertEqual(d._manual.state, "DISARMED")
            self.assertFalse(d._manual.armed)
            self.io.values[f"{SMC}:COMM"] = 1
            self.io.writes.clear()
            await d._service_manual()
            self.assertNotIn((f"{SMC}:RF_CMD", 1), self.io.writes)

    async def test_conflict_arbitration(self):
        d = device(self.io)
        await d._manual.arm()
        d._prepare_command(1, d.device_name+':PREPARE_CMD')
        d._execution_command(1, d.device_name+':START_CMD')
        d._waveform_stage_command([0]*8192, d.device_name+':WAVEFORM_STAGE_REQ')
        self.assertFalse(d._prepare_requested)
        self.assertFalse(d._start_requested)
        self.assertFalse(d._waveform_stage_requested)
        d._manual.armed = False
        d._prepare_requested = True
        d._manual_command(1, d.device_name+':MANUAL_ARM_CMD')
        self.assertIsNone(d._manual_request)

    async def test_restart_starts_disarmed_no_writes(self):
        d = device(self.io)
        d.connect()
        self.assertFalse(d._manual.armed)
        self.assertFalse(d._execution_armed)
        self.assertFalse(d._prepared)
        self.assertEqual(self.io.writes, [])

    async def test_fm_runtime_configuration_loss(self):
        await self.on()
        await self.m.fm_on(self.params, 5)
        self.io.values[f"{SMC}:FM_SOURCE_RBV"] = 0
        with self.assertRaises(RuntimeError): await self.m.monitor()

    async def test_abort_during_fm_blocks_fm_energization(self):
        await self.on()
        self.io.abort_after = f"{WGEN}:OUTPUT_CMD"
        with self.assertRaises(RuntimeError): await self.m.fm_on(self.params, 5)
        self.assertNotIn((f"{SMC}:FM_CMD", 1), self.io.writes)

    async def test_reachable_sequencer_requires_fresh_ack(self):
        seq = "UNH:GEN:SSRF:SEQ"
        self.io.values.update({f"{seq}:EXECUTION_ENABLED": 1,
                              f"{seq}:SAFE_OFF_ID": 2,
                              f"{seq}:MANUAL_STATE": "DISARMED",
                              f"{seq}:MANUAL_REASON": "Safe baseline verified"})
        calls = []
        async def stale_abort(): calls.append(1)
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            await establish_safe_shutdown(self.io, timeout_s=0, request_abort=stale_abort)
        self.assertEqual(calls, [1])
        async def fresh_abort():
            await self.m.safe_off()
            self.io.values[f"{seq}:SAFE_OFF_ID"] = 3
        await establish_safe_shutdown(self.io, request_abort=fresh_abort)

    async def test_ioc_abort_before_queued_rf_on(self):
        d = device(self.io)
        await d._manual.arm()
        d._manual_command(1, d.device_name+':RF_ON_CMD')
        d._execution_command(1, d.device_name+':ABORT_CMD')
        await d._service_manual()
        self.assertNotIn((f"{SMC}:RF_CMD", 1), self.io.writes)

    async def test_ioc_manual_command_dispatch(self):
        d = device(self.io)
        for command, expected in (('MANUAL_ARM_CMD', 'ARMED'), ('RF_ON_CMD', 'RF_ON')):
            d._manual_command(1, d.device_name+':'+command)
            await d._service_manual()
            task = d._manual_task
            if task: await task
            self.assertEqual(d._manual.state, expected)

    async def test_ioc_manual_level_defaults_and_metadata(self):
        d = device(self.io)
        self.assertEqual(d.pvs["MANUAL_LEVEL_DBM"].get(), -120.0)
        self.assertEqual(d.pvs["START_LEVEL_DBM"].get(), -120.0)
        self.assertEqual(d.pvs["TARGET_LEVEL_DBM"].get(), -22.0)
        self.assertEqual(d.pvs["MANUAL_LEVEL_DBM"].metadata["EGU"], "dBm")
        self.assertEqual(d.pvs["MANUAL_LEVEL_DBM"].metadata["PREC"], 2)
        configured = device(self.io, manual_level_dbm=-110.0)
        self.assertEqual(configured.pvs["MANUAL_LEVEL_DBM"].get(), -110.0)
        self.assertEqual(configured.pvs["TARGET_LEVEL_DBM"].get(), -22.0)

    async def test_ioc_manual_rf_level_independent_of_burn_target(self):
        d = device(self.io)
        for manual, target in ((-120.0, -22.0), (-120.0, -10.0), (-110.0, -22.0)):
            with self.subTest(manual=manual, target=target):
                d.pvs["MANUAL_LEVEL_DBM"].set(manual)
                d.pvs["TARGET_LEVEL_DBM"].set(target)
                d.pvs["CARRIER_MHZ"].set(34.0)
                self.io.writes.clear()
                for command in ("MANUAL_ARM_CMD", "RF_ON_CMD"):
                    d._manual_command(1, d.device_name + ":" + command)
                    await d._service_manual()
                    if d._manual_task:
                        await d._manual_task
                self.assertEqual(d._manual.state, "RF_ON")
                self.assertEqual(d._manual.level, manual)
                self.assertIn((f"{SMC}:LEVEL_CMD", manual), self.io.writes)
                self.assertNotIn((f"{SMC}:LEVEL_CMD", target), self.io.writes)
                self.assertEqual(self.io.values[f"{SMC}:LEVEL_RBV"], manual)
                self.assertEqual(self.io.values[f"{SMC}:FREQ_RBV"], 34.0)
                self.assertEqual(self.io.values[f"{SMC}:FM_RBV"], 0)
                self.assertEqual(self.io.values[f"{WGEN}:OUTPUT_RBV"], 0)
                d._manual_command(1, d.device_name + ":RF_OFF_CMD")
                await d._service_manual()
                self.assertEqual(d._manual.state, "DISARMED")
                self.assertEqual(self.io.values[f"{SMC}:RF_RBV"], 0)

    async def test_ioc_plain_rf_does_not_read_burn_or_fm_parameters(self):
        d = device(self.io)
        for name in ("START_LEVEL_DBM", "TARGET_LEVEL_DBM", "FM_DEV_KHZ",
                     "FM_PERIOD_S", "VHIGH_V", "VLOW_V", "FM_REPEATS"):
            d.pvs[name].set("invalid burn parameter")
        await d._manual.arm()
        with patch.object(d, "_parameters", side_effect=AssertionError("burn parameters read")):
            await d._run_manual_request("RF_ON_CMD")
        self.assertEqual(d._manual.state, "RF_ON")
        self.assertEqual(self.io.values[f"{SMC}:LEVEL_RBV"], -120.0)

    async def test_manual_level_does_not_change_burn_parameters_or_plan(self):
        d = device(self.io)
        parameters = d._parameters()
        signature = d._parameter_signature()
        plan = build_burn_plan(parameters, waveform_points=8192, waveform_id_before=5)
        d.pvs["MANUAL_LEVEL_DBM"].set(-100.0)
        self.assertEqual(d._parameters(), parameters)
        self.assertEqual(d._parameters().target_level_dbm, -22.0)
        self.assertEqual(d._parameter_signature(), signature)
        self.assertEqual(build_burn_plan(d._parameters(), waveform_points=8192,
                                        waveform_id_before=5), plan)

    async def test_runtime_wavegen_unavailable_disarms(self):
        d = device(self.io)
        await d._manual.arm()
        await d._manual.rf_on(33., -22.)
        self.io.fail.add(f"{WGEN}:OUTPUT_RBV")
        await d._service_manual()
        await d._service_manual()
        self.assertEqual(d._manual.state, 'CLEANUP_FAILED')
        self.assertFalse(d._manual.armed)
        self.assertFalse(d._execution_armed)


    async def test_all_off_failures_independently_attempted(self):
        commands = (f"{SMC}:SWEEP_ARM_CMD", f"{SMC}:RF_CMD", f"{SMC}:FM_CMD", f"{WGEN}:OUTPUT_CMD")
        for failing in commands:
            with self.subTest(failing=failing):
                io = IO()
                m = ManualRF(io, lambda: False)
                m.executor.verify_timeout_s = .001
                for command in commands: io.values[command.replace('_CMD', '_RBV')] = 1
                io.fail.add(failing)
                self.assertFalse(await m.safe_off())
                for command in commands: self.assertIn((command, 0), io.writes)
                self.assertFalse(any('PORT_CMD' in pv for pv, _ in io.writes))

    async def test_alarm_readback_cannot_verify_off(self):
        class AlarmValue(int): severity = 2
        self.io.values[f"{SMC}:RF_RBV"] = AlarmValue(0)
        with self.assertRaises(RuntimeError): await self.m.arm()
        self.io.stuck.add(f"{SMC}:RF_CMD")
        self.assertFalse(await self.m.safe_off())
        self.assertFalse(any('PORT_CMD' in pv for pv, _ in self.io.writes))

    async def test_all_pending_conflicts_reject_manual_commands(self):
        for attribute in ('_start_requested', '_prepare_requested', '_waveform_stage_requested',
                          '_execution_armed', '_safe_off_requested'):
            d = device(self.io)
            setattr(d, attribute, True)
            for command in ('MANUAL_ARM_CMD', 'RF_ON_CMD', 'FM_ON_CMD', 'FM_OFF_CMD'):
                d._manual_command(1, d.device_name+':'+command)
                self.assertIsNone(d._manual_request)
        for attribute in ('_execution_task', '_prepare_task', '_waveform_stage_task'):
            d = device(self.io)
            task = asyncio.create_task(asyncio.sleep(0))
            setattr(d, attribute, task)
            d._manual_command(1, d.device_name+':MANUAL_ARM_CMD')
            self.assertIsNone(d._manual_request)
            await task

    async def test_active_manual_fm_blocks_prepare_stage_and_burn(self):
        d = device(self.io)
        await d._manual.arm()
        await d._manual.rf_on(33., -22.)
        await d._manual.fm_on(self.params, 5)
        d._prepare_command(1, d.device_name+':PREPARE_CMD')
        d._waveform_stage_command([0]*8192, d.device_name+':WAVEFORM_STAGE_REQ')
        d._execution_command(1, d.device_name+':ARM_CMD')
        d._execution_command(1, d.device_name+':START_CMD')
        self.assertFalse(d._prepare_requested)
        self.assertFalse(d._waveform_stage_requested)
        self.assertFalse(d._execution_armed)
        self.assertFalse(d._start_requested)


    async def test_unreachable_detected_sequencer_retains_owners(self):
        with self.assertRaisesRegex(RuntimeError, 'refusing concurrent fallback'):
            await establish_safe_shutdown(self.io, sequencer_expected=True)
        self.assertEqual(self.io.writes, [])


    async def test_disarm_during_fm_transition_prevents_fm_on(self):
        d = device(self.io)
        d.pvs["VHIGH_V"].set(.7)
        d.pvs["VLOW_V"].set(-.7)
        await d._manual.arm()
        await d._manual.rf_on(33., -22.)
        d._set_prepared(True, waveform_id=5, signature=d._parameter_signature())
        write = self.io.write
        async def disarm_after_wavegen(pv, value):
            await write(pv, value)
            if pv == f"{WGEN}:OUTPUT_CMD" and value == 1:
                d._manual_command(1, d.device_name+':MANUAL_DISARM_CMD')
        self.io.write = disarm_after_wavegen
        d._manual.executor.write = disarm_after_wavegen
        d._manual_command(1, d.device_name+':FM_ON_CMD')
        await d._service_manual()
        task = d._manual_task
        await task
        await d._service_manual()
        self.assertNotIn((f"{SMC}:FM_CMD", 1), self.io.writes)
        self.assertFalse(d._manual.armed)
        self.assertEqual(d._manual.state, 'DISARMED')

    async def test_abort_during_actual_rf_transition(self):
        d = device(self.io)
        await d._manual.arm()
        write = self.io.write
        async def abort_after_nmr(pv, value):
            await write(pv, value)
            if pv == f"{NMR_SW}:PORT_CMD" and value == 1:
                d._execution_command(1, d.device_name+':ABORT_CMD')
        d._manual.executor.write = abort_after_nmr
        d._manual_command(1, d.device_name+':RF_ON_CMD')
        await d._service_manual()
        task = d._manual_task
        await task
        await d._service_manual()
        self.assertNotIn((f"{SMC}:RF_CMD", 1), self.io.writes)
        self.assertEqual(d._manual.state, 'DISARMED')

    async def test_repeated_requests_during_cleanup_are_safe(self):
        d = device(self.io)
        await d._manual.arm()
        await d._manual.rf_on(33., -22.)
        write = self.io.write
        async def repeated_off(pv, value):
            await write(pv, value)
            if pv == f"{SMC}:SWEEP_ARM_CMD":
                for command in ('RF_OFF_CMD', 'MANUAL_DISARM_CMD'):
                    d._manual_command(1, d.device_name+':'+command)
                d._execution_command(1, d.device_name+':ABORT_CMD')
                d._manual_command(1, d.device_name+':MANUAL_ARM_CMD')
                d._prepare_command(1, d.device_name+':PREPARE_CMD')
        d._manual.executor.write = repeated_off
        d._manual_command(1, d.device_name+':RF_OFF_CMD')
        self.io.writes.clear()
        await d._service_manual()
        self.assertEqual(d._safe_off_id, 1)
        self.assertIsNone(d._manual_request)
        self.assertFalse(d._prepare_requested)
        self.assertFalse(d._manual.armed)
        self.assertEqual(d._manual.state, 'DISARMED')
        self.assertNotIn((f"{SMC}:RF_CMD", 1), self.io.writes)

    async def test_safety_loss_while_cleanup_runs_blocks_routing(self):
        d = device(self.io)
        write = self.io.write
        async def lose_readback(pv, value):
            await write(pv, value)
            if pv == f"{SMC}:RF_CMD":
                self.io.fail.add(f"{SMC}:RF_RBV")
        d._manual.executor.write = lose_readback
        d._manual_command(1, d.device_name+':RF_OFF_CMD')
        await d._service_manual()
        self.assertEqual(d._manual.state, 'CLEANUP_FAILED')
        self.assertFalse(d._manual.armed)
        for command in (f"{SMC}:SWEEP_ARM_CMD", f"{SMC}:RF_CMD", f"{SMC}:FM_CMD", f"{WGEN}:OUTPUT_CMD"):
            self.assertIn((command, 0), self.io.writes)
        self.assertFalse(any('PORT_CMD' in pv for pv, _ in self.io.writes))



class GuiAndShutdownStatic(unittest.TestCase):
    def test_manual_buttons_use_sequencer(self):
        root = ET.parse('gui/genesis_ssrf.bob').getroot()
        for w in root.findall('widget'):
            if (w.findtext('name') or '').startswith('Manual_'):
                self.assertTrue(w.findtext('actions/action/pv_name').startswith('ca://UNH:GEN:SSRF:SEQ:'))

    def test_manual_level_gui_binding(self):
        root = ET.parse('gui/genesis_ssrf.bob').getroot()
        fields = {w.findtext('name'): w for w in root.findall('widget')}
        self.assertEqual(fields['ManualParamLabel1'].findtext('text'), 'Manual RF level dBm')
        self.assertEqual(fields['ManualParam1'].findtext('pv_name'),
                         'ca://UNH:GEN:SSRF:SEQ:MANUAL_LEVEL_DBM')

    def test_manual_level_profiles_and_archive(self):
        for profile in ('commissioning/ssrf_sim_readonly', 'ssrf_physical', 'commissioning/ssrf_sim_active'):
            with self.subTest(profile=profile):
                config = yaml.safe_load((Path('configs') / profile /
                                         'sequencer/settings.yaml').read_text())['ssrf_sequencer']
                self.assertEqual(config['manual_level_dbm'], -120.0)
                self.assertEqual(config['start_level_dbm'], -120.0)
                self.assertEqual(config['target_level_dbm'], -22.0)
                self.assertIn('MANUAL_LEVEL_DBM', config['archive_records'])
                self.assertEqual(config['records']['MANUAL_LEVEL_DBM']['DESC'],
                                 'SMC plain manual RF level')
        archive = Path('configs/ssrf_physical/archiver/settings.yaml').read_text()
        self.assertIn('UNH:GEN:SSRF:SEQ:MANUAL_LEVEL_DBM', archive)

    def test_expert_output_route_controls_disabled(self):
        for filename, suffixes in (
            ('genesis_ssrf_smc100a.bob', ('RF_CMD','FM_CMD','SWEEP_ARM_CMD','SWEEP_TRIGGER_CMD')),
            ('genesis_ssrf_wavegen.bob', ('OUTPUT_CMD','LOAD_CMD')),
            ('genesis_ssrf_switches.bob', ('PORT_CMD',)),
        ):
            root = ET.parse('gui/'+filename).getroot()
            for w in root.findall('widget'):
                if any((w.findtext('pv_name') or '').endswith(':'+s) for s in suffixes):
                    self.assertEqual(w.findtext('enabled'), 'false')
                    self.assertIsNone(w.find('rules'))

    def test_shutdown_barrier_precedes_owner_termination(self):
        text = Path('scripts/stop_ssrf_physical.sh').read_text()
        self.assertLess(text.index('await establish_safe_shutdown'), text.index('for s in genesis-ssrf-archiver'))
        self.assertNotIn('if session_exists genesis-ssrf-sequencer; then', text)
        self.assertIn('retaining physical owners', text)
