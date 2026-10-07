from genesis_common.modbus_base import ModbusDevice
from softioc import builder, alarm


class Device(ModbusDevice):
    """4-point cryogenic LN2 level probe read via a Datexel DAT8017-I.

    Each of 4 Pt1000 RTDs is wired as a current divider against the module's
    internal ~100 ohm shunt: immersed (wet) RTDs drop to ~75 ohm and shunt
    current away from the module (lower mA reading), while dry, self-heated
    RTDs rise to ~120+ ohm and force more current through the module's shunt
    (higher mA reading). The RTD loops live on physical channels 0, 2, 4, 6;
    channels 1, 3, 5, 7 are unused by this wiring. Channel 0 reads the top
    RTD, channel 6 the bottom.
    """

    RTD_CHANNEL_INDICES = (0, 2, 4, 6)

    def __init__(self, device_name, settings):
        self.threshold = settings.get('threshold', 10.0)  # mA; below = wet, above = dry
        self.level_names = settings['channels']  # 4 PV names, in probe order (top to bottom)
        super().__init__(device_name, settings)

    def _create_pvs(self):
        """Create 4 wet/dry binary PVs and one overall percent-full PV"""
        for name in self.level_names:
            self.pvs[name] = builder.boolIn(name, ZNAM='Dry', ONAM='Wet', DISP=self.sevr['DISP'])
        self.pvs['Level_Percent'] = builder.aIn('Level_Percent', **self.sevr)

    async def do_reads(self):
        """Read all 8 current registers, derive wet/dry per RTD channel, and
        set overall level percentage from the contiguous wet run at the bottom"""
        try:
            readings = self.t.read_all()
            wet_states = []
            for name, idx in zip(self.level_names, self.RTD_CHANNEL_INDICES):
                current_ma = readings[idx] / 1000
                is_wet = current_ma < self.threshold
                self.pvs[name].set(is_wet)
                wet_states.append(is_wet)
            self._handle_read_success()

            # wet_states is top-to-bottom (probe order). Liquid fills from the
            # bottom up, so reverse before scanning: the fill level is the first
            # dry channel counting up from the bottom. Any wet channel above
            # that is physically impossible and means at least one sensor's
            # reading is bad.
            bottom_to_top = wet_states[::-1]
            fill_count = next((i for i, wet in enumerate(bottom_to_top) if not wet), len(bottom_to_top))
            if any(bottom_to_top[fill_count:]):
                self.pvs['Level_Percent'].set_alarm(severity=2, alarm=alarm.CALC_ALARM)
            else:
                self.pvs['Level_Percent'].set(100 * fill_count / len(bottom_to_top))
                self.remove_alarm('Level_Percent')
            return True
        except (OSError, TypeError, AttributeError) as e:
            print(e)
            self._handle_read_error()
            self.set_alarm('Level_Percent')
            return False
