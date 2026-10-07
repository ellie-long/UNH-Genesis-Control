import asyncio
import logging

from genesis_common.base_device import GenesisBaseDevice
from alicat import FlowController
from softioc import builder


class Device(GenesisBaseDevice):
    """ALICAT mcw Flow controller"""

    def _create_pvs(self):
        """Create level input PVs for each channel"""
        for channel in self._skip_none_channels():
            self.pvs[channel + "_FI"] = builder.aIn(channel + "_FI", **self.sevr)   # Mass flow
            self.pvs[channel + "_PI"] = builder.aIn(channel + "_PI", **self.sevr)   # Pressure
            self.pvs[channel + "_TI"] = builder.aIn(channel + "_TI", **self.sevr)   # Temperature (C)
            self.pvs[channel + "_CI"] = builder.aIn(channel + "_CI", **self.sevr)   # Control (flow or pressure)
            self.pvs[channel + "_SP"] = builder.aOut(channel + "_SP", on_update_name=self.do_sets, **self.sevr)  # Setpoint

    def _create_connection(self):
        return DeviceConnection(
            self.settings['ip'],
            self.settings['port'],
            self.settings['timeout']
        )

    async def do_reads(self):
        """Read from Alicat"""
        try:
            data = await self.t.read_all()
            for channel in self._skip_none_channels():
                self.pvs[channel + "_FI"].set(data['mass_flow'])
                self.pvs[channel + "_PI"].set(data['pressure'])
                self.pvs[channel + "_TI"].set(data['temperature'])
                self.pvs[channel + "_CI"].set(data['control_point'])
            self._handle_read_success()
            return True
        except OSError:
            self._handle_read_error()
            return False

    def _post_connect(self):
        """Schedule async gas type init and setpoint read after connection"""
        asyncio.ensure_future(self._async_post_connect())

    async def _async_post_connect(self):
        try:
            await self.t.async_connect()
            await self.t.set_gas_type(self.settings['gas_type'])
            await self._async_read_outs()
        except OSError as e:
            logging.error(f"Post-connect failed on {self.settings['ip']}: {e}")

    async def _async_read_outs(self):
        for pv_name in self._skip_none_channels():
            try:
                data = await self.t.read_all()
                self.pvs[pv_name + '_SP'].set(data['setpoint'])
            except OSError:
                logging.error(f"Read out error on {pv_name}")
                self.reconnect()

    def do_sets(self, new_value, pv):
        """Set PV values to device"""
        gate_pv = pv.replace(self.device_name + ':', '')
        if not self.can_write(gate_pv):
            return

        pv_name = pv.replace(self.device_name + ':', '')  # remove device name
        try:
            if '_SP' in pv_name:
                asyncio.ensure_future(self.t.set_flow_rate(new_value))
            else:
                logging.error(f"Error, control PV not categorized: {pv_name}")
        except OSError:
            self.reconnect()


class DeviceConnection():
    """Handle connection to Alicat MCW Flow Controller through 'alicat' Python interface"""

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.fc = None

    async def async_connect(self):
        try:
            self.fc = FlowController(f'{self.host}:{self.port}')
        except Exception as e:
            logging.error(f"Alicat Connection failed on {self.host}: {e}")
            raise

    async def read_all(self):
        """Read from device"""
        if self.fc is None:
            raise OSError('Alicat not connected')
        try:
            data = await self.fc.get()
            return data
        except Exception as e:
            logging.error(f"Alicat read failed on {self.host}: {e}")
            raise OSError('Alicat read')

    async def set_flow_rate(self, rate):
        """Set flow rate set point"""
        await self.fc.set_flow_rate(rate)

    async def set_gas_type(self, type):
        """Set flow rate set point"""
        await self.fc.set_gas(type)
