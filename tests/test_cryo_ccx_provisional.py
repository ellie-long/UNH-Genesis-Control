"""Offline four-band target-stick 2 CCX validation; never connects to hardware."""
import ast
import csv
import math
from pathlib import Path
from zipfile import ZipFile
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
import yaml
from genesis_common.cernox_ccx import load_ccx_bands, ccx_kelvin

ROOT=Path(__file__).resolve().parents[1]
CONF=ROOT/'configs/target_physical/ls224_magnet/settings.yaml'
ODS=ROOT/'calibration/Thermo_Calibrations.ods'
SRC=ROOT/'target_ioc/instruments/ls224.py'


def config():
    return yaml.safe_load(CONF.read_text())['ls224_magnet']


def run_channel(channel, ohms, raw_status=0, curves=None, settings=None):
    tree=ast.parse(SRC.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Device')
    m=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='read_all')
    namespace={
        'decode_status':lambda raw: ', '.join(label for bit,label in
            ((1,'invalid reading'),(16,'temp underrange'),(32,'temp overrange'),
             (64,'sensor units zero'),(128,'sensor units overrange'))
            if int(raw) & bit) if str(raw).strip().isdigit() else 'unreadable status',
        'ccx_kelvin':ccx_kelvin,'math':math,
        'resistance_to_kelvin': lambda *args:float('nan'),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[m],type_ignores=[])),str(SRC),'exec'),namespace)
    d=settings or config()
    slots=dict(zip(d['channels'],d['slots']))
    class Transport:
        def sensor_reading(self, slot):
            assert slot == slots[channel]
            return ohms
        def reading_status(self,slot):return raw_status
        def kelvin(self,slot):raise AssertionError('Must not use instrument KRDG?')
    fake=SimpleNamespace(settings=d, t=Transport(), _cal={},
        _ccx=curves if curves is not None else load_ccx_bands(ODS),
        _slot_of=lambda name, index: slots[name])
    return namespace['read_all'](fake,channel,d['channels'].index(channel)+1)


def test_explicit_source_identity_and_readonly():
    d=config()
    assert d['ip']=='192.168.1.13'
    assert dict(zip(d['channels'],d['slots']))['T1']=='C4'
    assert dict(zip(d['channels'],d['slots']))['T3']=='C2'
    assert d['ccx_calibration']=='calibration/Thermo_Calibrations.ods'
    assert d['ccx_sensors']=={'T1':'2-T1','T3':'2-T3'}
    assert set(d['ccx_required'])=={'T1','T3'}
    assert 'ccx_provisional' not in d
    assert 'ccx_kelvin_bounds' not in d
    assert d['write_enabled'] is False


def test_targetstick_2_ods_is_complete():
    # The ODS is the sole source of calibration coefficients. Do not assert
    # equality to an old CSV extraction: changing valid ODS coefficients must
    # not require editing code, tests, or another calibration data file.
    source=load_ccx_bands(ODS)
    assert set(source)=={'2-T1','2-T3'}
    for name in ('2-T1','2-T3'):
        assert len(source[name])==4
        for zl,zu,coeff in source[name]:
            assert math.isfinite(zl) and math.isfinite(zu) and zl < zu
            assert len(coeff)==10 and all(math.isfinite(a) for a in coeff)
    # Check the source workbook is genuinely an ODS with the expected rows.
    with ZipFile(ODS) as archive:
        root=ET.fromstring(archive.read('content.xml'))
    ns='{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
    table=next(t for t in root.findall('.//'+ns+'table') if t.get(ns+'name')=='Thermo_Calibrations')
    rows=table.findall(ns+'table-row')
    assert len(rows)>=22


def test_live_resistances_offline_kelvin():
    curves=load_ccx_bands(ODS)
    for channel, source, ohms in [('T1','2-T1',40.049),('T3','2-T3',47.929)]:
        reading=run_channel(channel,ohms,32)
        expected=ccx_kelvin(curves[source],ohms)
        assert reading[f'{channel}_RI']==ohms
        assert math.isfinite(expected)
        assert reading[f'{channel}_TI']==pytest.approx(expected,abs=1e-8)
        assert reading[f'{channel}_Status']=='OK; 224 TEMP RANGE'


