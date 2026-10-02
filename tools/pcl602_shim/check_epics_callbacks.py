"""Real EPICS callback regression against a local simulated controller only.
Run from the IOC environment: python tools/pcl602_shim/check_epics_callbacks.py
No robot hardware is contacted.
"""
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
import threading
import time

def main():
    repo=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(repo))
    sys.path.insert(0,str(repo/'tools/pcl602_shim'))
    from pcl602_shim import Robot, Simulator, Server
    from softioc import builder, softioc, asyncio_dispatcher
    from microwave_ioc.pcl602 import toolkit_device as module
    cfg=json.loads((repo/'tools/pcl602_shim/config.json').read_text())
    robot=Robot(Simulator(),cfg)
    calls=Counter()
    original=robot.dispatch

    def dispatch(owner,request):
     calls[request['op']]+=1
     return original(owner,request)
    robot.dispatch=dispatch
    server=Server(('127.0.0.1',0),robot)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start()
    builder.SetDeviceName('PCL_CALLBACK_TEST')
    host,port=server.server_address
    device=module.Device('PCL_CALLBACK_TEST',dict(ip=host,port=port,timeout=3,channels=['X','Y']))
    try:
     assert device.connect()
     builder.LoadDatabase()
     dispatcher=asyncio_dispatcher.AsyncioDispatcher()
     softioc.iocInit(dispatcher)
     async def poll():
      while True:
       await asyncio.sleep(.05)
       await device.do_reads()
     dispatcher(poll)
     def put(name,value):
      softioc.dbpf('PCL_CALLBACK_TEST:'+name,str(value))
      time.sleep(.15)
     put('CONTROL_MODE',1)
     put('ESTOP',1)
     put('CONTROL_MODE',1)
     time.sleep(.2)
     print('MODE_AFTER_STOP_AND_REENABLE',int(robot.motion.mode),flush=True)
     print('CALLBACK_COUNTS',dict(calls),flush=True)
     assert robot.motion.mode==1
     assert calls['STOP_ALL']==1,calls
     assert calls['MODE']==2,calls
     put('READ_ERROR',1)
     assert calls['READ_ERROR']==1,calls
     put('X_HOME',1)
     assert calls['HOME']==0,calls
     put('X_MOVE_REL',100)
     assert calls['MOVE_REL']==1,calls
     assert robot.controller.targets['X']==100
     # Controller-side changes must refresh outputs without issuing any commands.
     before=calls.copy()
     with robot.lock:
      robot.motion.mode=0
      robot.values['X']['max_speed']=12000
      robot.values['X']['acceleration']=2000
      robot.controller.enabled['X']=False
     time.sleep(.2)
     assert calls['MODE']==before['MODE'],calls
     assert calls['MAX_SPEED']==before['MAX_SPEED'],calls
     assert calls['ACCEL']==before['ACCEL'],calls
     assert calls['MOTOR_ENABLE']==before['MOTOR_ENABLE'],calls
     assert device.pvs['CONTROL_MODE'].get()==0
     assert device.pvs['X_MOTOR_ENABLE'].get()==0
     put('X_ZERO',1)
     assert calls['ZERO']==1,calls
     put('X_STOP',1)
     assert calls['STOP']==1,calls
     print('REAL_EPICS_CALLBACK_REGRESSION_PASSED',flush=True)
    finally:
     device.close()
     server.shutdown();server.server_close();thread.join()


if __name__ == "__main__":
    main()
