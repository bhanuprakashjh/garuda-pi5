"""Bounded host pipeline primitives, independent of camera and NPU SDKs."""
from collections import deque
import threading
import time


class Trace:
    def __init__(self,capacity=8192):
        self.rows=deque(maxlen=capacity);self.next_id=0;self.lock=threading.Lock()
    def add(self,kind,**values):
        with self.lock:
            self.rows.append(dict(id=self.next_id,kind=kind,**values));self.next_id+=1
    def snapshot(self,after=-1):
        with self.lock:
            return dict(next_id=self.next_id,overrun=bool(self.rows and after>=0 and after<self.rows[0]['id']-1),
                        records=[dict(r) for r in self.rows if r['id']>after])


class LatestJob:
    def __init__(self):
        self.cv=threading.Condition();self.job=None;self.closed=False;self.overwrites=0
    def put(self,job):
        with self.cv:
            if self.closed:return
            if self.job is not None:self.overwrites+=1
            self.job=job;self.cv.notify()
    def take(self):
        with self.cv:
            self.cv.wait_for(lambda:self.job is not None or self.closed)
            job=self.job;self.job=None;return job
    def close(self):
        with self.cv:self.closed=True;self.job=None;self.cv.notify_all()


class TripleSelector:
    """Wait briefly for delayed mono mates; never reuses an exposure."""
    def __init__(self,skew_ns=5_000_000,wait_ns=50_000_000,capacity=8,rgb_skew_ns=None):
        self.frames={k:deque(maxlen=capacity) for k in ('rgb','left','right')}
        self.skew_ns=skew_ns;self.wait_ns=wait_ns
        self.rgb_skew_ns=skew_ns if rgb_skew_ns is None else rgb_skew_ns
        self.used={k:-1 for k in self.frames}
        self.stats=dict(skew_rejections=0,selection_skips=0,history_overwrites=0)
    def add(self,name,packet):
        q=self.frames[name]
        if len(q)==q.maxlen:self.stats['history_overwrites']+=1
        q.append(packet)
    def clear(self):
        for q in self.frames.values():q.clear()
    def select(self,now=None):
        now=time.monotonic_ns() if now is None else now
        rgbs=[f for f in self.frames['rgb'] if f['seq']>self.used['rgb']]
        # Newest complete triple; keep a newly arrived unmatched RGB pending.
        for rgb in reversed(rgbs):
            chosen={'rgb':rgb}
            for name in ('left','right'):
                candidates=[f for f in self.frames[name] if f['seq']>self.used[name]]
                if candidates:chosen[name]=min(candidates,key=lambda f:abs(f['device_ns']-rgb['device_ns']))
            skew=max(f['device_ns'] for f in chosen.values())-min(f['device_ns'] for f in chosen.values())
            if len(chosen)==3 and abs(chosen['left']['device_ns']-chosen['right']['device_ns'])<=self.skew_ns and skew<=self.rgb_skew_ns:
                self.stats['selection_skips']+=sum(f['seq']<rgb['seq'] for f in rgbs)
                for k,f in chosen.items():
                    self.used[k]=f['seq']
                    while self.frames[k] and self.frames[k][0]['seq']<=f['seq']:
                        self.frames[k].popleft()
                return chosen
            if now-rgb['dequeue_ns']>=self.wait_ns:
                self.stats['skew_rejections']+=1;self.used['rgb']=max(self.used['rgb'],rgb['seq']);break
        return None


class FrameRing:
    """Recent mono frames and RGB timestamps, so a stereo pair can be found for a
    colour frame the detector has just finished with. Limits are the same as the
    full-frame path: left/right within `skew_ns`, all three within `rgb_skew_ns`."""
    def __init__(self,capacity=24,skew_ns=5_000_000,rgb_skew_ns=33_333_333):
        self.frames={k:deque(maxlen=capacity) for k in ('rgb','left','right')}
        self.skew_ns=skew_ns;self.rgb_skew_ns=rgb_skew_ns;self.lock=threading.Lock()
    def add(self,name,packet):
        with self.lock:self.frames[name].append(packet)
    def clear(self):
        with self.lock:
            for q in self.frames.values():q.clear()
    def find(self,rgb_host_ns,tolerance_ns=1000):
        """Returns (triple, None) or (None, reason). The detector reports the RGB host
        capture time through a float, hence the 1 microsecond tolerance."""
        with self.lock:
            rgbs=list(self.frames['rgb']);lefts=list(self.frames['left']);rights=list(self.frames['right'])
        rgb=min(rgbs,key=lambda f:abs(f['host_ns']-rgb_host_ns),default=None)
        if rgb is None or abs(rgb['host_ns']-rgb_host_ns)>tolerance_ns:return None,'frame_not_in_history'
        if not lefts or not rights:return None,'no_mono_frames'
        left=min(lefts,key=lambda f:abs(f['device_ns']-rgb['device_ns']))
        right=min(rights,key=lambda f:abs(f['device_ns']-left['device_ns']))
        stamps=[f['device_ns'] for f in (rgb,left,right)]
        if abs(left['device_ns']-right['device_ns'])>self.skew_ns:return None,'left_right_outside_limit'
        if max(stamps)-min(stamps)>self.rgb_skew_ns:return None,'rgb_outside_limit'
        return dict(rgb=rgb,left=left,right=right),None


def require_exclusive_hailo(environ=None,run=None):
    """Only the explicit transient bench runner may select live Hailo stereo.

    Runner declares systemd Conflicts/After with mono. This is orchestration,
    not a security boundary against arbitrary external device users.
    """
    import os,subprocess
    environ=os.environ if environ is None else environ
    run=subprocess.run if run is None else run
    if environ.get('GARUDA_EXCLUSIVE_STEREO')!='1':
        raise RuntimeError('live Hailo stereo requires run-hailo-stereo-check.sh; standing selection refused')
    for unit in ('garuda-mono.service','garuda-mono-trial.service'):
        result=run(['systemctl','show',unit,'-p','ActiveState','--value'],capture_output=True,text=True)
        if result.returncode or result.stdout.strip() not in ('inactive','failed'):
            raise RuntimeError('NPU competitor active or unknown: '+unit)
