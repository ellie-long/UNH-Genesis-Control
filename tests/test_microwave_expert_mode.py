"""Microwave Expert mode (Elena Long, 2026-10-24).

A confirmed "Microwave Expert Unlock" widens the output frequency window from
138-142 GHz to 134-144 GHz.  Normal limits return on Lock, at start-up, and
whenever RF goes OFF.  The supervisor and the Vaunix IOC both enforce the
active window; the Vaunix IOC follows the supervisor's ExpertMode PV and falls
back to normal limits when it cannot read it.  Office-only: EPICS, network
and vendor calls are fakes (the pattern of test_microwave_rf_operation.py).
"""
import inspect
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import yaml

from logic_devices.microwave_supervisor.conversions import FrequencyConversion
from logic_devices.microwave_supervisor.device import Device as Supervisor
from microwave_ioc.vaunix_lms.toolkit_device import Device as Source, ShimRejected
from tools.vaunix_shim.vaunix_shim import ControlLease, Protocol, VaunixError

ROOT = Path(__file__).resolve().parents[1]
GHZ = 1e9


class PV:
    def __init__(self, name, output=False, **kwargs):
        self.name, self.output, self.kwargs = name, output, kwargs
        self.value = kwargs.get("initial_value", 0)
        self.callback = kwargs.get("on_update")
        if self.callback is None and kwargs.get("on_update_name"):
            self.callback = lambda value: kwargs["on_update_name"](value, name)

    def get(self):
        return self.value

    def set(self, value, **kwargs):
        self.value = value

    async def write(self, value):
        self.value = value
        if self.callback:
            result = self.callback(value)
            if inspect.isawaitable(result):
                await result

    def set_alarm(self, **kwargs):
        pass


def record(name, *args, **kwargs):
    return PV(name, **kwargs)


def output(name, *args, **kwargs):
    return PV(name, output=True, **kwargs)


class Brick:
    """A Lab Brick behind the widened Pi shim (134-144 GHz / 12)."""
    test_mode = False
    model, serial, library_version = "FAKE", 15033, 1
    minimum_frequency_hz, maximum_frequency_hz = 134 * GHZ / 12, 144 * GHZ / 12
    minimum_power_dbm, maximum_power_dbm = -40.0, 10.0

    def __init__(self):
        self.f, self.p, self.r, self.ref, self.pll = 140 * GHZ / 12, -12.0, 0, 1, 1
        self.sweep = [11.5e9, 11.8e9, 1e6, 1000, 1, 0, 0, 0, 0]

    def allowed_frequency_limits(self):
        return self.minimum_frequency_hz, self.maximum_frequency_hz

    def frequency(self): return self.f
    def power(self): return self.p
    def rf(self): return self.r
    def reference(self): return self.ref
    def pll_locked(self): return self.pll
    def sweep_state(self): return tuple(self.sweep)

    def set_frequency(self, hz):
        lo, hi = self.allowed_frequency_limits()
        if not lo <= hz <= hi:
            raise VaunixError("frequency outside the shim limits")
        self.f = hz
        return self.f

    def set_sweep_flag(self, name, enabled):
        self.sweep[-1] = int(enabled)
        return int(enabled)

    def set_rf(self, enabled):
        self.r = int(enabled)
        return self.r


class Transport:
    def __init__(self, lease):
        self.lease, self.client = lease, object()

    def command(self, request):
        reply = self.lease.dispatch(request, self.client)
        if reply.startswith("ERR "):
            raise ShimRejected(reply[4:])
        return reply

    def identity(self): return self.command("*IDN?"), 0
    def limits(self): return tuple(float(v) for v in self.command("LIMITS?").split())
    def acquire(self): return self.command("CONTROL ACQUIRE")
    def heartbeat(self): return self.command("CONTROL HEARTBEAT")
    def owned(self): return self.command("CONTROL?") == "1"

    def read(self):
        v = self.command("READ?").split()
        return float(v[0]) / 1e9, float(v[1]), *map(int, v[2:])

    def read_rf(self): return int(self.command("RF?"))

    def sweep(self):
        v = self.command("SWEEP?").split()
        return *(float(n) / 1e9 for n in v[:3]), *map(int, v[3:])

    def set_rf(self, enabled): return int(self.command(f"RF {int(enabled)}").split()[1])
    def set_sweep_flag(self, name, enabled): return int(self.command(f"SWEEP_{name} {int(enabled)}").split()[1])

    def set_frequency(self, ghz):
        return float(self.command(f"FREQ {float(ghz) * 1e9:.0f}").split()[1]) / 1e9

    def close(self): self.lease.disconnect(self.client)


