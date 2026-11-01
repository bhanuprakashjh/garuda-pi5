"""Exercise camera-follow/orbit/dropout/landing using only the local simulator UI API."""
import json
import math
import os
from pathlib import Path
import time
import urllib.request

BASE='http://127.0.0.1:8099'
def command(**data):
    request=urllib.request.Request(BASE+'/api/command',data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=3) as r:return json.load(r)
def state():
    with urllib.request.urlopen(BASE+'/api/state',timeout=3) as r:return json.load(r)
def pause(value):
    request=urllib.request.Request('http://127.0.0.1:8101/api/config',data=json.dumps({'paused':value}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=3):pass

def main():
    if os.environ.get('GARUDA_SIM')!='1':raise SystemExit('GARUDA_SIM=1 required')
    start=state()
    if start['armed'] or not start['telemetry_fresh']:raise SystemExit('Requires disarmed simulator with telemetry')
    path=Path('.runtime')/('visual-flight-'+time.strftime('%Y%m%d-%H%M%S')+'.jsonl')
    rows=[]
    with path.open('x') as log:
        def sample(stage):
            s=state();s.update(test_stage=stage,wall_monotonic=time.monotonic());rows.append(s)
            log.write(json.dumps(s)+'\n');log.flush();return s
        def until(predicate,stage,timeout):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                s=sample(stage)
                if predicate(s):return s
                time.sleep(.2)
            raise RuntimeError(stage+' timed out: '+s.get('error',''))
        def period(stage,duration):
            deadline=time.monotonic()+duration
            while time.monotonic()<deadline:
                sample(stage);time.sleep(.2)
        try:
            command(command='source',kind='gazebo');command(command='settings',gap=3,speed=1,limit=15)
            until(lambda s:s['source_kind']=='gazebo' and len(s['tracks'])==1,'camera',15)
            command(command='takeoff')
            until(lambda s:s['phase']=='FLYING' and s['altitude']>1.2,'takeoff',30)
            s=until(lambda s:len(s['tracks'])==1 and s['tracks'][0].get('range_m') is not None,'target',15)
            command(command='select',id=s['tracks'][0]['id'],epoch=s['frame']['epoch'])
            command(command='follow');period('follow',25)
            print('FOLLOW sampled',flush=True)
            pause(True);period('dropout',2.5);pause(False)
            period('recovery',5)
            command(command='circle');period('circle',40)
            print('ORBIT sampled',flush=True)
            command(command='hold');period('hold',2)
        finally:
            pause(False);command(command='land')
            until(lambda s:not s['armed'] and s['landed'],'land',45)
    follow=[r for r in rows if r['test_stage']=='follow']
    moving=[r for r in follow if math.hypot(*r['intent'][:2])>.05]
    drop=[r for r in rows if r['test_stage']=='dropout'];settled=[r for r in drop if r['wall_monotonic']-drop[0]['wall_monotonic']>1]
    report=dict(log=str(path),follow_samples=len(follow),moving_samples=len(moving),
        dropout_held=all(math.hypot(*r['intent'][:2])==0 for r in settled),
        landed_disarmed=not rows[-1]['armed'] and rows[-1]['landed'],
        max_command_speed=max(math.hypot(*r['intent'][:2]) for r in rows))
    path.with_suffix('.summary.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    if not moving or not report['dropout_held'] or not report['landed_disarmed']:raise SystemExit('Visual functional checks failed')

if __name__=='__main__':main()
