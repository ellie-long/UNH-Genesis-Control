"""Hardware-free regression tests: scalar-only SIGLENT CH1 integration."""
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
import yaml

from microwave_ioc.siglent_sds814x.toolkit_device import (
    Controller,
    MeasurementUnavailable,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeTransport:
    def __init__(self, source='C1', reading='2.4375E-02'):
        self.source = source
        self.reading = reading
        self.commands = []

    def write(self, command):
        self.commands.append(('write', command))

    def query(self, command):
        self.commands.append(('query', command))
        if command == '*IDN?':
            return 'Siglent Technologies,SDS814X HD,TEST,1.0'
        if command == ':MEAS:SIMP:SOUR?':
            return self.source
        if command == ':MEAS:SIMP:VAL? MEAN':
            return self.reading
        raise AssertionError('Unexpected SCPI command: ' + command)


def test_scalar_ch1_read_uses_no_acquisition_or_waveform_commands():
    transport = FakeTransport()
    controller = Controller(transport)
    assert controller.initialize().startswith('Siglent Technologies')
    assert controller.read_ch1_voltage() == pytest.approx(0.024375)
    assert transport.commands == [
        ('write', 'CHDR OFF'),
        ('query', '*IDN?'),
        ('query', ':MEAS:SIMP:SOUR?'),
        ('query', ':MEAS:SIMP:VAL? MEAN'),
    ]
    assert not any(('WAV' in cmd or ':ACQ' in cmd or ':TRIG' in cmd)
                   for _, cmd in transport.commands)


def test_wrong_source_fails_closed_without_querying_reading():
    transport = FakeTransport(source='C2')
    with pytest.raises(MeasurementUnavailable, match='C1'):
        Controller(transport).read_ch1_voltage()
    assert transport.commands == [('query', ':MEAS:SIMP:SOUR?')]


@pytest.mark.parametrize('result', [
    'The number of measurements is zero', 'nan', 'inf', '-inf', '9.9e37', ''
])
def test_unavailable_measurement_is_never_reported_as_valid(result):
    transport = FakeTransport(reading=result)
    with pytest.raises(MeasurementUnavailable):
        Controller(transport).read_ch1_voltage()


def test_scope_gui_has_scalar_ch1_live_trend_without_waveform_pvs():
    gui = ROOT / 'gui/genesis_mw_scope.bob'
    tree = ET.parse(gui)
    assert not list(tree.iter('xyplot'))
    plots = list(tree.iter('widget'))
    stripcharts = [w for w in plots if w.attrib.get('type') == 'stripchart']
    assert len(stripcharts) == 1
    trend = stripcharts[0]
    assert trend.findtext('time_range') == '5 minutes'
    assert [pv.text for pv in trend.iter('y_pv')] == [
        'ca://$(P):$(C)_VI'
    ]
    assert not list(trend.iter('x_pv'))
    bound = {p.text for p in tree.iter('pv_name')}
    assert bound == {
        'ca://$(P):$(C)_VI',
        'ca://$(P):$(C)_Comm_Status',
        'ca://$(P):$(C)_Data_Valid',
        'ca://$(P):$(C)_Last_Error',
    }
    assert all('Wave_' not in item and 'History_' not in item
               for item in bound | {pv.text for pv in trend.iter('y_pv')})
    assert int(tree.getroot().findtext('height')) >= 600


def test_scope_settings_archive_only_scalar_values():
    config = yaml.safe_load((ROOT / 'configs/commissioning/microwave_physical/'
                             'siglent_sds814x/settings.yaml').read_text())
    scope = config['siglent_sds814x']
    assert scope['archive_records'] == [
        'SIG_VI', 'SIG_Comm_Status', 'SIG_Data_Valid'
    ]
    assert not any('Frame' in item or 'Wave' in item or 'History' in item
                   for item in scope['archive_records'])
    driver = (ROOT / 'microwave_ioc/siglent_sds814x/toolkit_device.py').read_text()
    assert ':WAV:' not in driver
    assert 'WaveformIn' not in driver
    assert 'snapshot(' not in driver


def test_scope_status_command_does_not_request_removed_pvs():
    script = (ROOT / 'scripts/genesisctl.sh').read_text()
    section = script.split('mw_scope_command() {', 1)[1].split('\n}\n', 1)[0]
    for name in ('"Comm_Status"', '"Data_Valid"', '"VI"', '"Last_Error"'):
        assert name in section
    assert '"Data_Age"' not in section
    assert '"Acq_Status"' not in section
