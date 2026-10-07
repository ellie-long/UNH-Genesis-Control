import logging
import re
from softioc import builder
from genesis_common.telnet_base import TelnetDevice, TelnetConnection


class Device(TelnetDevice):
    """Makes library of PVs needed for HFC300 Mass Flow Controller and provides methods to connect them to the device

    Attributes:
        pvs: dict of Process Variables keyed by name
        channels: channels of device
    """

    def _create_pvs(self):
        """Create PVs for each channel"""
        mode_list = [
            ['Default', 0], ['Auto', 0], ['Hold', 0], ['Shut', 0],
            ['Purge', 0], ['Variable', 0], ['Error', 0]
        ]



        for channel in self._skip_none_channels():
            self.pvs[channel + "_FI"] = builder.aIn(channel + "_FI", **self.sevr)          # Flow
            self.pvs[channel + "_TI"] = builder.aIn(channel + "_TI", **self.sevr)          # Temperature (C)
            self.pvs[channel + "_SS"] = builder.aIn(channel + "_SS", **self.sevr)          # System state
            self.pvs[channel + "_SP_IMP"] = builder.aIn(channel + "_SP_IMP", **self.sevr)  # Implemented setpoint
            self.pvs[channel + "_VP"] = builder.stringIn(channel + "_VP")                   # Valve position

            self.pvs[channel + "_MODE"] = builder.mbbOut(channel + "_MODE", *mode_list, on_update_name=self.do_sets)  # MFC mode
            self.pvs[channel + "_SP"] = builder.aOut(channel + "_SP", on_update_name=self.do_sets, **self.sevr)      # Setpoint
            self.pvs[channel + "_VP_MAN"] = builder.aOut(channel + "_VP_MAN", on_update_name=self.do_sets, **self.sevr)  # Manual valve set

    def _create_connection(self):
        return DeviceConnection(
            self.settings['ip'],
            self.settings['port'],
            self.settings['timeout']
        )

    def _post_connect(self):
        """Read initial output values after connection"""
        self.read_outs()

    def read_outs(self):
        """Read and set OUT PVs at the start of the IOC"""
        for pv_name in self._skip_none_channels():
            try:
                self.pvs[pv_name + '_MODE'].set(self.t.read_mode())
                self.pvs[pv_name + '_SP'].set(self.t.read_setpoint())
                self.pvs[pv_name + '_VP_MAN'].set(self.t.read_valve_manual())
            except OSError:
                logging.error(f"Read out error on {pv_name}")
                self.reconnect()

    def do_sets(self, new_value, pv):
        """Set PV values to device"""
        gate_pv = pv.replace(self.device_name + ':', '')
        if not self.can_write(gate_pv):
            return

        pv_name = pv.replace(self.device_name + ':', '')
        try:
            if pv_name.endswith('_MODE'):
                result = self.t.set_mode(int(new_value))
                self.pvs[pv_name].set(result)
            elif pv_name.endswith('_SP'):
                result = self.t.set_setpoint(new_value)
                self.pvs[pv_name].set(result)
            elif pv_name.endswith('_VP_MAN'):
                result = self.t.set_valve_manual(new_value)
                self.pvs[pv_name].set(result)
            else:
                logging.error(f"Error, control PV not categorized: {pv_name}")
        except OSError:
            self.reconnect()

    async def do_reads(self):
        """Read from HFC300 and update PVs"""
        read_pvs = [ch + s for ch in self._skip_none_channels()
                    for s in ('_FI', '_TI', '_SS', '_SP_IMP', '_VP')]
        try:
            for channel in self._skip_none_channels():
                self.pvs[channel + "_FI"].set(self.t.read_flow())
                self.pvs[channel + "_TI"].set(self.t.read_temperature())
                self.pvs[channel + "_SS"].set(self.t.read_system_state())
                self.pvs[channel + "_SP_IMP"].set(self.t.read_impl_setpoint())
                self.pvs[channel + "_VP"].set(self.t.read_valve_position())
                self.pvs[channel + "_MODE"].set(self.t.read_mode())
            self._handle_read_success(read_pvs)
            return True
        except OSError:
            self._handle_read_error(read_pvs)
            return False


