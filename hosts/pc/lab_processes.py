"""Own only the experiment's bridge/dashboard processes, with local logs and PID checks."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'.runtime/processes'

def identity(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]
    except (FileNotFoundError,ProcessLookupError):return None

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','stop','status'])
    p.add_argument('--only',choices=['vision','agent','dashboard']);a=p.parse_args()
    RUN.mkdir(parents=True,exist_ok=True)
    commands={
        'vision':[str(ROOT/'.runtime/vision-env/bin/python'),'hosts/pc/vision.py','--data-root',(os.environ.get('GARUDA_DATA_ROOT') or str(ROOT/'datasets'))],
        'agent':[str(ROOT/'.runtime/agent/bin/MicroXRCEAgent'),'udp4','-p','8889','-v','2'],
        'dashboard':[str(ROOT/'.runtime/tools/pixi'),'run','bash','hosts/pc/run_ros.sh','--source',os.environ.get('GARUDA_PI_URL','http://10.42.0.128:8098')],
    }
    if a.action=='start' and os.environ.get('GARUDA_SIM')!='1':raise SystemExit('GARUDA_SIM=1 required')
    if a.only:commands={a.only:commands[a.only]}
    if a.action=='stop':
        try:
            with urllib.request.urlopen('http://127.0.0.1:8099/api/state',timeout=1) as response:state=json.load(response)
        except Exception:state={}
        if state.get('armed'):raise SystemExit('Land the simulated aircraft before stopping lab services')
    for name,cmd in commands.items():
        file=RUN/f'{name}.json';record=json.loads(file.read_text()) if file.exists() else {}
        alive=bool(record) and identity(record['pid'])==record['identity']
        if a.action=='stop' and alive:
            os.killpg(record['pid'],signal.SIGTERM);print(name,'stop requested');continue
        if a.action=='start' and not alive:
            env=dict(os.environ,GARUDA_SIM='1',ROS_DOMAIN_ID='42',ROS_LOCALHOST_ONLY='1',GZ_PARTITION='garuda_isolated',PYTHONNOUSERSITE='1')
            if name=='agent':env['LD_LIBRARY_PATH']=str(ROOT/'.runtime/agent/lib')
            with (RUN/f'{name}.log').open('ab') as log:
                process=subprocess.Popen(cmd,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            record={'pid':process.pid,'identity':identity(process.pid)};file.write_text(json.dumps(record))
            time.sleep(.3)
            if process.poll() is not None:raise SystemExit(f'{name} failed; inspect {RUN/name}.log')
            print(name,'started',process.pid)
        else:print(name,'running' if alive else 'stopped')

if __name__=='__main__':main()
