"""Local, ROS-independent demo shell and simulator launcher (stdlib only)."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import time
from lab_processes import identity
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / '.runtime/lab-config.json'
LOCK = threading.RLock()
MESSAGE = ''

def discover():
    bases = [ROOT.parent, *ROOT.parents, Path.home(), Path.home()/'Development', Path.home()/'Downloads', Path.home()/'Applications']
    def first(paths):
        return next((str(p) for p in paths if p.exists()), '')
    return dict(px4=first(b/'PX4-Autopilot' for b in bases),
                qgc=shutil.which('QGroundControl') or first(p for b in bases for p in b.glob('QGroundControl*.AppImage')),
                actor=first((Path.home()/'.gz/fuel').glob('*/mingfei/models/actor/*/meshes/walk.dae')),
                data_root=first(b/'hailo-export' for b in bases), pi_url='http://10.42.0.128:8098')

def config():
    value = discover()
    if CONFIG.exists(): value.update(json.loads(CONFIG.read_text()))
    return value

def validate(value):
    if set(value) != {'px4', 'qgc', 'actor', 'data_root', 'pi_url'}: raise ValueError('Unexpected configuration fields')
    for key in ('px4', 'qgc', 'actor', 'data_root'):
        if not isinstance(value[key], str): raise ValueError('Paths must be text')
        value[key] = str(Path(value[key]).expanduser().resolve()) if value[key].strip() else ''
    url = urllib.parse.urlsplit(value['pi_url'])
    if url.scheme != 'http' or not url.hostname or url.username or url.query or url.fragment or url.path not in ('', '/'):
        raise ValueError('Pi URL must be an HTTP address such as http://10.42.0.128:8098')
    value['pi_url'] = value['pi_url'].rstrip('/')
    return value

def processes(isolated=False):
    result = []
    for entry in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            if isolated and b'GZ_PARTITION=garuda_isolated' not in entry.with_name('environ').read_bytes().split(b'\0'): continue
            args = entry.read_bytes().decode(errors='replace').split('\0')
            if args and args[0]: result.append(args)
        except OSError: pass
    return result

def components():
    args = processes()
    gazebo_args = processes(isolated=True)
    return dict(sitl=any('start_sitl.py' in ' '.join(a[:3]) and '--gazebo-only' not in a for a in args),
                qgc=any(any('qgroundcontrol' in Path(v).name.lower() for v in a[:2]) for a in args),
                gazebo=any('--gazebo-only' in a or 'gz sim server' in ' '.join(a[:3]) or ('gz sim' in ' '.join(a[:3]) and any('garuda_lab.sdf' in v for v in a)) for a in gazebo_args))

def missing(c):
    checks = {'PX4 SITL build': Path(c['px4'])/'build/px4_sitl_default/bin/px4',
              'QGroundControl': Path(c['qgc']), 'Actor mesh': Path(c['actor']),
              'ROS environment (see README)': ROOT/'.runtime/tools/pixi',
              'Matched ROS messages (run prepare_messages.py)': ROOT/'.runtime/matched_ws/install/setup.bash',
              'DDS agent': ROOT/'.runtime/agent/bin/MicroXRCEAgent',
              'Detector weights (run fetch_vision.py)': ROOT/'.runtime/models/yolox/object_detection_yolox_2022nov.onnx',
              'Vision environment (see README)': ROOT/'.runtime/vision-env/bin/python'}
    result = [name for name, path in checks.items() if not path.is_file()]
    if not shutil.which('gz'): result.append('Gazebo gz executable')
    return result

def launch(name, argv, env):
    run = ROOT/'.runtime/processes'; run.mkdir(parents=True, exist_ok=True)
    with (run/f'{name}.log').open('ab') as log:
        subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)

def stop_managed(name):
    path=ROOT/'.runtime/processes'/f'{name}.json'
    if not path.exists(): return
    record=json.loads(path.read_text());pid=record['pid']
    if identity(pid)!=record['identity']: return
    try: os.killpg(pid,signal.SIGTERM)
    except ProcessLookupError: return
    deadline=time.monotonic()+5
    while identity(pid)==record['identity'] and time.monotonic()<deadline: time.sleep(.05)
    if identity(pid)==record['identity']: os.killpg(pid,signal.SIGKILL)

def simulation_processes():
    """Capture identities for only this checkout's simulator and isolated Gazebo."""
    result={}
    for entry in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args=entry.read_bytes().decode(errors='replace').split('\0');pid=int(entry.parent.name)
            env=entry.with_name('environ').read_bytes().split(b'\0')
            cwd=entry.with_name('cwd').resolve()
            wrapper=cwd==ROOT and any(v.endswith('/start_sitl.py') for v in args[:3])
            vehicle=Path(args[0]).name=='px4' and str(ROOT/'.runtime/sitl') in args
            gazebo=b'GZ_PARTITION=garuda_isolated' in env and 'gz sim' in ' '.join(args[:3])
            if wrapper or vehicle or gazebo: result[pid]=identity(pid)
        except OSError: pass
    return result

