import logging
from softioc import builder
from genesis_common.telnet_base import TelnetDevice, TelnetConnection


class Device(TelnetDevice):
    """SRS BGA244 Binary Gas Analyzer"""

    def _create_pvs(self):
        for channel in self._skip_none_channels():
            self.pvs[channel + "_RI1"]  = builder.aIn(channel + "_RI1",  **self.sevr)  # Primary gas ratio
            self.pvs[channel + "_RI2"]  = builder.aIn(channel + "_RI2",  **self.sevr)  # Secondary gas ratio
            self.pvs[channel + "_TI"]   = builder.aIn(channel + "_TI",   **self.sevr)  # Gas temperature
            self.pvs[channel + "_PI"]   = builder.aIn(channel + "_PI",   **self.sevr)  # Analysis pressure
            self.pvs[channel + "_NSOS"] = builder.aIn(channel + "_NSOS", **self.sevr)  # Normalized speed of sound
            self.pvs[channel + "_BTI"]  = builder.aIn(channel + "_BTI",  **self.sevr)  # Block temperature

    def _create_connection(self):
        return DeviceConnection(
            self.settings['ip'],
            self.settings['port'],
            self.settings['timeout']
        )

    async def do_reads(self):
        try:
            ri1, ri2, ti, pi, nsos, bti = self.t.read_all()
            for channel in self._skip_none_channels():
                self.pvs[channel + "_RI1"].set(ri1)
                self.pvs[channel + "_RI2"].set(ri2)
                self.pvs[channel + "_TI"].set(ti)
                self.pvs[channel + "_PI"].set(pi)
                self.pvs[channel + "_NSOS"].set(nsos)
                self.pvs[channel + "_BTI"].set(bti)
            self._handle_read_success()
            return True
        except OSError:
            self._handle_read_error()
            return False


class DeviceConnection(TelnetConnection):
    """Handle connection to SRS BGA244 via serial over ethernet (RS-232 adapter)"""

    def read_all(self):
        """Query all measurements via XALL?.
        Returns [ratio1, ratio2, gas_temp, pressure, norm_sos, block_temp]"""
        try:
            self.tn.write(b'XALL?\n')
            data = self.tn.read_until(b'\n', timeout=self.timeout).decode('ascii')
            return [float(x) for x in data.strip().split(',')]
        except Exception as e:
            logging.error(f"BGA244 read failed on {self.host}: {e}")
            raise OSError('BGA244 read')
