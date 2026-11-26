"""Bounded encoded-video relay. GStreamer parses/payloads; OAK alone encodes.

On backlog/pipe error restart the payloader and wait for a random-access frame,
prepending cached codec parameters. Never delay sensor acquisition on network IO.
"""
import ipaddress
import os
import queue
import re
import select
import shutil
import subprocess
import threading
import time


def nals(data):
    return [p for p in re.split(b'\x00\x00\x00?\x01',data) if p]


def nal_type(nal,codec):
    return (nal[0]&31) if codec=='h264' else ((nal[0]>>1)&63)


def command(codec,host,port):
    ipaddress.ip_address(host)
    if codec not in ('h264','h265') or not 1<=port<=65535: raise ValueError('invalid video target')
    return ['gst-launch-1.0','-q','fdsrc','fd=0','do-timestamp=true','!',codec+'parse','!',
            'rtp'+codec+'pay','pt=96','config-interval=-1','mtu=1200','!',
            'udpsink','host='+host,'port='+str(port),'sync=false','async=false']


class VideoLink:
    def __init__(self,codec,host,port):
        self.cmd=command(codec,host,port);self.codec=codec;self.q=queue.Queue(4)
        self.stop=threading.Event();self.restart=threading.Event();self.proc=None
        self.parameters={};self.stats=dict(frames_in=0,bytes_in=0,frames_sent=0,dropped=0,restarts=0,error=None)
        if not shutil.which('gst-launch-1.0'): raise RuntimeError('gst-launch-1.0 not installed')
        for element in ('fdsrc',codec+'parse','rtp'+codec+'pay','udpsink'):
            subprocess.run(['gst-inspect-1.0',element],check=True,stdout=subprocess.DEVNULL,timeout=10)
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def submit(self,data):
        self.stats['frames_in']+=1;self.stats['bytes_in']+=len(data)
        try:self.q.put_nowait(data)
        except queue.Full:
            self.stats['dropped']+=1;self.restart.set()

    def _close(self):
        if self.proc:
            self.proc.terminate()
            try:self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
            self.proc.stdin.close();self.proc=None

    def _run(self):
        awaiting=True
        try:
            while not self.stop.is_set():
                if self.restart.is_set():
                    self._close();awaiting=True
                    while True:
                        try:self.q.get_nowait();self.stats['dropped']+=1
                        except queue.Empty:break
                    self.restart.clear()
                try:data=self.q.get(timeout=.1)
                except queue.Empty:continue
                parts=nals(data);types=[nal_type(n,self.codec) for n in parts]
                required=(7,8) if self.codec=='h264' else (32,33,34)
                for kind,n in zip(types,parts):
                    if kind in required:self.parameters[kind]=n
                key=any(t==5 for t in types) if self.codec=='h264' else any(t in (19,20,21) for t in types)
                if awaiting:
                    if not key or not all(t in self.parameters for t in required):
                        self.stats['dropped']+=1;continue
                    data=b''.join(b'\0\0\0\1'+self.parameters[t] for t in required)+data
                    self.proc=subprocess.Popen(self.cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL)
                    os.set_blocking(self.proc.stdin.fileno(),False);self.stats['restarts']+=1
                    awaiting=False
                try:
                    view=memoryview(data);deadline=time.monotonic()+.25
                    while view:
                        remaining=deadline-time.monotonic()
                        if remaining<=0 or not select.select([], [self.proc.stdin], [], remaining)[1]:
                            raise TimeoutError('video pipe backlog')
                        try:n=os.write(self.proc.stdin.fileno(),view)
                        except BlockingIOError:continue
                        view=view[n:]
                    self.stats['frames_sent']+=1;self.stats['last_sent_monotonic']=time.monotonic();self.stats['error']=None
                except (OSError,TimeoutError) as exc:
                    self.stats['error']=str(exc);self.stats['dropped']+=1;self.restart.set()
        except Exception as exc:
            self.stats['error']='relay worker failed: '+str(exc)
        finally:self._close()

    def close(self):
        self.stop.set();self.thread.join(timeout=3)
