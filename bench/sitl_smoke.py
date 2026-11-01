"""Explicit software-aircraft takeoff/land acceptance test against the isolated lab."""
import json
import os
from pathlib import Path
import time
import urllib.request

BASE='http://127.0.0.1:8099'

def state():
    with urllib.request.urlopen(BASE+'/api/state',timeout=2) as r:return json.load(r)

def command(name):
    req=urllib.request.Request(BASE+'/api/command',data=json.dumps({'command':name}).encode(),
                              headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=2) as r:return json.load(r)

def main():
    if os.environ.get('GARUDA_SIM')!='1':raise SystemExit('GARUDA_SIM=1 required')
    s=state()
    if s['armed'] or not s['telemetry_fresh']:raise SystemExit('Requires disarmed SITL with fresh telemetry')
    log=Path('.runtime/takeoff-land.jsonl');log.parent.mkdir(exist_ok=True)
    seen=[]
    def wait_for(predicate,seconds):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            s=state();seen.append(s)
            with log.open('a') as f:f.write(json.dumps({'wall_time':time.time(),**s})+'\n')
            if predicate(s):return s
            time.sleep(.2)
        raise RuntimeError('Timeout: '+json.dumps(s))
    try:
        command('takeoff')
        s=wait_for(lambda s:s['armed'] and s['phase']=='FLYING' and s['altitude']>1.2,25)
        print('TAKEOFF PASS',s['altitude'],s['ack'],flush=True)
    finally:command('land')
    s=wait_for(lambda s:not s['armed'] and s['landed'] and s['phase']=='GROUND',45)
    print('LAND PASS',s['altitude'],s['ack'],flush=True)
    print('Samples',len(seen),'max_altitude',max(s['altitude'] for s in seen),'log',log)

if __name__=='__main__':main()
