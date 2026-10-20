"""Contract for separate fridge-shell and pumping-cart IONIVAC readbacks."""
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

ROOT=Path(__file__).resolve().parents[1]


def parse(relative):
    return yaml.safe_load((ROOT/relative).read_text())


def test_independent_ionivac_iocs_and_coldboard():
    mgr=parse('configs/target_physical/ioc_manager/settings.yaml')
    shell=parse('configs/target_physical/ionivac/settings.yaml')['ionivac']
    pump=parse('configs/target_physical/ionivac_pump/settings.yaml')['ionivac_pump']
    cold=parse('configs/target_physical/siglent_coldboard/settings.yaml')['siglent_coldboard']
    assert (shell['ip'], shell['port'],shell['write_enabled']) == ('192.168.1.251',5006,False)
    assert (pump['ip'],pump['port'],pump['write_enabled']) == ('192.168.1.251',5005,False)
    assert (cold['ip'],cold['port'],cold['write_enabled']) == ('192.168.1.18',5025,False)
    assert shell['module']==pump['module']=='target_ioc.instruments.ionivac'
    assert mgr['ionivac_pump']['ca_server_port']==5129
    assert mgr['ionivac_pump']['pva_server_port']==5229


def test_both_gauges_visible_and_archived():
    archive=parse('configs/target_physical/archiver/settings.yaml')['archiver']['explicit_pvs']
    for name in ('Ion_PI','Ion_Raw'):
        assert f'UNH:GEN:CRYO:ION:PUMP:{name}' in archive
    pv = 'ca://UNH:GEN:CRYO:ION:PUMP:Ion_PI'
    vacuum=ET.parse(ROOT/'gui/genesis_cryo_vacuum.bob').getroot()
    expert=ET.parse(ROOT/'gui/genesis_cryo_ion_expert.bob').getroot()
    for tree in (vacuum,expert):
        assert any(x.text==pv for x in tree.iter('pv_name'))
        assert not tree.findall('.//action[@type="write_pv"]')
    trend=ET.parse(ROOT/'gui/genesis_cryo_ion_expert.plt').getroot()
    assert any(x.text==pv[5:] for x in trend.iter('name'))


def test_lifecycle_manages_pump_ioc_and_keeps_optional_out():
    script=(ROOT/'scripts/cryo_physical.sh').read_text()
    assert script.count('    ionivac_pump\n') >= 3
    assert '"UNH:GEN:CRYO:ION:PUMP:WRITE_ENABLED"' in script
    assert '"UNH:GEN:CRYO:ION:PUMP:COMM"' in script
