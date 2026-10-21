#!/usr/bin/env python3
"""Read-only EPICS acceptance for both Cryo Ionivac owners and core state."""
import asyncio
import math
import os
from pathlib import Path
import yaml

root=Path(__file__).resolve().parents[1]
cfg=yaml.safe_load((root/'configs/target_physical/ioc_manager/settings.yaml').read_text())
ports=sorted({int(v['ca_server_port']) for v in cfg.values()
              if isinstance(v,dict) and 'ca_server_port' in v})
os.environ['EPICS_CA_AUTO_ADDR_LIST']='NO'
os.environ['EPICS_CA_ADDR_LIST']=' '.join(f'127.0.0.1:{p}' for p in ports)
os.environ['EPICS_CA_MAX_ARRAY_BYTES']='262144'
from aioca import caget

async def read(pv):
    try:
        return await caget(pv,timeout=3)
    except Exception as e:
        print(f'[MISSING] {pv}: {e}')
        return None

async def main():
    fails=[]
    for label,base in (
       ('FRIDGE-SHELL 5006','UNH:GEN:CRYO:ION'),
       ('PUMP-STATION 5005','UNH:GEN:CRYO:ION:PUMP')):
        print('\n=== '+label+' ===')
        status=await read(base+':COMM')
        pressure=await read(base+':Ion_PI')
        raw=await read(base+':Ion_Raw')
        hb=await read(base+(':MAN:ionivac_time' if label.startswith('FRIDGE') else ':MAN:ionivac_pump_time'))
        print('COMM=',status,'Ion_PI (Torr)=',pressure,'Ion_Raw=',raw,'heartbeat=',hb)
        if status is None or int(status)!=1:
            fails.append(label+' COMM not 1')
        try:
            if not math.isfinite(float(pressure)) or float(pressure)<=0:
                raise ValueError('nonpositive or nonfinite')
            if not 0<=int(raw)<=65535:
                raise ValueError('telegram outside uint16')
        except (TypeError,ValueError,OverflowError):
            fails.append(label+' invalid pressure/raw')
    print('\n=== SYSTEM SUMMARY ===')
    for name in ('FRIDGE:COMM','MAG:COMM','VAC:COMM','FIELD:COMM',
                 'COLDBOARD:COMM','ARCH:Archive_Status','ARCH:Archive_PV_Count',
                 'ALARM:Alarm_Missing'):
        value=await read('UNH:GEN:CRYO:'+name)
        print(name,'=',value)
    if fails:
        raise SystemExit('[FAIL] '+ '; '.join(fails))
    print('[PASS] Both independent Ionivac owners return finite positive pressure with COMM=1')

if __name__=='__main__':
    asyncio.run(main())