def settings(profile, ioc):
    key = {"supervisor": "microwave_supervisor", "vaunix_lms": "vaunix_lms"}[ioc]
    return yaml.safe_load((ROOT / "configs/commissioning" / profile / ioc / "settings.yaml").read_text())[key]


class ExpertConversion(unittest.TestCase):
    def test_normal_and_expert_windows(self):
        c = FrequencyConversion(12, 138, 142, 134, 144)
        self.assertEqual(c.limits_text(), "Normal 138-142 GHz")
        with self.assertRaises(ValueError):
            c.validate_output(136.0)
        c.expert = True
        self.assertEqual(c.validate_output(136.0), 136.0)
        self.assertEqual(c.validate_output(143.9), 143.9)
        self.assertEqual(c.limits_text(), "EXPERT 134-144 GHz")
        with self.assertRaises(ValueError):
            c.validate_output(144.5)

    def test_expert_range_must_contain_the_normal_range(self):
        with self.assertRaises(ValueError):
            FrequencyConversion(12, 138, 142, 139, 144)

    def test_without_expert_limits_nothing_widens(self):
        c = FrequencyConversion(12, 138, 142)
        c.expert = True
        with self.assertRaises(ValueError):
            c.validate_output(136.0)


class ExpertSettings(unittest.TestCase):
    def test_both_profiles_configure_the_expert_window(self):
        for profile in ("microwave_physical", "microwave_sim"):
            sup = settings(profile, "supervisor")["frequency"]
            self.assertEqual((sup["output_min_ghz"], sup["output_max_ghz"]), (138.0, 142.0))
            self.assertEqual((sup["expert_min_ghz"], sup["expert_max_ghz"]), (134.0, 144.0))
            self.assertTrue(sup["expert_lock_on_rf_off"])
            src = settings(profile, "vaunix_lms")
            self.assertAlmostEqual(src["rf_safety"]["expert_frequency_min_ghz"] * 12, 134.0, places=6)
            self.assertAlmostEqual(src["rf_safety"]["expert_frequency_max_ghz"] * 12, 144.0, places=6)
            self.assertEqual(src["rf_safety_pvs"]["supervisor_expert"], "UNH:GEN:MW:SUP:ExpertMode")

    def test_pi_shim_service_allows_the_expert_range(self):
        unit = (ROOT / "tools/vaunix_shim/vaunix-shim.service").read_text()
        self.assertIn("--minimum-hz 11166666666", unit)
        self.assertIn("--maximum-hz 12000000000", unit)

    def test_screen_asks_before_unlocking(self):
        root = ET.parse(ROOT / "gui/genesis_microwave.bob").getroot()
        widgets = {w.findtext("name"): w for w in root.iter("widget")}
        unlock, lock = widgets["MW_Expert_Unlock"], widgets["MW_Expert_Lock"]
        self.assertEqual(unlock.findtext("text"), "Microwave Expert Unlock")
        self.assertEqual(unlock.findtext("show_confirm_dialog"), "true")
        self.assertEqual(unlock.findtext("confirm_message"), "Are you a microwave expert?")
        self.assertEqual(unlock.find(".//pv_name").text, "ca://UNH:GEN:MW:SUP:ExpertUnlock")
        self.assertEqual(lock.find(".//pv_name").text, "ca://UNH:GEN:MW:SUP:ExpertLock")
        self.assertEqual(widgets["MW_Limits_V"].findtext("pv_name"), "ca://UNH:GEN:MW:SUP:FrequencyLimits")


class ExpertIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch("softioc.builder." + kind,
                              output if kind.endswith("Out") or kind == "Action" else record)
                        for kind in ("boolIn", "aIn", "mbbIn", "longIn", "stringIn", "boolOut",
                                     "aOut", "mbbOut", "Action", "longOut")]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.brick = Brick()
        self.lease = ControlLease(Protocol(self.brick))
        self.source = Source("UNH:GEN:MW:SRC", settings("microwave_physical", "vaunix_lms"))
        self.source._create_connection = lambda: Transport(self.lease)
        self.source.connect()
        ucfg = settings("microwave_physical", "supervisor")
        ucfg["rf_confirmation_timeout"] = 0.08
        self.sup = Supervisor("UNH:GEN:MW:SUP", ucfg)
        self.unreadable = set()

        async def get(pv, **kwargs):
            if pv in self.unreadable:
                raise TimeoutError(pv)
            if ":MAN:" in pv:
                return time.time()
            if pv.startswith("UNH:GEN:MW:PS:"):
                return {"COMM": 1, "VMEAS": 5.0, "OUTPUT_RBV": 1}[pv.rsplit(":", 1)[1]]
            if pv.startswith("UNH:GEN:MW:SRC:"):
                return self.source.pvs[pv.rsplit(":", 1)[1]].get()
            return self.sup.pvs[pv.rsplit(":", 1)[1]].get()

        async def put(pv, value, **kwargs):
            name = pv.rsplit(":", 1)[1]
            if name == "MW_RF_CMD":
                self.source.pvs["MW_RF_CMD"].set(value)
                await self.source._rf_command(value)
            elif name == "MW_FC":
                await self.source.pvs["MW_FC"].write(value)
            else:
                raise AssertionError("unexpected fake caput " + pv)

        self.ca = [patch("aioca.caget", side_effect=get), patch("aioca.caput", side_effect=put)]
        for p in self.ca:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.ca])
        await self.sup.do_reads()
        await self.source.do_reads()

    async def unlock(self):
        await self.sup.pvs["ExpertUnlock"].write(1)
        await self.source.do_reads()            # the IOC follows the supervisor

    async def test_starts_normal_and_refuses_136_ghz(self):
        self.assertEqual(self.sup.pvs["ExpertMode"].get(), 0)
        self.assertEqual(self.sup.pvs["FrequencyLimits"].get(), "Normal 138-142 GHz")
        await self.sup.pvs["OutputFrequency"].write(136.0)
        self.assertIn("outside", self.sup.pvs["LastError"].get())
        self.assertAlmostEqual(self.brick.f * 12 / GHZ, 140.0)

    async def test_unlock_allows_136_and_143_ghz_through_both_iocs(self):
        await self.unlock()
        self.assertEqual(self.sup.pvs["ExpertMode"].get(), 1)
        self.assertEqual(self.sup.pvs["FrequencyLimits"].get(), "EXPERT 134-144 GHz")
        self.assertTrue(self.source._expert)
        for target in (136.0, 143.0):
            await self.sup.pvs["OutputFrequency"].write(target)
            self.assertEqual(self.sup.pvs["LastError"].get(), "")
            self.assertAlmostEqual(self.brick.f * 12 / GHZ, target, places=4)
        await self.sup.pvs["OutputFrequency"].write(144.5)     # beyond even Expert
        self.assertIn("outside", self.sup.pvs["LastError"].get())

    async def test_vaunix_refuses_a_direct_wide_write_without_expert(self):
        before = self.brick.f
        await self.source.pvs["MW_FC"].write(136.0 / 12)
        self.assertEqual(self.brick.f, before)
        await self.unlock()
        await self.source.pvs["MW_FC"].write(136.0 / 12)
        self.assertAlmostEqual(self.brick.f * 12 / GHZ, 136.0, places=4)

    async def test_vaunix_falls_back_to_normal_when_the_supervisor_is_unreadable(self):
        await self.unlock()
        self.unreadable.add("UNH:GEN:MW:SUP:ExpertMode")
        await self.source.do_reads()
        self.assertFalse(self.source._expert)

    async def test_rf_off_locks_expert_mode(self):
        await self.unlock()
        await self.sup.pvs["OutputFrequency"].write(136.0)
        await self.sup._set_rf(True)
        self.assertEqual(self.brick.r, 1, self.sup.pvs["LastError"].get())
        self.assertEqual(self.sup.pvs["ExpertMode"].get(), 1)
        await self.sup._set_rf(False)
        self.assertEqual(self.brick.r, 0)
        self.assertEqual(self.sup.pvs["ExpertMode"].get(), 0)
        self.assertEqual(self.sup.pvs["FrequencyLimits"].get(), "Normal 138-142 GHz")
        self.assertIn("RF OFF", self.sup.pvs["ExpertStatus"].get())

    async def test_lock_with_rf_on_outside_the_normal_window_turns_rf_off(self):
        await self.unlock()
        await self.sup.pvs["OutputFrequency"].write(136.0)
        await self.sup._set_rf(True)
        self.assertEqual(self.brick.r, 1, self.sup.pvs["LastError"].get())
        await self.sup.pvs["ExpertLock"].write(1)
        self.assertEqual(self.brick.r, 0)
        self.assertEqual(self.sup.pvs["ExpertMode"].get(), 0)

    async def test_rf_on_at_136_ghz_is_refused_without_expert(self):
        await self.unlock()
        await self.sup.pvs["OutputFrequency"].write(136.0)
        await self.sup.pvs["ExpertLock"].write(1)              # RF was off: just locks
        await self.source.do_reads()
        await self.sup._set_rf(True)
        self.assertEqual(self.brick.r, 0)
        self.assertNotEqual(self.sup.pvs["LastError"].get(), "")
