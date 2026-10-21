"""Read-only regression tests for all 16 conventional thermometers from ODS."""

import ast
import math
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
import yaml

from genesis_common.thermometry_ods import load_classic_ods

ROOT = Path(__file__).resolve().parents[1]
ODS = ROOT / 'calibration/Thermo_Calibrations.ods'
SRC = ROOT / 'target_ioc/instruments/ls224.py'


def settings(stem):
    return yaml.safe_load((ROOT / f'configs/target_physical/{stem}/settings.yaml').read_text())[stem]


def channel_slots(stem):
    conf = settings(stem)
    return dict(zip(conf['channels'], conf['slots']))


def conventional(stem):
    slots = channel_slots(stem)
    ccx = set(settings(stem).get('ccx_sensors', {}))
    return {name: slot for name, slot in slots.items() if name not in ccx}


def isolated_method(class_name, method):
    tree = ast.parse(SRC.read_text())
    cl = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == class_name)
    return next(x for x in cl.body if isinstance(x, ast.FunctionDef) and x.name == method)


def read_classic_channel(stem, channel, ohms, bits, *, curves=None):
    # Execute only read_all and the pure resistance conversion, with a fake
    # transport.  Any KRDG request is rejected; no actual device is accessed.
    tree = ast.parse(SRC.read_text())
    curve_fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef)
                    and x.name == 'resistance_to_kelvin')
    reads_fn = isolated_method('Device', 'read_all')
    decoder = next(x for x in tree.body if isinstance(x, ast.FunctionDef)
                   and x.name == 'decode_status')
    ns = {'math':math, 'ccx_kelvin':lambda *args:float('nan'),
          'STATUS_BITS':[(1, 'invalid reading'), (16, 'temp underrange'),
                         (32, 'temp overrange'), (64, 'sensor units zero'),
                         (128, 'sensor units overrange')]}
    for fn in (curve_fn, decoder, reads_fn):
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                     str(SRC), 'exec'), ns)
    conf = settings(stem)
    mapping = channel_slots(stem)
    cal = load_classic_ods(ODS, conventional(stem)) if curves is None else curves

    class FakeTransport:
        def sensor_reading(self, slot):
            assert slot == mapping[channel]
            return ohms
        def reading_status(self, slot):
            assert slot == mapping[channel]
            return bits
        def kelvin(self, slot):
            raise AssertionError('External ODS must not fall back to KRDG')

    fake = SimpleNamespace(settings=conf, t=FakeTransport(), _cal=cal,
                           _ccx={}, _slot_of=lambda name, i:mapping[name])
    return ns['read_all'](fake, channel, conf['channels'].index(channel)+1)


def test_all_configured_calibrations_use_the_single_ods():
    expected = {'ls224_fridge':11, 'ls224_magnet':5}
    for stem,count in expected.items():
        config = settings(stem)
        assert config['calibration'] == 'calibration/Thermo_Calibrations.ods'
        found = load_classic_ods(ODS, conventional(stem))
        assert len(found) == count
        for name,slot in conventional(stem).items():
            typ,coefs,sensor = found[slot]
            assert sensor == name
            assert typ in {'CCS','CCS2','CCCS'}
            assert len(coefs) == (7 if typ == 'CCCS' else 3)
            assert all(math.isfinite(v) for v in coefs)
    assert settings('ls224_magnet')['ccx_calibration'] == 'calibration/Thermo_Calibrations.ods'


@pytest.mark.parametrize('stem,channels', [
    ('ls224_fridge',['F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11']),
    ('ls224_magnet',['M1','M2','M3','M4','M5']),
])
def test_ods_applies_to_all_live_channel_names(stem,channels):
    assert sorted(conventional(stem)) == sorted(channels)
    for name in channels:
        result = read_classic_channel(stem,name,2000.0,0)
        assert math.isfinite(result[f'{name}_TI'])
        assert result[f'{name}_Status'] == 'OK'


@pytest.mark.parametrize('status',[16,32,48])
def test_instrument_kelvin_range_does_not_override_external_curve(status):
    result = read_classic_channel('ls224_fridge','F1',2000.0,status)
    assert math.isfinite(result['F1_TI'])
    assert result['F1_Status'] == 'OK; 224 TEMP RANGE'


@pytest.mark.parametrize('status',[1,2,33,64,128,'unknown',-1])
def test_sensor_and_unknown_faults_are_invalid(status):
    result=read_classic_channel('ls224_fridge','F1',2000.0,status)
    assert math.isnan(result['F1_TI'])
    assert not result['F1_Status'].startswith('OK')


def test_missing_curve_does_not_fall_back_to_lakeshore_kelvin():
    result=read_classic_channel('ls224_fridge','F1',2000.0,0,curves={})
    assert math.isnan(result['F1_TI'])
    assert result['F1_Status']=='CALIBRATION MISSING/MISMATCH'


def test_missing_file_or_bad_ods_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        load_classic_ods(tmp_path/'no.ods', {'F1':'A'})
    bad=tmp_path/'bad.ods'
    bad.write_bytes(b'bad zip')
    with pytest.raises(ValueError):
        load_classic_ods(bad,{'F1':'A'})


def test_ods_wiring_mismatch_rejected():
    with pytest.raises(ValueError,match='slot mismatch'):
        load_classic_ods(ODS,{'F1':'B'})


def test_duplicate_expected_input_rejected():
    with pytest.raises(ValueError,match='Duplicate Lake Shore slot'):
        load_classic_ods(ODS,{'F1':'A','F2':'A'})


def test_new_valid_coefficients_are_loaded_from_modified_file(tmp_path):
    # Replace only the first numeric coefficient in a copy of ODS and verify
    # the loader reads the modified value, without changing Python or CSV.
    cloned = tmp_path/'updated.ods'
    table = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
    office = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
    with ZipFile(ODS) as source:
        original = {name:source.read(name) for name in source.namelist()}
    root = ET.fromstring(original['content.xml'])
    sheet = next(t for t in root.findall('.//'+table+'table') if t.get(table+'name')=='Thermo_Calibrations')
    row = sheet.findall(table+'table-row')[6]  # F1, source spreadsheet row 7
    cells=[x for x in row if x.tag==table+'table-cell']
    coeff=cells[3]
    before=float(coeff.get(office+'value'))
    coeff.set(office+'value',str(before+0.005))
    changed=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    with ZipFile(cloned,'w') as output:
        for name,data in original.items():
            output.writestr(name,changed if name=='content.xml' else data)
    default=load_classic_ods(ODS, {'F1':'A'})['A'][1][0]
    updated=load_classic_ods(cloned, {'F1':'A'})['A'][1][0]
    assert updated == pytest.approx(default+0.005)