def reset_simulation():
    global MESSAGE
    if os.environ.get('GARUDA_SIM')!='1': raise ValueError('GARUDA_SIM=1 required')
    if not LOCK.acquire(blocking=False): raise ValueError('Lab startup is already running')
    try:
        absent=missing(config())
        if absent: raise ValueError('Setup required: '+', '.join(absent))
        # Reset explicitly discards only a simulated flight, even if cached state says armed.
        stop_managed('dashboard')
        captured=simulation_processes()
        for pid,stamp in captured.items():
            if stamp and identity(pid)==stamp:
                try: os.kill(pid,signal.SIGTERM)
                except ProcessLookupError: pass
        deadline=time.monotonic()+7
        while any(stamp and identity(pid)==stamp for pid,stamp in captured.items()) and time.monotonic()<deadline: time.sleep(.1)
        for pid,stamp in captured.items():
            if stamp and identity(pid)==stamp:
                try: os.kill(pid,signal.SIGKILL)
                except ProcessLookupError: pass
        stop_managed('agent')
        start()
        MESSAGE='Simulation reset. Select your target again after telemetry connects.'
    finally: LOCK.release()

def start():
    global MESSAGE
    if os.environ.get('GARUDA_SIM') != '1': raise ValueError('Restart launcher with GARUDA_SIM=1')
    if not LOCK.acquire(blocking=False): return
    try:
        c = config(); absent = missing(c)
        if absent: raise ValueError('Setup required: '+', '.join(absent))
        live = components()
        if live['sitl'] and not live.get('gazebo'):
            raise ValueError('Gazebo closed but PX4 is still running. Use Reset simulation to start a fresh flight.')
        if not live['sitl']:
            if any(Path(a[0]).name=='px4' and '-i' in a and a[a.index('-i')+1]=='3' for a in processes()):
                raise ValueError('PX4 instance 3 already exists outside the launcher; use Reset simulation to recover this lab')
            stop_managed('dashboard');stop_managed('agent')
        env = dict(os.environ, GARUDA_SIM='1', GARUDA_PI_URL=c['pi_url'], GARUDA_DATA_ROOT=c.get('data_root',''))
        completed = subprocess.run([sys.executable, 'hosts/pc/lab_processes.py', 'start'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
        if completed.returncode: raise ValueError(completed.stderr or completed.stdout)
        if not live['qgc']:
            qenv = dict(env, XDG_CONFIG_HOME=str(ROOT/'.runtime/qgc-config'), XDG_DATA_HOME=str(ROOT/'.runtime/qgc-data'))
            launch('qgc', [c['qgc']] + (['--appimage-extract-and-run'] if c['qgc'].endswith('.AppImage') else []), qenv)
        if not live['sitl']:
            launch('sitl', [sys.executable, 'hosts/pc/start_sitl.py', '--visual', '--px4', c['px4'], '--actor', c['actor']] + (['--attach'] if live.get('gazebo') else []), env)
        MESSAGE = 'Start requested. Waiting for PX4 telemetry; logs are in .runtime/processes.'
    finally: LOCK.release()

class HTTP(BaseHTTPRequestHandler):
    def reply(self, code, data, kind='application/json'):
        if not isinstance(data, bytes): data = json.dumps(data).encode()
        self.send_response(code); self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def trusted(self):
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        return host in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}') and (not origin or origin == 'http://'+host)
    def do_GET(self):
        if not self.trusted(): return self.reply(403, {'error':'Local requests only'})
        if self.path=='/': return self.reply(200, (ROOT/'hosts/pc/demo.html').read_bytes(), 'text/html; charset=utf-8')
        if self.path=='/api/lab':
            c=config();return self.reply(200,dict(config=c,components=components(),missing=missing(c),message=MESSAGE))
        self.proxy()
    def do_POST(self):
        global MESSAGE
        if not self.trusted() or self.headers.get('Content-Type')!='application/json': return self.reply(403, {'error':'Local JSON requests only'})
        if self.path not in ('/api/lab/start','/api/lab/config','/api/lab/reset'): return self.proxy()
        try:
            size=int(self.headers.get('Content-Length',0))
            if size>8192: raise ValueError('Request too large')
            data=json.loads(self.rfile.read(size))
            if self.path.endswith('/config'):
                c=validate(data);CONFIG.parent.mkdir(parents=True,exist_ok=True)
                tmp=CONFIG.with_suffix('.tmp');tmp.write_text(json.dumps(c,indent=2));tmp.replace(CONFIG)
                MESSAGE='Settings saved. Running processes keep their settings until their next start.'
            elif self.path.endswith('/reset'): reset_simulation()
            else: start()
            self.reply(200,{'message':MESSAGE})
        except (ValueError,OSError,subprocess.TimeoutExpired) as exc:
            MESSAGE=str(exc);self.reply(400,{'error':MESSAGE})
    def proxy(self):
        if not self.path.startswith(('/api/state','/api/command','/vision/')): return self.reply(404, {})
        try:
            size=int(self.headers.get('Content-Length',0))
            if size>8192: return self.reply(413,{})
            body=self.rfile.read(size) if self.command=='POST' else None
            request=urllib.request.Request('http://127.0.0.1:8099'+self.path, data=body, headers={'Content-Type':'application/json'}, method=self.command)
            with urllib.request.urlopen(request, timeout=3) as r: self.reply(r.status,r.read(),r.headers.get('Content-Type','application/json'))
        except urllib.error.HTTPError as exc: self.reply(exc.code,exc.read())
        except (OSError,urllib.error.URLError): self.reply(503,{'error':'PX4 service is stopped. Use Start PX4 lab.'})
    def log_message(self, *args): pass

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8100);a=p.parse_args()
    print(f'GARUDA main window: http://127.0.0.1:{a.port}/',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),HTTP).serve_forever()

if __name__=='__main__': main()