class DeviceConnection(TelnetConnection):
    """Handle connection to HFC300 Mass Flow Controller via telnet to RS-232"""

    def __init__(self, host, port, timeout):
        super().__init__(host, port, timeout)
        self.float_regex = re.compile(r'([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)')
        self.int_regex = re.compile(r'(\d+)')
        self.hex_regex = re.compile(r'(?:0?x)([0-9A-Fa-f]+)')

    def _send_command(self, command):
        """Send a command and read response until '>' terminator"""
        try:
            self.tn.write(bytes(command + '\r', 'ascii'))
            data = self.tn.read_until(b'>', timeout=self.timeout).decode('ascii')
            return data
        except Exception as e:
            logging.error(f"HFC300 command '{command}' failed on {self.host}: {e}")
            raise OSError('HFC300 command failed')

    def read_flow(self):
        """Read current flow in configured units (F command)"""
        try:
            data = self._send_command('F')
            m = self.float_regex.search(data)
            return float(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 flow read failed on {self.host}: {e}")
            raise OSError('HFC300 flow read')

    def read_temperature(self):
        """Read system temperature in degrees C (TEMP command)"""
        try:
            data = self._send_command('TEMP')
            m = self.float_regex.search(data)
            return float(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 temperature read failed on {self.host}: {e}")
            raise OSError('HFC300 temperature read')

    def read_system_state(self):
        """Read system state (SS command): 1=Init, 4=Normal, 6=Failure, 8=Calibration"""
        try:
            data = self._send_command('SS')
            m = self.int_regex.search(data)
            return int(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 system state read failed on {self.host}: {e}")
            raise OSError('HFC300 system state read')

    def read_impl_setpoint(self):
        """Read implemented setpoint in flow units (V8 command)"""
        try:
            data = self._send_command('V8')
            m = self.float_regex.search(data)
            return float(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 implemented setpoint read failed on {self.host}: {e}")
            raise OSError('HFC300 implemented setpoint read')

    _VP_BASE = {0x10: 'CLOSED', 0x20: 'PURGE', 0x30: 'HOLD', 0x40: 'VARIABLE', 0x50: 'AUTO'}
    _VP_MODIFIERS = {0x01: 'OVERRIDE_SHUT', 0x02: '1PCT_SHUTDOWN', 0x04: 'OVERRIDE_PURGE'}

    @staticmethod
    def _decode_valve_position(val):
        """Decode V3 hex value to human-readable string"""
        modifier_bits = val & 0x07
        if modifier_bits:
            return '+'.join(s for bit, s in DeviceConnection._VP_MODIFIERS.items() if modifier_bits & bit)
        return DeviceConnection._VP_BASE.get(val & 0xF0, f'UNKNOWN(x{val:02X})')

    def read_valve_position(self):
        """Read valve position as string from hex response (V3 command)"""
        try:
            data = self._send_command('V3')
            m = self.hex_regex.search(data)
            return self._decode_valve_position(int(m.group(1), 16))
        except Exception as e:
            logging.error(f"HFC300 valve position read failed on {self.host}: {e}")
            raise OSError('HFC300 valve position read')

    def read_mode(self):
        """Read MFC mode (V1 command): 0=Default, 1=Auto, 2=Hold, 3=Shut, 4=Purge, 5=Variable, 6=Error"""
        try:
            data = self._send_command('V1')
            m = self.int_regex.search(data)
            return int(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 mode read failed on {self.host}: {e}")
            raise OSError('HFC300 mode read')

    def read_setpoint(self):
        """Read flow setpoint in flow units (V4 command)"""
        try:
            data = self._send_command('V4')
            m = self.float_regex.search(data)
            return float(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 setpoint read failed on {self.host}: {e}")
            raise OSError('HFC300 setpoint read')

    def read_valve_manual(self):
        """Read manual valve drive value (V28 command)"""
        try:
            data = self._send_command('V28')
            m = self.int_regex.search(data)
            return int(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 manual valve read failed on {self.host}: {e}")
            raise OSError('HFC300 manual valve read')

    def set_mode(self, mode):
        """Set MFC mode (V1=N command) and read back"""
        try:
            self._send_command(f'V1={mode}')
            data = self._send_command('V1')
            m = self.int_regex.search(data)
            return int(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 mode set failed on {self.host}: {e}")
            raise OSError('HFC300 mode set')

    def set_setpoint(self, value):
        """Set flow setpoint in flow units (V4=N command) and read back"""
        try:
            self._send_command(f'V4={value}')
            data = self._send_command('V4')
            m = self.float_regex.search(data)
            return float(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 setpoint set failed on {self.host}: {e}")
            raise OSError('HFC300 setpoint set')

    def set_valve_manual(self, value):
        """Set manual valve drive value (V28=N command) and read back"""
        try:
            self._send_command(f'V28={value}')
            data = self._send_command('V28')
            m = self.int_regex.search(data)
            return int(m.group(1))
        except Exception as e:
            logging.error(f"HFC300 manual valve set failed on {self.host}: {e}")
            raise OSError('HFC300 manual valve set')
