"""Local PC evaluation detector and replay. Never connects to a flight controller.

Gazebo RGBD capture reused from the Hailo-15 bench; YOLOX inference is PC CPU,
not a Hailo benchmark. No actor coordinates or annotation boxes enter detection.
"""
import argparse
from collections import OrderedDict, deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import sys
import subprocess
import threading
import time
import uuid
from urllib.parse import urlparse, parse_qs

import cv2
import numpy as np
from gazebo_pixels import GazeboPixels, depth_range, jpeg

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'services/garuda-follow'))
from tracker import Tracker

class Detector:
    def __init__(self, model, engine='ort'):
        cv2.setNumThreads(4)
        self.engine=engine
        if engine=='ort':
            import onnxruntime as ort
            options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
            options.add_session_config_entry('session.intra_op.allow_spinning','0')
            self.net=ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider'])
            self.input_name=self.net.get_inputs()[0].name
        else:self.net=cv2.dnn.readNetFromONNX(str(model))
        self.sha=hashlib.sha256(Path(model).read_bytes()).hexdigest()
        self.grid=np.concatenate([np.stack(np.meshgrid(np.arange(640//s),np.arange(640//s)),axis=-1).reshape(-1,2) for s in (8,16,32)])
        self.stride=np.concatenate([np.full((640//s)**2,s) for s in (8,16,32)])[:,None]

    def infer(self,rgb):
        h,w=rgb.shape[:2];ratio=min(640/w,640/h)
        padded=np.full((640,640,3),114,dtype=np.uint8)
        padded[:int(h*ratio),:int(w*ratio)]=cv2.resize(rgb,(int(w*ratio),int(h*ratio)))
        tensor=padded.transpose(2,0,1)[None].astype(np.float32)
        if self.engine=='ort':raw=self.net.run(None,{self.input_name:tensor})[0][0]
        else:
            self.net.setInput(tensor);raw=self.net.forward()[0]
        # Decode anchor-free centre/size predictions. Person class is COCO index 0.
        scores=raw[:,4]*raw[:,5];indices=np.flatnonzero(scores>=.35)
        if not len(indices):return []
        centres=(raw[indices,:2]+self.grid[indices])*self.stride[indices]
        sizes=np.exp(raw[indices,2:4])*self.stride[indices]
        boxes=np.concatenate((centres-sizes/2,sizes),axis=1)/ratio
        keep=cv2.dnn.NMSBoxes(boxes.tolist(),scores[indices].tolist(),.35,.45)
        results=[]
        for k in np.asarray(keep).reshape(-1):
            x,y,bw,bh=map(float,boxes[k]);x0=max(0.,x);y0=max(0.,y)
            bw=min(float(w),x+bw)-x0;bh=min(float(h),y+bh)-y0
            if bw>3 and bh>5:results.append(dict(x=x0,y=y0,w=bw,h=bh,confidence=float(scores[indices[k]]),class_id=0,label='person'))
        return results

def catalog(root):
    """Existing Hailo-15 annotated aerial data, referenced in place."""
    base=Path(root)/'datasets/visdrone/VisDrone2019-SOT-val/sequences'
    result={}
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            if folder.is_dir() and any(folder.glob('*.jpg')):
                result[folder.name]=dict(path=str(folder),label='VisDrone · '+folder.name,kind='images',fps=30)
    sample = ROOT / 'samples/playback-check.avi'
    if sample.is_file():
        result['garuda-playback-check'] = dict(path=str(sample), label='Synthetic playback check (not a detection benchmark)', kind='video')
    return result

class Replay:
    def __init__(self,entry):
        self.entry=entry;self.files=sorted(Path(entry['path']).glob('*.jpg')) if entry['kind']=='images' else []
        self.cap=None if self.files else cv2.VideoCapture(entry['path'])
        if self.cap and not self.cap.isOpened():raise ValueError('Cannot open video')
        self.total=len(self.files) if self.files else int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps=entry.get('fps',30) if self.files else self.cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(self.fps) or self.fps<=0:self.fps=30
        self.index=0;self.started=time.monotonic();self.done=False
    def read(self):
        if self.index>=self.total:self.done=True;return None
        if self.files:bgr=cv2.imread(str(self.files[self.index]))
        else:ok,bgr=self.cap.read();bgr=bgr if ok else None
        if bgr is None:self.done=True;return None
        self.index+=1
        return cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
    def close(self):
        if self.cap:self.cap.release()

class Vision:
    def __init__(self,detector,clips,data_root=None):
        self.detector=detector;self.clips=clips;self.pixels=GazeboPixels()
        self.data_root=str(data_root or ROOT/'datasets')
        self.lock=threading.RLock();self.frames=OrderedDict();self.state={};self.error='Waiting for camera'
        self.mode='gazebo';self.epoch=uuid.uuid4().hex;self.version=0;self.replay=None
        self.paused=True;self.rate=1.;self.running=True;self.tracker=Tracker(max_age=3)
        self.timings=deque(maxlen=300);self.processed=0;self.started=time.monotonic()
        self.last_gz=0;self.last_at=0.;self.last_seq=0
        self.benchmark=None;self.report=None
    def configure(self,data):
        with self.lock:
            if 'benchmark' in data:
                clip=data['benchmark']
                if clip not in self.clips:raise ValueError('Unknown replay clip')
                if self.benchmark and self.benchmark.poll() is None:raise ValueError('Benchmark already running')
                self.report=ROOT/'.runtime/reports'/('replay-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.json')
                self.report.parent.mkdir(parents=True,exist_ok=True)
                with self.report.with_suffix('.log').open('wb') as log:
                    cmd=[sys.executable,str(ROOT/'bench/vision_replay.py'),'--clip',clip,'--out',str(self.report),'--data-root',self.data_root]
                    if self.clips[clip]['kind']=='video':cmd.extend(['--video',self.clips[clip]['path']])
                    self.benchmark=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
                return
            if 'paused' in data:self.paused=bool(data['paused'])
            if 'rate' in data:
                rate=float(data['rate'])
                if not math.isfinite(rate) or not .25<=rate<=2:raise ValueError('Replay rate must be 0.25–2')
                self.rate=rate
            if 'mode' in data:
                mode=data['mode']
                if mode not in ('gazebo','replay'):raise ValueError('Invalid vision mode')
                new=Replay(self.clips[data['clip']]) if mode=='replay' else None
                if self.replay:self.replay.close()
                self.mode=mode;self.replay=new;self.paused=False;self.version+=1
                self.epoch=uuid.uuid4().hex;self.state={};self.frames.clear();self.tracker=Tracker(max_age=3)
                self.timings.clear();self.processed=0;self.started=time.monotonic();self.last_gz=0
    def snapshot(self):
        with self.lock:
            out=dict(self.state);out['age_ms']=(time.monotonic()-self.last_at)*1000 if self.state else None
            frame,depth,clock,rtf=self.pixels.snapshot()
            if self.mode=='gazebo' and (not clock or time.monotonic_ns()-clock[1]>500_000_000):out['age_ms']=1e9
            out.update(error=self.error,kind=self.mode,paused=self.paused,rate=self.rate,rtf=rtf,
                benchmark='running' if self.benchmark and self.benchmark.poll() is None else 'complete' if self.report and self.report.exists() else 'failed' if self.benchmark else 'idle',
                replay=dict(frame=self.replay.index,total=self.replay.total,done=self.replay.done) if self.replay else None)
            return out
    def run(self):
        next_due=0
        while self.running:
            try:
                with self.lock:
                    version=self.version;mode=self.mode;epoch=self.epoch
                    if self.paused:frame=None
                    elif mode=='gazebo':
                        item,depth,clock,rtf=self.pixels.snapshot()
                        frame=item if item and item[0]!=self.last_gz else None
                        if frame:self.last_gz=frame[0]
                    elif self.replay and time.monotonic()>=next_due:
                        rgb=self.replay.read();depth=None
                        frame=(self.replay.index,0,time.monotonic_ns(),rgb) if rgb is not None else None
                        next_due=time.monotonic()+1/(self.replay.fps*self.rate)
                    else:frame=None
                if frame is None:time.sleep(.01);continue
                seq,sim_ns,ingress,rgb=frame;begin=time.monotonic();dets=self.detector.infer(rgb)
                finished=time.monotonic();infer_ms=(finished-begin)*1000
                with self.lock:
                    if version!=self.version:continue
                    tracks=self.tracker.update(dets)
                    observations=[]
                    if mode=='gazebo' and depth is None:
                        with self.pixels.lock:depth=self.pixels.depths.get(sim_ns)
                    for t in tracks:
                        box=dict(zip(('x','y','w','h'),t.bbox))
                        observations.append(dict(id=t.id,**box,conf=t.confidence,coasting=False,label='person',
                            range_m=depth_range(depth,box) if mode=='gazebo' else None,gesture=dict(name=None)))
                    self.last_at=ingress/1e9;self.last_seq+=1;self.processed+=1;self.timings.append(infer_ms)
                    elapsed=max(.001,finished-self.started);fseq=self.last_seq
                    self.state=dict(frame=dict(seq=fseq,epoch=epoch,width=rgb.shape[1],height=rgb.shape[0],sim_ns=sim_ns),
                        tracks=observations,detector=dict(mode='person',name='YOLOX-S · PC CPU',engine=self.detector.engine,sha256=self.detector.sha),
                        camera=dict(hfov=1.2,pitch=.12,depth_paired=depth is not None),
                        metrics=dict(frames=self.processed,processing_fps=self.processed/elapsed,inference_ms=infer_ms,
                            inference_p95_ms=float(np.percentile(self.timings,95)),ingress_to_result_ms=(finished-ingress/1e9)*1000),
                        kind=mode)
                    self.frames[(epoch,fseq)]=jpeg(rgb)
                    while len(self.frames)>8:self.frames.popitem(last=False)
                    self.error=''
            except Exception as exc:
                with self.lock:self.error=str(exc)
                time.sleep(.2)

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8101)
    p.add_argument('--model',type=Path,default=ROOT/'.runtime/models/yolox/object_detection_yolox_2022nov.onnx')
    p.add_argument('--data-root',default=str(ROOT/'datasets'))
    p.add_argument('--video',type=Path,action='append',default=[]);a=p.parse_args()
    if os.environ.get('GARUDA_SIM')!='1':raise SystemExit('GARUDA_SIM=1 required')
    clips=catalog(a.data_root)
    for path in a.video:
        path=path.resolve()
        if not path.is_file():raise SystemExit(f'Video missing: {path}')
        clips[path.stem]=dict(path=str(path),label=path.name,kind='video')
    vision=Vision(Detector(a.model),clips,a.data_root)
    threading.Thread(target=vision.run,daemon=True).start()
    class HTTP(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,code,data,kind='application/json'):
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            parsed=urlparse(self.path)
            if parsed.path=='/api/state':self.reply(200,json.dumps(vision.snapshot(),allow_nan=False).encode())
            elif parsed.path=='/api/clips':self.reply(200,json.dumps([dict(id=k,label=v['label']) for k,v in clips.items()]).encode())
            elif parsed.path=='/api/report':
                with vision.lock:path=vision.report
                self.reply(200,path.read_bytes()) if path and path.exists() else self.reply(404,b'{}')
            elif parsed.path=='/frame.jpg':
                q=parse_qs(parsed.query)
                try:key=(q['epoch'][0],int(q['seq'][0]))
                except (KeyError,ValueError):self.reply(400,b'{}');return
                with vision.lock:frame=vision.frames.get(key)
                self.reply(200,frame,'image/jpeg') if frame else self.reply(410,b'{}')
            elif parsed.path=='/overview.jpg':
                with vision.pixels.lock:frame=vision.pixels.overview
                if frame and time.monotonic_ns()-frame[0]<2e9:self.reply(200,frame[1],'image/jpeg')
                else:self.reply(503,b'{}')
            else:self.reply(404,b'{}')
        def do_POST(self):
            if self.path!='/api/config' or self.headers.get('Content-Type')!='application/json':self.reply(400,b'{}');return
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{a.port}') :self.reply(403,b'{}');return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<2048:raise ValueError('body size')
                vision.configure(json.loads(self.rfile.read(size)));self.reply(200,b'{"ok":true}')
            except (KeyError,ValueError,TypeError) as exc:self.reply(400,json.dumps(dict(error=str(exc))).encode())
    try:ThreadingHTTPServer(('127.0.0.1',a.port),HTTP).serve_forever()
    finally:vision.running=False

if __name__=='__main__':main()