@pytest.mark.parametrize('status',[0,16,32,48])
@pytest.mark.parametrize('channel,source,ohms',[('T1','2-T1',40.049),
                                              ('T3','2-T3',47.929)])
def test_internal_kelvin_range_flag_not_sensor_failure(channel,source,ohms,status):
    expected=ccx_kelvin(load_ccx_bands(ODS)[source],ohms)
    assert math.isfinite(expected)
    out=run_channel(channel,ohms,status)
    assert out[f'{channel}_TI']==pytest.approx(expected,abs=1e-8)
    assert out[f'{channel}_Status']==('OK' if status==0 else 'OK; 224 TEMP RANGE')


@pytest.mark.parametrize('status',[1,2,33,64,128,'garbled',-1])
@pytest.mark.parametrize('channel,ohms',[('T1',40.049),('T3',47.929)])
def test_sensor_faults_unknown_bits_fail_closed(channel,ohms,status):
    out=run_channel(channel,ohms,status)
    assert math.isnan(out[f'{channel}_TI'])
    assert not out[f'{channel}_Status'].startswith('OK')


@pytest.mark.parametrize('channel',["T1","T3"])
@pytest.mark.parametrize('r',[0,-1,float('nan'),float('inf'),1,1e12])
def test_bad_or_uncovered_resistance_rejected(channel,r):
    assert math.isnan(run_channel(channel,r,32)[f'{channel}_TI'])


def test_missing_ods_curve_does_not_fallback_to_kelvin():
    for channel,r in [('T1',40.049),('T3',47.929)]:
        out=run_channel(channel,r,32,curves={})
        assert math.isnan(out[f'{channel}_TI'])
        assert out[f'{channel}_Status']=='CCX CALIBRATION MISSING'


def test_corrupt_ods_rejected(tmp_path):
    p=tmp_path/'bad.ods';p.write_bytes(b'not a zip')
    with pytest.raises(ValueError):load_ccx_bands(p)


def test_reference_recurrence_each_band():
    # Independently evaluate Chebyshev Tn via recurrence, avoiding cosine.
    curves=load_ccx_bands(ODS)
    for source in curves:
        for zl,zu,a in curves[source]:
            z=(zl+zu)/2
            r=10**z
            k=(2*z-zl-zu)/(zu-zl)
            prev,cur=1.0,k
            value=a[0]+a[1]*cur
            for coef in a[2:]:
                nex=2*k*cur-prev
                value+=coef*nex
                prev,cur=cur,nex
            assert math.isfinite(ccx_kelvin(curves[source],r))
            # Midpoint may be covered by a neighboring overlapping band;
            # choose probe z with matching widest margin if needed.
            containing=[b for b in curves[source] if b[0]<=z<=b[1]]
            chosen=max(containing,key=lambda b:min(z-b[0],b[1]-z))
            if chosen==(zl,zu,a):
                assert ccx_kelvin(curves[source],r)==pytest.approx(value,abs=1e-8)


def test_gui_labels_match_current_stick():
    overview=(ROOT/'gui/genesis_cryo.bob').read_text()
    magnet=(ROOT/'gui/genesis_cryo_magnet.bob').read_text()
    assert 'T1 C4 (K)' in overview and 'T3 C2 (K)' in overview
    assert 'T1 C4 / 2-T1' in magnet and 'T3 C2 / 2-T3' in magnet
    for text in (overview,magnet):
        root=ET.fromstring(text)
        pvs=[w.findtext('pv_name') or '' for w in root.iter('widget')]
        for channel in ('T1','T3'):
            for suffix in ('TI','RI','Status'):
                if suffix=='RI':continue
                assert any(p.endswith(f'{channel}_{suffix}') for p in pvs)


def test_csv_legacy_slot_names_consistent():
    path=ROOT/'calibration/magnet_thermometry.csv'
    with path.open(encoding='utf-8-sig',newline='') as fh:
        rows={row['Name']:row for row in csv.DictReader(fh)}
    assert rows['T1']['Lakeshore_Slot']=='C4'
    assert rows['T3']['Lakeshore_Slot']=='C2'
