import logging
from softioc import builder
from genesis_common.telnet_base import TelnetDevice, TelnetConnection


class Device(TelnetDevice):
    """EIP Frequency Counter via Prologix GPIB controller"""

    def _create_pvs(self):
        for channel in self._skip_none_channels():
            self.pvs[channel] = builder.aIn(channel, **self.sevr)

    def _create_connection(self):
        return DeviceConnection(
            self.settings['ip'],
            self.settings['port'],
            self.settings['timeout'],
            self.settings['addr'],
            self.settings['band'],
            self.settings['subband'],
            self.settings['cent_freq'],
            self.settings['rate']
        )


class DeviceConnection(TelnetConnection):
    """Handle connection to EIP frequency counter via Prologix GPIB"""

    def __init__(self, host, port, timeout, addr, band, subband, cent_freq, rate):
        super().__init__(host, port, timeout)
        try:
            self.tn.write(bytes(f"++addr {addr}\n", 'ascii'))
            self.tn.write(bytes(f"BA {band}\n", 'ascii'))
            self.tn.write(bytes(f"SU {subband}\n", 'ascii'))
            self.tn.write(bytes(f"CE {cent_freq} GHz\n", 'ascii'))
            self.tn.write(bytes(f"SA {rate} ms\n", 'ascii'))
        except Exception as e:
            logging.error(f"EIP counter init failed on {self.host}: {e}")

    def read_all(self):
        """Read frequency via 'OU DE', return list with value in GHz"""
        data = ''
        try:
            self.tn.write(bytes("OU DE\n", 'ascii'))
            data = self.tn.read_until(b'\r', timeout=self.timeout).decode('ascii')
            return [int(data.strip()) / 1e9]
        except ValueError:
            logging.error(f"EIP counter parse failed on {self.host}: raw={data!r}")
            raise OSError('EIP counter parse')
        except Exception as e:
            logging.error(f"EIP counter read failed on {self.host}: {e}")
            raise OSError('EIP counter read')
