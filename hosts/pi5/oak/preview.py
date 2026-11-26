#!/usr/bin/env python3
"""OAK RGB bench preview. JPEG encoding on OAK; no inference or flight commands."""
import argparse
import hashlib
from pathlib import Path
from pairing import Pairing, Frame, DEPTH_ENCODINGS, pack_depth_preview
from geometry import from_transform
from imu_stream import IMUHistory
from video_link import VideoLink
import json
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

import depthai as dai

PAGE = b'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GARUDA | OAK live camera</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#071019;color:#e9f1f6;font:15px system-ui}
header{display:flex;align-items:center;gap:20px;flex-wrap:wrap;padding:15px 24px;border-bottom:1px solid #294050}
h1{font-size:20px;margin:0}a{color:#6cdbff}button{background:#163348;color:white;border:1px solid #446476;border-radius:6px;padding:8px 14px;cursor:pointer}
main{padding:16px;max-width:1440px;margin:auto}.view{background:#000;display:flex;align-items:center;justify-content:center;min-height:240px;border-radius:8px;overflow:hidden}
img{display:block;max-width:100%;max-height:calc(100dvh - 155px);object-fit:contain}.view:fullscreen{border-radius:0}.view:fullscreen img{max-height:100dvh;width:100%;height:100%;object-fit:contain}
p{color:#afc3d0;margin:10px 0}#status{margin-left:auto;color:#ffc67a}
</style><header><h1>GARUDA <span style="color:#6cdbff">OAK live camera</span></h1>
<a id="demo">Flight demo</a><button id="full">Fullscreen</button><span id="status">Connecting...</span></header>
<main><div class="view" id="view"><img id="feed" src="/stream" alt="Live OAK RGB camera"></div>
<p>OAK-D Pro RGB &middot; 1280 &times; 720 &middot; Camera preview only &middot; IR off</p>
<p>Live camera video. Detection, tracking and flight commands are not enabled on this preview.</p></main>
<script>
document.getElementById('demo').href=location.protocol+'//'+location.hostname+':8098/demo';
document.getElementById('full').onclick=()=>document.getElementById('view').requestFullscreen();
let failed=false;
async function status(){try{const r=await fetch('/health',{cache:'no-store'});const s=await r.json();
// age_s measures time since the last published preview JPEG. Depth may be idle.
const ok=Number.isFinite(s.age_s)&&s.age_s>=0&&s.age_s<2&&!s.error;
document.getElementById('status').textContent=ok?'LIVE | '+s.usb+' | '+s.fps.toFixed(1)+' fps':(s.error||'Waiting for camera');
document.getElementById('feed').style.visibility=ok?'visible':'hidden';
if(ok&&failed)document.getElementById('feed').src='/stream?t='+Date.now();failed=!ok;
}catch(e){document.getElementById('status').textContent='Connection lost';document.getElementById('feed').style.visibility='hidden';failed=true;}}
setInterval(status,1000);status();
</script></html>'''

condition = threading.Condition()
state = dict(jpeg=None, raw=None, raw_seq=-1, capture_ns=0, epoch=uuid.uuid4().hex,
             seq=0, last=0, fps=0., usb='unknown', error=None)


pairing = None
imu_history = None
video_link = None
streams = {}


def record_stream(name, frame):
    now = time.monotonic_ns()
    seq = frame.getSequenceNum()
    previous = streams.get(name)
    samples = previous['samples']+1 if previous else 1
    first = previous['first_arrival_ns'] if previous else now
    gaps = previous['sequence_gaps'] if previous else 0
    if previous:
        gaps += max(0, seq-previous['seq']-1)
    streams[name] = dict(seq=seq, capture_device_ns=int(frame.getTimestampDevice().total_seconds()*1e9),
        capture_host_ns=int(frame.getTimestamp().total_seconds()*1e9), last_arrival_ns=now,
        first_arrival_ns=first, samples=samples, sequence_gaps=gaps,
        received_hz=(samples-1)*1e9/(now-first) if now>first else 0.)


def health_streams():
    now = time.monotonic_ns()
    return {name: dict(v, age_s=(now-v['capture_host_ns'])/1e9,
                      stale=not 0 <= now-v['capture_host_ns'] < 1_000_000_000)
            for name,v in streams.items()}



def capture(stereo_enabled=False, imu_enabled=False, video=None):
    global pairing, imu_history, video_link
    try:
        if video:
            video_link=VideoLink(video['codec'],video['host'],video['port'])
            state['video_config']=video
        with dai.Device() as dev:
            dev.setIrLaserDotProjectorIntensity(0)
            dev.setIrFloodLightIntensity(0)
            with condition:
                state['usb'] = str(dev.getUsbSpeed()).split('.')[-1]
                state['stereo_enabled'] = stereo_enabled
                state['imu_enabled'] = imu_enabled
                state['imu_config'] = dict(accel_requested_hz=400,gyro_requested_hz=400,batch_threshold=5,max_batch_reports=40) if imu_enabled else None
                state['rgb_undistorted'] = stereo_enabled
            cal = dev.readCalibration().eepromToJson()
            geometry = dict(calibration=cal, rgb=[1280,720], stereo=[640,400], undistort=stereo_enabled)
            calibration_id = hashlib.sha256(json.dumps(geometry, sort_keys=True).encode()).hexdigest()
            if stereo_enabled:
                with condition:
                    pairing = Pairing(Path('/proc/sys/kernel/random/boot_id').read_text().strip(), calibration_id)
                    state['epoch'] = pairing.epoch
            with dai.Pipeline(dev) as pipeline:
                camera = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
                output = camera.requestOutput((1280, 720), type=dai.ImgFrame.Type.NV12, fps=15, enableUndistortion=stereo_enabled)
                raw_output = camera.requestOutput((1280, 720), type=dai.ImgFrame.Type.BGR888i,
                    fps=15, enableUndistortion=stereo_enabled)
                raw_queue = raw_output.createOutputQueue(maxSize=4, blocking=False)
                mono_queues = {}
                depth_queue = None
                if stereo_enabled:
                    stereo = pipeline.create(dai.node.StereoDepth)
                    stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.DEFAULT)
                    stereo.setLeftRightCheck(True)
                    stereo.setSubpixel(True)
                    for socket, target in [(dai.CameraBoardSocket.CAM_B, stereo.left),
                                           (dai.CameraBoardSocket.CAM_C, stereo.right)]:
                        mono = pipeline.create(dai.node.Camera).build(socket)
                        mono_output = mono.requestOutput((640, 400), type=dai.ImgFrame.Type.GRAY8,
                                           fps=15, enableUndistortion=False)
                        mono_output.link(target)
                        name = 'left' if socket == dai.CameraBoardSocket.CAM_B else 'right'
                        mono_queues[name] = mono_output.createOutputQueue(maxSize=4, blocking=False)
                    raw_output.link(stereo.inputAlignTo)
                    depth_queue = stereo.depth.createOutputQueue(maxSize=4, blocking=False)
                imu_queue = None
                if imu_enabled:
                    imu = pipeline.create(dai.node.IMU)
                    imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW], 400)
                    imu.setBatchReportThreshold(5)
                    imu.setMaxBatchReports(40)
                    imu_queue = imu.out.createOutputQueue(maxSize=64, blocking=False)
                    imu_history = IMUHistory(state['epoch'])
                encoder = pipeline.create(dai.node.VideoEncoder)
                encoder.setDefaultProfilePreset(15, dai.VideoEncoderProperties.Profile.MJPEG)
                encoder.setQuality(80)
                output.link(encoder.input)
                queue = encoder.bitstream.createOutputQueue(maxSize=2, blocking=False)
                video_queue=None
                if video:
                    video_output=camera.requestOutput((1920,1080),type=dai.ImgFrame.Type.NV12,fps=15,enableUndistortion=stereo_enabled)
                    ve=pipeline.create(dai.node.VideoEncoder)
                    profile=dai.VideoEncoderProperties.Profile.H265_MAIN if video['codec']=='h265' else dai.VideoEncoderProperties.Profile.H264_MAIN
                    ve.setDefaultProfilePreset(15,profile)
                    ve.setRateControlMode(dai.VideoEncoderProperties.RateControlMode.CBR)
                    ve.setBitrateKbps(video['kbps']);ve.setKeyframeFrequency(15);ve.setNumBFrames(0)
                    video_output.link(ve.input)
                    video_queue=ve.bitstream.createOutputQueue(maxSize=4,blocking=False)
                pipeline.start()
                start, count = time.monotonic(), 0
                while pipeline.isRunning():
                    if video_queue is not None:
                        video_frame=video_queue.tryGet()
                        if video_frame is not None:
                            video_link.submit(video_frame.getData().tobytes())
                            with condition:record_stream('video',video_frame)
                    if imu_queue is not None:
                        for _ in range(8):
                            imu_message = imu_queue.tryGet()
                            if imu_message is None: break
                            with condition:
                                for packet in imu_message.packets:
                                    for kind,report in [('accel',packet.acceleroMeter),('gyro',packet.gyroscope)]:
                                        imu_history.add(kind,report,time.monotonic_ns())
                    for name, mono_queue in mono_queues.items():
                        mono_frame = mono_queue.tryGet()
                        if mono_frame is not None:
                            with condition:
                                record_stream(name, mono_frame)
                    depth_frame = depth_queue.tryGet() if depth_queue else None
                    if depth_frame is not None:
                        if (depth_frame.getWidth(), depth_frame.getHeight()) != (1280, 720):
                            raise RuntimeError('Unexpected aligned depth dimensions')
                        payload = depth_frame.getData().tobytes()
                        if len(payload) != 1280*720*2:
                            raise RuntimeError('Unexpected depth payload size')
                        with condition:
                            record_stream('depth', depth_frame)
                            pairing.add('depth', Frame(depth_frame.getSequenceNum(),
                                int(depth_frame.getTimestampDevice().total_seconds()*1e9),
                                int(depth_frame.getTimestamp().total_seconds()*1e9),
                                payload, time.monotonic_ns()))
                            state['depth_last_ns'] = time.monotonic_ns()
                    raw_frame = raw_queue.tryGet()
                    if raw_frame is not None:
                        with condition:
                            payload = raw_frame.getData().tobytes()
                            if len(payload) != 1280*720*3:
                                raise RuntimeError('Unexpected RGB payload size')
                            record_stream('rgb', raw_frame)
                            if pairing and pairing.geometry is None:
                                g = from_transform(raw_frame.getTransformation(), pairing.epoch, calibration_id)
                                # Include actual delivered K/crop in calibration identity, since
                                # additional outputs can change the sensor's chosen source mode.
                                signature={k:v for k,v in g.items() if k not in ('epoch','calibration_id')}
                                calibration_id=hashlib.sha256(json.dumps([calibration_id,signature],sort_keys=True).encode()).hexdigest()
                                g['calibration_id']=calibration_id;pairing.calibration_id=calibration_id
                                pairing.geometry=g;state['geometry']=g
                            if pairing:
                                pairing.add('rgb', Frame(raw_frame.getSequenceNum(),
                                    int(raw_frame.getTimestampDevice().total_seconds()*1e9),
                                    int(raw_frame.getTimestamp().total_seconds()*1e9),
                                    payload, time.monotonic_ns()))
                            state.update(raw=payload,
                                raw_seq=raw_frame.getSequenceNum(),
                                capture_ns=int(raw_frame.getTimestamp().total_seconds()*1e9))
                            condition.notify_all()
                    with condition:
                        if pairing:
                            pairing.advance()
                        condition.notify_all()
                    frame = queue.tryGet()
                    if frame is None:
                        time.sleep(.001)
                        continue
                    jpeg = frame.getData().tobytes()
                    if not jpeg.startswith(b'\xff\xd8'):
                        raise RuntimeError('Encoder did not return JPEG data')
                    now = time.monotonic()
                    count += 1
                    with condition:
                        state.update(jpeg=jpeg, seq=state['seq']+1, last=now, error=None)
                        if now-start >= 1:
                            state['fps'] = count/(now-start)
                            start, count = now, 0
                        condition.notify_all()
    except Exception as exc:
        with condition:
            state.update(error=str(exc), jpeg=None, raw=None)
            condition.notify_all()
        print('OAK preview error:', repr(exc), flush=True)
    finally:
        if video_link: video_link.close()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/imu':
            if self.client_address[0] not in ('127.0.0.1','::1'):
                self.send_error(403); return
            try:
                q=parse_qs(urlsplit(self.path).query)
                after=int(q.get('after',['-1'])[0]);epoch=q.get('epoch',[None])[0]
                if after < -1: raise ValueError('invalid cursor')
            except ValueError:
                self.send_error(400); return
            with condition:
                data=imu_history.snapshot(after,epoch) if imu_history else None
            if data is None:
                self.send_error(503,'IMU disabled'); return
            body=json.dumps(data).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            return
        if path == '/timing':
            if self.client_address[0] not in ('127.0.0.1','::1'):
                self.send_error(403);return
            try: after=int(parse_qs(urlsplit(self.path).query).get('after',['-1'])[0])
            except ValueError: self.send_error(400);return
            trace=getattr(self.server,'trace',None)
            body=json.dumps(trace.snapshot(after) if trace else {'records':[], 'next_id':0,'overrun':False}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        if path == '/box-depth':
            # One colour-image region of one captured frame; unvalidated preview, local only.
            if self.client_address[0] not in ('127.0.0.1','::1'):
                self.send_error(403);return
            try:
                q=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                if set(q)!={'epoch','rgb_host_ns','x0','y0','x1','y1'} or any(len(v)!=1 for v in q.values()):raise ValueError('Invalid query')
                epoch=q['epoch'][0];stamp=int(q['rgb_host_ns'][0]);roi=tuple(int(q[k][0]) for k in ('x0','y0','x1','y1'))
                if not epoch or stamp<0:raise ValueError('Invalid query')
            except ValueError:
                self.send_error(400);return
            compute=getattr(self.server,'box_depth',None)
            code,meta,payload=compute(epoch,stamp,roi) if compute else (503,dict(reason='not_ready'),b'')
            blob=json.dumps(meta).encode();body=struct.pack('>I',len(blob))+blob+payload
            self.send_response(code);self.send_header('Content-Type','application/vnd.garuda.oak-box-depth')
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
            return
        if path in ('/pair','/pair-preview','/depth-preview'):
            if self.client_address[0] not in ('127.0.0.1', '::1'):
                self.send_error(403)
                return
            try:
                q = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                depth_only=path=='/depth-preview'
                if set(q)-({'after','epoch','enc'} if depth_only else {'after','epoch','depth'}) or any(len(v)!=1 for v in q.values()):
                    raise ValueError('Invalid query')
                encoding=q.get('enc',['raw'])[0]
                if encoding not in DEPTH_ENCODINGS:
                    raise ValueError('Invalid query')
                after = int(q['after'][0]) if 'after' in q else None
                epoch = q.get('epoch', [None])[0]
                want = q.get('depth', ['1'])[0]
                if (after is not None and after < 0) or want not in ('0','1'):
                    raise ValueError('Invalid query')
            except ValueError:
                self.send_error(400)
                return
            if path!='/pair':self.server.map_demand_ns=time.monotonic_ns()   # someone is looking at the full map
            with condition:
                selected_pairing=getattr(self.server,'rgb_pairing',None) if path=='/pair' else pairing
                selected_pairing=selected_pairing or pairing
                depth_failed=bool(state.get('depth_error')) and path!='/pair'
                deadline = time.monotonic()+(selected_pairing.limits['pair_wait_ms']/1000 if selected_pairing else 0)
                while True:
                    code, body = selected_pairing.response(after, epoch, want=='1',preview=path!='/pair',omit_rgb=depth_only) if selected_pairing and not state['error'] and not depth_failed else (503, None)
                    if code != 204 or time.monotonic() >= deadline:
                        break
                    condition.wait(min(.05, max(0,deadline-time.monotonic())))
            if depth_only and body:
                # Encoded after the owner lock is released so capture is never held up.
                body=pack_depth_preview(body[0],body[1],encoding)
            self.send_response(code)
            self.send_header('Content-Type', 'application/vnd.garuda.oak-depth-preview' if depth_only else 'application/vnd.garuda.oak-pair')
            self.send_header('Content-Length', str(len(body) if body else 0))
            self.end_headers()
            if body:
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            return
        if path == '/frame':
            # Raw pixels are local IPC only; browser users get the MJPEG preview.
            if self.client_address[0] not in ('127.0.0.1', '::1'):
                self.send_error(403)
                return
            try:
                after = int(parse_qs(urlsplit(self.path).query).get('after', ['-1'])[0])
            except ValueError:
                self.send_error(400)
                return
            with condition:
                condition.wait_for(lambda: state['raw_seq'] != after or state['error'], timeout=2)
                data = dict(state)
            if data['error'] or data['raw'] is None or time.monotonic_ns()-data['capture_ns']>1_000_000_000:
                self.send_error(503, 'Camera frame unavailable or stale')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(data['raw'])))
            for key, value in {'Sequence':data['raw_seq'], 'Capture-Ns':data['capture_ns'],
                               'Epoch':data['epoch'], 'Width':1280, 'Height':720,
                               'Format':'BGR888'}.items():
                self.send_header('X-Oak-'+key, str(value))
            self.end_headers()
            try:
                self.wfile.write(data['raw'])
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            seq = -1
            self.connection.settimeout(5)
            try:
                while True:
                    with condition:
                        condition.wait_for(lambda: state['seq'] != seq or state['error'], timeout=3)
                        if state['error'] or not state['jpeg'] or time.monotonic()-state['last']>3:
                            return
                        if seq == state['seq']:
                            continue
                        seq, jpeg = state['seq'], state['jpeg']
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+
                                     str(len(jpeg)).encode()+b'\r\n\r\n'+jpeg+b'\r\n')
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            return
        if path == '/diagnostics' and self.client_address[0] not in ('127.0.0.1', '::1'):
            self.send_error(403)
            return
        if path in ('/health', '/diagnostics'):
            with condition:
                data = {k:v for k,v in state.items() if k not in ('jpeg','raw','last')}
                if video_link: data['video'] = dict(video_link.stats)
                if pairing:
                    status = health_streams()
                    data['enabled_streams'] = ['rgb', 'depth', 'left', 'right', 'mjpeg'] + (['accel','gyro'] if imu_history else []) + (['video'] if video_link else [])
                    data['imu_enabled'] = bool(imu_history)
                    data['imu'] = imu_history.health(time.monotonic_ns()) if imu_history else {}
                    data['streams'] = status if path == '/diagnostics' else {k:{f:v[f] for f in ('samples','received_hz','sequence_gaps','age_s','stale')} for k,v in status.items()}
                    data['healthy'] = all(name in status and not status[name]['stale']
                                          for name in ('rgb','depth','left','right')) and not state['error']
                    if imu_history:
                        data['healthy'] = data['healthy'] and all(k in data['imu'] and not data['imu'][k]['stale'] for k in ('accel','gyro'))
                    if video_link:
                        data['healthy'] = data['healthy'] and 'video' in status and not status['video']['stale'] and video_link.stats['error'] is None and time.monotonic()-video_link.stats.get('last_sent_monotonic',0)<2
                    if path == '/diagnostics':
                        data['latest_pair'] = dict(pair_id=pairing.latest[0],
                            rgb_device_ns=pairing.latest[1].device_ns,
                            depth_device_ns=pairing.latest[2].device_ns if pairing.latest[2] else None) if pairing.latest else None
                    data['limits'] = pairing.limits
                    data['pairing'] = dict(pairing.stats)
                    data['depth_age_s'] = (time.monotonic_ns()-state['depth_last_ns'])/1e9 if 'depth_last_ns' in state else None
                    data['depth_reference_frame'] = 'unverified'
                    data['calibration_id'] = pairing.calibration_id
                if state.get('camera_compute') == 'capture-and-encoding-only':
                    data['transport_healthy'] = bool(data.get('healthy')) and not state.get('depth_error')
                    data['healthy'] = data['transport_healthy']
                    data['measurement_ready'] = False
                data['age_s'] = time.monotonic()-state['last'] if state['last'] else None
            body, kind = json.dumps(data).encode(), 'application/json'
        elif path == '/':
            body, kind = PAGE, 'text/html; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stereo', action='store_true', help='Opt in to stereo and undistorted RGB; changes geometry')
    parser.add_argument('--host-stereo', action='store_true', help='CPU rectification, stereo and alignment; no camera depth')
    parser.add_argument('--stereo-hef', help='Experimental live Hailo stereo HEF; requires --host-stereo and exclusive NPU access')
    parser.add_argument('--record-pairs', help='New directory for five raw stereo/RGB pairs and calibration')
    parser.add_argument('--imu', action='store_true', help='Stream raw accel/gyro at requested 400 Hz')
    parser.add_argument('--port', type=int, default=8096)
    parser.add_argument('--map-on-demand', action='store_true', help='Host stereo only: compute full depth maps only while /pair-preview or /depth-preview is being read')
    parser.add_argument('--map-interval-ms', type=int, default=0, help='Host stereo only: least time between full depth maps; 0 = every pair')
    parser.add_argument('--video-codec',choices=['h264','h265'])
    parser.add_argument('--video-host',default='127.0.0.1')
    parser.add_argument('--video-port',type=int,default=5600)
    parser.add_argument('--video-kbps',type=int,default=3000)
    args = parser.parse_args()
    if not 2000<=args.video_kbps<=4000: parser.error('video kbps must be 2000..4000')
    video=dict(codec=args.video_codec,host=args.video_host,port=args.video_port,kbps=args.video_kbps) if args.video_codec else None
    import signal
    def stop_signal(_sig,_frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_signal)
    server = ThreadingHTTPServer(('0.0.0.0', args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if args.stereo_hef and not args.host_stereo: parser.error('--stereo-hef requires --host-stereo')
    if not 0<=args.map_interval_ms<=900 or (args.map_interval_ms and not args.host_stereo): parser.error('--map-interval-ms is 0..900 and needs --host-stereo')
    if args.host_stereo:
        if args.imu: parser.error('host stereo currently uses camera/encoding only; IMU not enabled')
        import sys
        from host_capture import capture as host_capture
        host_capture(sys.modules[__name__],video=video,record_dir=args.record_pairs,stereo_hef=args.stereo_hef,map_interval_ms=args.map_interval_ms,map_on_demand=args.map_on_demand)
    else:
        capture(stereo_enabled=args.stereo, imu_enabled=args.imu, video=video)
    raise SystemExit(1)  # Let systemd recover device disconnects; never serve a frozen feed.
