# Not yet tested. Uses RS232, not GPIB!


import logging
from softioc import builder
from genesis_common.telnet_base import TelnetDevice, TelnetConnection


class Device(TelnetDevice):
    """ELVA-1 Power Meter via serial-to-Ethernet adapter
    RS232 setup: 1200 baud, B data bits, 1 stop bit, no parity
    """

    def _create_pvs(self):
        for channel in self._skip_none_channels():
            self.pvs[channel] = builder.aIn(channel, **self.sevr)

    def _create_connection(self):
        return DeviceConnection(
            self.settings['ip'],
            self.settings['port'],
            self.settings['timeout'],
            self.settings['freq'],
            self.settings.get('averaging', 50)
        )


class DeviceConnection(TelnetConnection):
    """Handle connection to ELVA-1 power meter via Telnet"""

    def __init__(self, host, port, timeout, freq, averaging=50):
        super().__init__(host, port, timeout)
        self.freq = freq
        try:
            self.tn.write(bytes(f"sens:freq {self.freq}\n", 'ascii'))
            self.tn.write(bytes(f"unit:pow w\n", 'ascii'))
            self.tn.write(bytes(f"calc:aver:coun {averaging}\n", 'ascii'))
            self.tn.write(bytes(f"disp:enab off\n", 'ascii'))  # stop display refresh during remote polling
        except Exception as e:
            logging.error(f"ELVA-1 init commands failed on {self.host}: {e}")

    def read_all(self):
        """Read power from meter, return list with value in W (or mW if meter responds in ÂµW)"""
        try:
            self.tn.write(bytes("read?\n", 'ascii'))
            data = self.tn.read_until(b'\n', timeout=self.timeout).decode('ascii')
        except Exception as e:
            logging.error(f"ELVA-1 read failed on {self.host}: {e}")
            raise OSError('ELVA-1 read')

        if 'error' in data.lower():
            logging.warning(f"ELVA-1 returned error on {self.host}: {data.strip()}")
            return [-1.0]

        try:
            p = data.strip().split()
            unit = p[1].upper() if len(p) > 1 else ''
            if unit == 'UW':
                value = float(p[0]) / 1000.0   # ÂµW â†’ mW
            elif unit == 'MW':
                value = float(p[0])             # already mW
            elif unit == 'DBM':
                # unit:pow w was not accepted; reconnect will re-send it
                logging.warning(f"ELVA-1 responded in dBm on {self.host} â€” unit setting lost, reconnecting")
                raise OSError('ELVA-1 unexpected unit DBM')
            else:
                value = float(p[0])
            return [value]
        except OSError:
            raise
        except (ValueError, IndexError) as e:
            logging.error(f"ELVA-1 parse failed on {self.host}: {e}, raw: {data!r}")
            raise OSError('ELVA-1 parse')
