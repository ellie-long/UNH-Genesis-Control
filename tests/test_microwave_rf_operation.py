"""Office-only RF integration. EPICS, network and vendor calls are fakes."""
import asyncio
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import yaml

from logic_devices.microwave_supervisor.device import Device as Supervisor
from microwave_ioc.vaunix_lms.toolkit_device import Device as Source, ShimRejected
from tools.vaunix_shim.vaunix_shim import Protocol, ControlLease, LabBrick, VaunixError, Server

ROOT = Path(__file__).resolve().parents[1]


class PV:
    def __init__(self, name, output=False, **kwargs):
        self.name, self.output, self.kwargs = name, output, kwargs
        self.value = kwargs.get('initial_value', 0)
        self.callback = kwargs.get('on_update')
        if self.callback is None and kwargs.get('on_update_name'):
            self.callback = lambda value: kwargs['on_update_name'](value, name)
        self.dispatched = 0

    def get(self): return self.value

    def set(self, value, **kwargs):
        if self.output and kwargs.get('process', True) and value != self.value and self.callback:
            self.dispatched += 1
        self.value = value

    def set_alarm(self, **kwargs): pass


def record(name, *args, **kwargs): return PV(name, **kwargs)
def output(name, *args, **kwargs): return PV(name, output=True, **kwargs)


class Brick:
    test_mode = False
    model, serial, library_version = 'FAKE', 15033, 1
    minimum_frequency_hz, maximum_frequency_hz = 11.5e9, 11.8333333333e9
    minimum_power_dbm, maximum_power_dbm = -40.0, 10.0

    def __init__(self):
        self.f, self.p, self.r, self.ref, self.pll = 11.6e9, -12.0, 1, 1, 1
        self.sweep = [11.5e9, 11.8e9, 1e6, 1000, 1, 0, 0, 0, 0]
        self.calls = []
        self.fail_sweep = self.fail_off = self.fail_on = False

    def allowed_frequency_limits(self): return self.minimum_frequency_hz, self.maximum_frequency_hz
    def frequency(self): return self.f
    def power(self): return self.p
    def rf(self): return self.r
    def reference(self): return self.ref
    def pll_locked(self): return self.pll
    def sweep_state(self): return tuple(self.sweep)
    def set_sweep_flag(self, name, enabled):
        self.calls.append(('sweep', bool(enabled)))
        if self.fail_sweep: raise VaunixError('sweep stop failed')
        self.sweep[-1] = int(enabled)
        return int(enabled)

    def set_rf(self, enabled):
        self.calls.append(('rf', bool(enabled)))
        if (enabled and self.fail_on) or (not enabled and self.fail_off):
            raise VaunixError('RF rejected')
        self.r = int(enabled)
        return self.r


class Transport:
    def __init__(self, lease): self.lease, self.client = lease, object()
    def command(self, request):
        reply = self.lease.dispatch(request, self.client)
        if reply.startswith('ERR '): raise ShimRejected(reply[4:])
        return reply
    def identity(self): return self.command('*IDN?'), 0
    def limits(self): return tuple(float(v) for v in self.command('LIMITS?').split())
    def acquire(self): return self.command('CONTROL ACQUIRE')
    def heartbeat(self): return self.command('CONTROL HEARTBEAT')
    def owned(self): return self.command('CONTROL?') == '1'
    def read(self):
        v = self.command('READ?').split()
        return float(v[0])/1e9, float(v[1]), *map(int, v[2:])
    def read_rf(self): return int(self.command('RF?'))
    def sweep(self):
        v=self.command('SWEEP?').split()
        return *(float(n)/1e9 for n in v[:3]), *map(int,v[3:])
    def set_rf(self, enabled): return int(self.command(f'RF {int(enabled)}').split()[1])
    def set_sweep_flag(self, name, enabled): return int(self.command(f'SWEEP_{name} {int(enabled)}').split()[1])
    def close(self): self.lease.disconnect(self.client)


class RFIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches=[]
        for kind in ('boolIn','aIn','mbbIn','longIn','stringIn','boolOut','aOut','mbbOut','Action','longOut'):
            self.patches.append(patch('softioc.builder.'+kind, output if kind.endswith('Out') or kind=='Action' else record))
        for p in self.patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.scfg=yaml.safe_load((ROOT/'configs/commissioning/microwave_physical/vaunix_lms/settings.yaml').read_text())['vaunix_lms']
        self.ucfg=yaml.safe_load((ROOT/'configs/commissioning/microwave_physical/supervisor/settings.yaml').read_text())['microwave_supervisor']
        self.ucfg['rf_confirmation_timeout']=0.08
        self.brick=Brick(); self.lease=ControlLease(Protocol(self.brick))
        self.source=Source('UNH:GEN:MW:SRC',self.scfg)
        self.source._create_connection=lambda: Transport(self.lease)
        self.source.connect()
        self.sup=Supervisor('UNH:GEN:MW:SUP',self.ucfg)
        self.overrides={}
        async def get(pv, **kwargs):
            if pv in self.overrides:
                v=self.overrides[pv]
                if isinstance(v,Exception): raise v
                return v
            if ':MAN:' in pv: return time.time()
            if pv.startswith('UNH:GEN:MW:PS:'):
                return {'COMM':1,'VMEAS':5.0,'OUTPUT_RBV':1}[pv.rsplit(':',1)[1]]
            if pv.startswith('UNH:GEN:MW:SRC:'): return self.source.pvs[pv.rsplit(':',1)[1]].get()
            return self.sup.pvs[pv.rsplit(':',1)[1]].get()
        async def put(pv, value, **kwargs):
            if pv.endswith('MW_RF_CMD'):
                self.source.pvs['MW_RF_CMD'].set(value,process=False)
                await self.source._rf_command(value)
            else: raise AssertionError('unexpected fake caput '+pv)
        self.get=get
        self.ca=[patch('aioca.caget',side_effect=get),patch('aioca.caput',side_effect=put)]
        for p in self.ca:p.start()
        self.addCleanup(lambda:[p.stop() for p in self.ca])
        await self.sup.do_reads()
        self.brick.calls.clear()

    async def on(self): await self.sup._set_rf(1)
    async def off(self): await self.sup._set_rf(0)

    async def test_valid_on_and_off_confirmed(self):
        await self.on()
        self.assertEqual(self.brick.r,1)
        self.assertTrue(self.sup._rf_request and self.sup._rf_valid and self.sup._rf_actual)
        await self.off()
        self.assertEqual(self.brick.r,0)
        self.assertEqual(self.sup.pvs['RFState'].get(),1)

    async def test_calibration_not_a_gate(self):
        for value in (None,False,'INVALID'):
            self.scfg['calibration_valid']=self.ucfg['calibration_valid']=value
            await self.on(); self.assertEqual(self.brick.r,1); await self.off()

    async def test_prerequisites_block_on(self):
        failures={'uca_connected':0,'vaunix_connected':0,'uca_output':0,'uca_voltage':4.8,
                  'simulation':1,'test_mode':1,'pll':0,'control_owned':0,'rf_valid':0,
                  'frequency':11.4,'power':10.25,'uca_time':time.time()-10,'vaunix_time':time.time()-10}
        for key,value in failures.items():
            with self.subTest(key=key):
                self.overrides[self.ucfg['rf_safety_pvs'][key]]=value
                await self.on()
                self.assertNotIn(('rf',True),self.brick.calls)
                self.assertFalse(self.sup._rf_request)
                self.overrides.clear();self.brick.calls.clear()
                await self.sup.do_reads()

    async def test_nonfinite_required_inputs_block_on(self):
        for key in ('uca_voltage','frequency','power','uca_time','rf_time','pll','uca_output'):
            for value in (float('nan'),float('inf'),float('-inf')):
                with self.subTest(key=key,value=value):
                    self.overrides[self.ucfg['rf_safety_pvs'][key]]=value
                    await self.on();self.assertNotIn(('rf',True),self.brick.calls)
                    self.overrides.clear();self.brick.calls.clear();await self.sup.do_reads()

    async def test_repeated_off_reaches_vendor_without_permissive(self):
        self.overrides[self.ucfg['rf_safety_pvs']['uca_voltage']]=0
        for _ in range(3): await self.off()
        self.assertEqual(self.brick.calls.count(('rf',False)),3)

    async def test_abort_wins_pending_on(self):
        entered,release=asyncio.Event(),asyncio.Event()
        original=self.sup._read_sources
        async def delayed():
            entered.set();await release.wait();return await original()
        self.sup._read_sources=delayed
        on=asyncio.create_task(self.on());await entered.wait()
        abort=asyncio.create_task(self.sup._abort_rf(1));await asyncio.sleep(0)
        release.set();await asyncio.gather(on,abort)
        self.assertEqual(self.brick.r,0);self.assertNotIn(('rf',True),self.brick.calls)

    async def test_abort_wins_inflight_command(self):
        entered,release=asyncio.Event(),asyncio.Event()
        original=self.source._rf_command
        async def delayed(value):
            if value: entered.set();await release.wait()
            await original(value)
        self.source._rf_command=delayed
        on=asyncio.create_task(self.on());await entered.wait()
        abort=asyncio.create_task(self.sup._abort_rf(1));await asyncio.sleep(0)
        release.set();await asyncio.gather(on,abort)
        self.assertEqual(self.brick.r,0);self.assertFalse(self.sup._rf_request)
        self.assertEqual(self.brick.calls[-1],('rf',False))

    async def test_abort_cancels_pending_ioc_validation_before_vendor_on(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.get
        async def delayed(pv, **kwargs):
            if pv == self.scfg['rf_safety_pvs']['supervisor_request']:
                entered.set()
                await release.wait()
            return await original(pv, **kwargs)
        with patch('aioca.caget', side_effect=delayed):
            on = asyncio.create_task(self.on())
            await entered.wait()
            abort = asyncio.create_task(self.sup._abort_rf(1))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(on, abort)
        self.assertNotIn(('rf', True), self.brick.calls)
        self.assertEqual(self.brick.r, 0)

    async def test_off_cancels_sweep_and_attempts_mute_despite_stop_failure(self):
        await self.on();self.sup._sweep_state=1;self.sup._points=[140]
        self.brick.fail_sweep=True
        await self.sup._abort_rf(1)
        self.assertEqual(self.brick.r,0);self.assertEqual(self.sup._points,[])
        self.assertIn(('sweep',False),self.brick.calls)
        self.assertIn(('rf',False),self.brick.calls)
        self.assertTrue(self.sup.pvs['LastError'].get())

    async def test_rejected_on_never_reports_on(self):
        self.brick.fail_on=True;await self.on()
        self.assertFalse(self.sup._rf_request);self.assertEqual(self.brick.r,0)
        self.assertNotEqual(self.sup.pvs['RFState'].get(),2)

    async def test_rejected_off_is_unknown(self):
        await self.on();self.brick.fail_off=True;await self.off()
        self.assertEqual(self.brick.r,1);self.assertEqual(self.sup.pvs['RFState'].get(),0)
        self.assertTrue(self.source.pvs['MW_RF_ERROR'].get())

    async def test_startup_and_reconnect_never_replay_on(self):
        await self.on();self.source.reconnect()
        self.assertEqual(self.brick.r,0)
        self.assertEqual(self.source.pvs['MW_RF_CMD'].get(),0)
        await self.sup.do_reads();self.assertFalse(self.sup._rf_request)
        await self.on();self.assertEqual(self.brick.r,1)
        restarted=Supervisor('UNH:GEN:MW:SUP',self.ucfg)
        await restarted.do_reads();self.assertEqual(self.brick.r,0)
        self.assertFalse(restarted._rf_request)

    async def test_failed_startup_never_ready(self):
        self.brick.fail_off=True;self.brick.r=1;self.source.reconnect()
        self.assertFalse(self.source.pvs['MW_Connected'].get())
        self.assertEqual(self.source.pvs['MW_RF_STATE'].get(),0)

    async def test_polling_never_dispatches_output_callbacks(self):
        await self.on();await self.source.do_reads()
        for pv in self.source.pvs.values(): self.assertEqual(pv.dispatched,0,pv.name)

    async def test_request_and_readback_are_distinct(self):
        self.source.pvs['MW_RF_CMD'].set(1,process=False)
        self.assertEqual(self.source.pvs['MW_RF_RBV'].get(),0)
        self.sup.pvs['RFEnable'].set(1,process=False)
        self.assertEqual(self.sup.pvs['RFActual'].get(),0)

    async def test_raw_on_cannot_bypass_supervisor(self):
        await self.source._legacy_rf(1);self.assertEqual(self.brick.r,0)
        await self.source._rf_command(1);self.assertEqual(self.brick.r,0)
        self.assertTrue(self.source.pvs['MW_RF_ERROR'].get())

    async def test_loss_blocks_on_and_recovery_does_not_resume(self):
        await self.on()
        self.overrides[self.ucfg['rf_safety_pvs']['uca_connected']]=OSError('lost')
        await self.sup.do_reads();self.assertFalse(self.sup._rf_request)
        self.assertEqual(self.brick.r,0)
        self.overrides.clear();await self.sup.do_reads()
        self.assertEqual(self.brick.r,0)

    async def test_uca_comm_loss_preserves_fresh_independent_off_confirmation(self):
        await self.on()
        self.overrides[self.ucfg['rf_safety_pvs']['uca_connected']] = 0
        self.assertFalse(await self.sup.do_reads())
        self.assertFalse(self.sup._rf_request)
        self.assertEqual(self.brick.r, 0)
        self.assertEqual(self.sup.pvs['RFState'].get(), 1)
        self.assertTrue(self.sup.pvs['RFValid'].get())
        self.assertFalse(self.sup.pvs['RFPermissive'].get())

    async def test_attenuation_and_pll_loss_after_on_are_diagnostic(self):
        await self.on()
        self.overrides[self.ucfg['rf_safety_pvs']['uca_voltage']]=3.8
        self.overrides[self.ucfg['rf_safety_pvs']['pll']]=0
        await self.sup.do_reads()
        self.assertEqual(self.brick.r,1);self.assertFalse(self.sup.pvs['RFPermissive'].get())

    async def test_records_support_repeated_off(self):
        self.assertTrue(self.sup.pvs['RFEnable'].kwargs['always_update'])
        self.assertTrue(self.source.pvs['MW_RF_CMD'].kwargs['always_update'])
        self.assertTrue(self.source.pvs['MW_RF'].kwargs['always_update'])

    async def test_off_confirmation_does_not_require_pll_query(self):
        await self.on()
        self.brick.pll_locked = lambda: (_ for _ in ()).throw(VaunixError('PLL read failed'))
        await self.off()
        self.assertEqual(self.brick.r, 0)
        self.assertEqual(self.sup.pvs['RFState'].get(), 1)
        self.assertEqual(self.sup.pvs['LastError'].get(), '')

    async def test_repeated_abort_reaches_vendor_each_time(self):
        for _ in range(3):
            await self.sup._abort_rf(1)
        self.assertEqual(self.brick.calls.count(('rf', False)), 3)

    async def test_ioc_off_cancels_pending_on(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.get
        async def delayed(pv, **kwargs):
            if pv.endswith('OUTPUT_RBV'):
                entered.set()
                await release.wait()
            return await original(pv, **kwargs)
        self.sup._rf_request = True
        self.sup.pvs['RFEnable'].set(1, process=False)
        with patch('aioca.caget', side_effect=delayed):
            on = asyncio.create_task(self.source._rf_command(1))
            await entered.wait()
            off = asyncio.create_task(self.source._rf_command(0))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(on, off)
        self.assertNotIn(('rf', True), self.brick.calls)
        self.assertEqual(self.brick.r, 0)

    async def test_poll_cannot_confirm_unexecuted_off_command(self):
        tasks = []
        async def enqueue(pv, value, **kwargs):
            async def delayed():
                await asyncio.sleep(0.02)
                await self.source._rf_command(value)
            tasks.append(asyncio.create_task(delayed()))
        # Even freshly polled OFF cannot substitute for a command completion.
        self.overrides[self.ucfg['rf_safety_pvs']['rf_time']] = time.time() + 0.001
        with patch('aioca.caput', side_effect=enqueue):
            await self.off()
        await asyncio.gather(*tasks)
        self.assertIn(('rf', False), self.brick.calls)

    async def test_supervisor_process_loss_mutes_at_source(self):
        await self.on()
        self.overrides[self.scfg['rf_safety_pvs']['supervisor_time']] = time.time() - 10
        await self.source.do_reads()
        self.assertEqual(self.brick.r, 0)
        self.overrides.clear()
        await self.source.do_reads()
        self.assertEqual(self.brick.r, 0)

    async def test_lease_loss_is_unknown_until_safe_reacquisition(self):
        await self.on()
        self.lease.revoke()
        await self.source.do_reads()
        self.assertFalse(self.source.pvs['MW_RF_VALID'].get())
        self.assertFalse(self.source.pvs['MW_CONTROL_OWNED'].get())
        await self.source.do_reads()
        self.assertEqual(self.source.pvs['MW_RF_STATE'].get(), 1)
        self.assertEqual(self.source.pvs['MW_RF_CMD'].get(), 0)
        self.assertEqual(self.brick.r, 0)

    async def test_failed_off_latches_on_block_until_safe_off(self):
        await self.on()
        self.brick.fail_off = True
        await self.off()
        self.assertFalse(self.source.pvs['MW_RF_READY'].get())
        self.brick.fail_off = False
        await self.source.do_reads()
        self.brick.calls.clear()
        await self.on()
        self.assertNotIn(('rf', True), self.brick.calls)
        await self.off()
        await self.on()
        self.assertEqual(self.brick.r, 1)

    async def test_existing_power_envelope_includes_nonbaseline_power(self):
        self.brick.p = -30.0
        await self.source.do_reads()
        await self.on()
        self.assertEqual(self.brick.r, 1)



class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.brick=Brick();self.clock=0.0
        self.lease=ControlLease(Protocol(self.brick),timeout=2,now=lambda:self.clock)
        self.a,self.b=object(),object()
    def acquire(self): self.assertEqual(self.lease.dispatch('CONTROL ACQUIRE',self.a),'OK 2')
    def test_exclusive_and_competitor_cannot_write(self):
        self.acquire()
        self.assertTrue(self.lease.dispatch('CONTROL ACQUIRE',self.b).startswith('ERR'))
        self.assertTrue(self.lease.dispatch('RF 1',self.b).startswith('ERR'))
        self.assertTrue(self.lease.dispatch('READ?',self.b).split()[2]=='0')
    def test_heartbeat_preserves_ownership(self):
        self.acquire();self.clock=1;self.lease.dispatch('CONTROL HEARTBEAT',self.a)
        self.clock=2.5;self.lease.expire();self.assertIs(self.lease.owner,self.a)
    def test_reads_do_not_renew_or_write(self):
        self.acquire();self.brick.calls.clear();self.clock=3
        self.lease.dispatch('READ?',self.b)
        self.assertEqual(self.brick.calls,[])
        self.assertEqual(self.lease.dispatch('CONTROL?',self.a),'0')
        self.lease.expire();self.assertIsNone(self.lease.owner)
    def test_expiry_mutes_independently_of_sweep_failure(self):
        self.acquire();self.brick.set_rf(True);self.brick.fail_sweep=True
        self.clock=3;self.lease.expire()
        self.assertEqual(self.brick.r,0);self.assertIsNone(self.lease.owner)
        self.assertTrue(self.lease.last_error)
    def test_disconnect_mutes_and_reacquisition_does_not_restore(self):
        self.acquire();self.brick.set_rf(True);self.lease.disconnect(self.a)
        self.acquire();self.assertEqual(self.brick.r,0)
    def test_nonowner_disconnect_does_not_mute(self):
        self.acquire();self.brick.set_rf(True);self.lease.disconnect(self.b)
        self.assertEqual(self.brick.r,1)
    def test_failed_off_blocks_acquire_and_watchdog_retries(self):
        self.brick.fail_off=True
        self.assertTrue(self.lease.dispatch('CONTROL ACQUIRE',self.a).startswith('ERR'))
        self.assertIsNone(self.lease.owner);self.brick.fail_off=False
        self.lease.expire();self.assertEqual(self.brick.r,0);self.assertFalse(self.lease.last_error)


class VendorSafety(unittest.TestCase):
    def test_stop_failure_does_not_skip_vendor_off(self):
        brick=LabBrick.__new__(LabBrick);brick.device_id=1;calls=[]
        class API:
            def start_sweep(self,*a): calls.append('stop');return 0x80000000
            def set_rf(self,*a): calls.append('off');return 0
            def get_rf(self,*a): return 0
        brick.api=API()
        with self.assertRaises(VaunixError):brick._stop_and_mute()
        self.assertEqual(calls,['stop','off'])
    def test_device_open_mutes_before_failing_diagnostic(self):
        class API:
            r = 1
            calls = []
            def initialize(self): pass
            def set_test_mode(self, mode): pass
            def get_version(self): return 1
            def get_num_devices(self): return 1
            def get_dev_info(self, ids): ids[0] = 1; return 1
            def get_serial(self, device): return 15033
            def open_device(self, device): return 0
            def close_device(self, device): self.calls.append('close')
            def start_sweep(self, device, enabled): self.calls.append('stop'); return 0
            def set_rf(self, device, enabled): self.r = int(enabled); self.calls.append('off'); return 0
            def get_rf(self, device): return self.r
            def get_model_name(self, device, name): self.calls.append('diagnostic'); return 0
        api = API()
        brick = LabBrick(api, serial=15033)
        with self.assertRaises(VaunixError):
            brick.open()
        self.assertEqual(api.r, 0)
        self.assertLess(api.calls.index('off'), api.calls.index('diagnostic'))

    def test_vendor_carrier_power_pll_limits_preserved(self):
        brick=LabBrick.__new__(LabBrick);brick.device_id=1
        brick.allowed_min_hz=brick.minimum_frequency_hz=11.5e9
        brick.allowed_max_hz=brick.maximum_frequency_hz=11.8333333333e9
        brick.minimum_power_dbm=-40;brick.maximum_power_dbm=10
        class API:
            f,p,pll=1160000000,0,1
            def get_frequency(self,*a):return self.f
            def get_power(self,*a):return self.p
            def get_pll_lock(self,*a):return self.pll
            def set_rf(self,*a):return 0
            def get_rf(self,*a):return 1
        brick.api=API()
        self.assertEqual(brick.set_rf(True),1)
        for attr,value in [('f',1140000000),('p',41),('pll',0)]:
            old=getattr(brick.api,attr);setattr(brick.api,attr,value)
            with self.subTest(attr=attr),self.assertRaises(VaunixError):brick.set_rf(True)
            setattr(brick.api,attr,old)


class GUIBoundary(unittest.TestCase):
    def test_supervised_on_and_abort_and_diagnostics(self):
        for filename in ('genesis_microwave.bob','genesis_mw_supervisor.bob'):
            root=ET.parse(ROOT/'gui'/filename).getroot()
            buttons=[w.findtext('pv_name') for w in root.iter('widget') if w.attrib['type']=='slide_button']
            self.assertTrue(any(p.endswith(':RFEnable') for p in buttons if p))
            self.assertFalse(any(':MW_RF' in p for p in buttons if p))
            actions=[a.findtext('pv_name') for a in root.iter('action') if a.attrib['type']=='write_pv']
            self.assertTrue(any(p.endswith(':RFAbort') for p in actions if p))
            pvs={p.text for p in root.iter('pv_name')}
            self.assertFalse(any(p.endswith(':MW_RF') for p in pvs))
            for suffix in ('RFState','RFValid','ControlOwned'):self.assertTrue(any(p.endswith(':'+suffix) for p in pvs))


class LocalSocketLease(unittest.IsolatedAsyncioTestCase):
    """The production server/watchdog and IOC transport on loopback only."""
    async def asyncSetUp(self):
        self.brick = Brick()
        self.server = Server(Protocol(self.brick), lease_timeout=0.12)
        self.listener = await asyncio.start_server(self.server.client, '127.0.0.1', 0)
        self.port = self.listener.sockets[0].getsockname()[1]
        self.watchdog = asyncio.create_task(self.server.watchdog())
        self.writers = []

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        self.listener.close()
        await self.listener.wait_closed()
        self.watchdog.cancel()
        await asyncio.gather(self.watchdog, return_exceptions=True)
        await asyncio.sleep(0.02)

    async def client(self):
        reader,writer = await asyncio.open_connection('127.0.0.1', self.port)
        self.writers.append(writer)
        async def ask(command):
            writer.write((command+'\n').encode())
            await writer.drain()
            return (await reader.readline()).decode().strip()
        return ask,writer

    async def test_watchdog_mutes_without_client_traffic(self):
        ask,_ = await self.client()
        self.assertTrue((await ask('CONTROL ACQUIRE')).startswith('OK'))
        self.assertEqual(await ask('RF 1'), 'OK 1')
        await asyncio.sleep(0.2)
        self.assertEqual(self.brick.r, 0)
        self.assertEqual(await ask('CONTROL?'), '0')
        self.assertTrue((await ask('RF 1')).startswith('ERR'))

    async def test_disconnect_cleanup_and_competing_socket(self):
        ask,owner = await self.client()
        other,_ = await self.client()
        await ask('CONTROL ACQUIRE')
        self.assertTrue((await other('CONTROL ACQUIRE')).startswith('ERR'))
        self.assertTrue((await other('RF 1')).startswith('ERR'))
        await ask('RF 1')
        self.brick.fail_sweep = True
        owner.close();await owner.wait_closed();await asyncio.sleep(0.04)
        self.assertEqual(self.brick.r, 0)
        self.assertIsNone(self.server.lease.owner)

    async def test_production_transport_acquires_and_heartbeats(self):
        from microwave_ioc.vaunix_lms.toolkit_device import ShimConnection
        connection = await asyncio.to_thread(ShimConnection, '127.0.0.1', self.port, 1.0)
        try:
            await asyncio.to_thread(connection.acquire)
            self.assertTrue(await asyncio.to_thread(connection.owned))
            await asyncio.to_thread(connection.heartbeat)
            self.assertEqual(await asyncio.to_thread(connection.set_rf, True), 1)
            self.assertEqual(await asyncio.to_thread(connection.set_rf, False), 0)
        finally:
            await asyncio.to_thread(connection.close)


class InstalledAPISmoke(unittest.TestCase):
    def test_real_pysoftioc_records_with_local_simulation_only(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-c', '''
import asyncio
from pathlib import Path
from unittest.mock import patch
import yaml
import devices
from softioc import builder
from microwave_ioc.vaunix_lms.toolkit_device import Device
from logic_devices.microwave_supervisor.device import Device as Supervisor
assert Path(devices.__file__).resolve().parent == Path.cwd() / 'devices'
builder.SetDeviceName('OFFICE:SOURCE')
cfg = yaml.safe_load(Path('configs/commissioning/microwave_sim/vaunix_lms/settings.yaml').read_text())['vaunix_lms']
with patch('socket.create_connection', side_effect=AssertionError('no sockets')), patch('subprocess.run', side_effect=AssertionError('no SSH')):
    source = Device('OFFICE:SOURCE', cfg)
    source.connect()
    assert source.pvs['MW_Connected'].get() == 1
    assert source.pvs['MW_RF_RBV'].get() == 0
    asyncio.run(source._rf_command(0))
    assert source.pvs['MW_RF_DONE_TIME'].get() > 0
    asyncio.run(source.do_reads())
    builder.SetDeviceName('OFFICE:SUPERVISOR')
    cfg = yaml.safe_load(Path('configs/commissioning/microwave_sim/supervisor/settings.yaml').read_text())['microwave_supervisor']
    supervisor = Supervisor('OFFICE:SUPERVISOR', cfg)
    asyncio.run(supervisor.do_reads())
    asyncio.run(supervisor._set_rf(1))
    assert supervisor.pvs['RFState'].get() == 2
    asyncio.run(supervisor._abort_rf(1))
    assert supervisor.pvs['RFState'].get() == 1
'''], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
