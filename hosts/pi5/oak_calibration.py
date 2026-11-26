#!/usr/bin/env python3
"""Bench calibration workspace. Reads the single OAK owner; never opens/writes the device.

Uses existing OpenCV/NumPy. Sessions are immutable in setup, captures append-only.
Candidate RGB intrinsics and held-out reprojection are NOT stereo distance calibration.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import threading
from collections import Counter
from statistics import median as sample_median
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import cv2
import numpy as np

REVISION = 'calibration-workspace-v3-measured-tape'
HANDOUTS = {
    '/chennai/oak-targets-a4.pdf': 'application/pdf',
    '/chennai/OPERATOR.md': 'text/plain; charset=utf-8',
    '/chennai/CLAUDE_HANDOFF.md': 'text/plain; charset=utf-8',
}
MAX_CAPTURES = 60
MAX_BYTES = 1024**3
PATTERNS = ('checkerboard', 'circles', 'asymmetric_circles')


def number(value, lo, hi, name):
    if isinstance(value, bool): raise ValueError(name+' must be numeric')
    try: v = float(value)
    except (ValueError, TypeError): raise ValueError(name+' must be numeric')
    if not math.isfinite(v) or not lo <= v <= hi: raise ValueError(name+' outside allowed limits')
    return v


def setup(data):
    out = {}
    for key in ('name', 'operator', 'camera', 'board_id', 'tape_reference'):
        value = data.get(key, '')
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError(key+' is required (maximum 200 characters)')
        out[key] = value.strip()
    mount = data.get('mount_context', 'unspecified')
    if mount not in ('unspecified', 'bench', 'grounded_airframe'):
        raise ValueError('choose a mount context')
    mount_id = data.get('mount_id', '')
    if not isinstance(mount_id, str) or len(mount_id) > 200:
        raise ValueError('invalid mount label')
    if mount == 'grounded_airframe' and not mount_id.strip():
        raise ValueError('enter the airframe or mount revision for a mounted session')
    out.update(mount_context=mount, mount_id=mount_id.strip())
    if data.get('pattern') not in PATTERNS: raise ValueError('unsupported pattern')
    if data.get('board_condition') not in ('rigid_flat', 'bent', 'unknown'):
        raise ValueError('record board condition')
    goal=data.get('goal','depth')
    if goal not in ('depth','lens','practice'): raise ValueError('choose a calibration task')
    out.update(pattern=data['pattern'], board_condition=data['board_condition'], goal=goal, require_depth=True, require_measured_tape=(goal=='depth'))
    for key in ('columns', 'rows'):
        n = number(data.get(key), 2, 20, key)
        if int(n) != n: raise ValueError(key+' must be an integer')
        out[key] = int(n)
    if out['columns'] * out['rows'] > 300: raise ValueError('maximum 300 pattern points')
    out['pitch_mm'] = number(data.get('pitch_mm'), .1, 500, 'pitch_mm')
    return out


def object_points(config):
    cols, rows = config['columns'], config['rows']
    points = np.zeros((cols*rows, 3), np.float32)
    if config['pattern'] == 'asymmetric_circles':
        points[:, :2] = [(2*c + r % 2, r) for r in range(rows) for c in range(cols)]
    else:
        points[:, :2] = np.mgrid[:cols, :rows].T.reshape(-1, 2)
    return points * (config['pitch_mm']/1000)


def detect(image, config):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    size = (config['columns'], config['rows'])
    if config['pattern'] == 'checkerboard':
        found, points = cv2.findChessboardCornersSB(gray, size, cv2.CALIB_CB_NORMALIZE_IMAGE)
    else:
        flags = cv2.CALIB_CB_SYMMETRIC_GRID if config['pattern'] == 'circles' else cv2.CALIB_CB_ASYMMETRIC_GRID
        found, points = cv2.findCirclesGrid(gray, size, flags=flags)
    return points.reshape(-1, 2) if found else None


def read_camera():
    """Prefer an exact pair; raw RGB fallback only when owner says stereo is disabled."""
    with urlopen('http://127.0.0.1:8096/health', timeout=2) as r: health = json.load(r)
    if health.get('error'): raise ValueError('camera reports an error')
    if health.get('stereo_enabled'):
        from oak_pair import read_pair
        with urlopen('http://127.0.0.1:8096/pair-preview?depth=1', timeout=4) as r:
            pair = read_pair(r, int(r.headers['Content-Length']), allow_unqualified_preview=True)
        meta = pair.meta
        capture = meta['rgb']['capture_host_ns']
        image = np.frombuffer(pair.rgb, np.uint8).reshape(720, 1280, 3).copy()
        depth = pair.depth_mm
        provenance = dict(mode='undistorted_rgb', epoch=meta['epoch'], sequence=meta['rgb']['seq'],
                          capture_host_ns=capture, calibration_id=meta['calibration_id'],
                          geometry=meta.get('geometry'), pair_metadata=meta)
    else:
        with urlopen('http://127.0.0.1:8096/frame?after=-1', timeout=3) as r:
            h = r.headers
            if (int(h['X-Oak-Width']), int(h['X-Oak-Height']), h['X-Oak-Format']) != (1280, 720, 'BGR888'):
                raise ValueError('unexpected frame format')
            if int(h['Content-Length']) != 1280*720*3: raise ValueError('unexpected frame length')
            data = r.read(1280*720*3+1)
            if len(data) != 1280*720*3: raise ValueError('truncated image')
            capture = int(h['X-Oak-Capture-Ns'])
            provenance = dict(mode='raw_rgb', epoch=h['X-Oak-Epoch'], sequence=int(h['X-Oak-Sequence']),
                              capture_host_ns=capture, calibration_id=None, geometry=None)
        image = np.frombuffer(data, np.uint8).reshape(720, 1280, 3).copy()
        depth = None
    if provenance['epoch'] != health.get('epoch'):
        raise ValueError('camera restarted during capture; retry with the current mode')
    if not 0 <= time.monotonic_ns()-capture < 1_000_000_000:
        raise ValueError('camera frame is stale or clock does not match')
    return image, depth, provenance


def depth_preview(depth, step=1):
    from oak_depth_view import colourise
    return colourise(depth, step=step)


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    os.replace(temp, path)


def board_tape_report(session):
    """One row per saved capture, preserving split, misses and unknown historic references."""
    buf = io.StringIO(newline='')
    writer = csv.writer(buf)
    writer.writerow(('capture_id', 'split', 'sequence', 'tape_m', 'tape_measured',
                     'tape_uncertainty_mm', 'board_median_m', 'board_valid_fraction',
                     'board_minus_tape_m', 'comparison_status'))
    checks = {'tune': [], 'validate': []}
    counts = {'tune': Counter(), 'validate': Counter()}
    for c in session['captures']:
        board = c.get('board_depth') or {}
        median, tape = board.get('median_m'), c.get('distance_m')
        status = 'raw_reference_difference'
        if session['config'].get('goal') != 'depth':
            status = 'other_task'
        elif session['config'].get('board_condition') != 'rigid_flat':
            status = 'flatness_unconfirmed'
        elif c.get('tape_measured') is not True or tape is None:
            status = 'tape_unconfirmed'
        elif not c.get('pattern_found'):
            status = 'pattern_missing'
        elif not c.get('has_depth') or median is None:
            status = 'board_depth_missing'
        difference = round(median-tape, 6) if status == 'raw_reference_difference' else None
        counts[c['split']][status] += 1
        if difference is not None:
            checks[c['split']].append(difference)
        writer.writerow((c['id'], c['split'], c.get('sequence'), tape, c.get('tape_measured'),
                         c.get('tape_uncertainty_mm'), median, board.get('valid_fraction'),
                         difference, status))
    report = {'session_id': session['id'], 'tape_reference': session['config'].get('tape_reference'),
              'mount_context': session['config'].get('mount_context', 'unspecified'),
              'mount_id': session['config'].get('mount_id', ''), 'quantity': 'Board median minus marked tape reference, in metres.',
              'scope': 'Raw descriptive comparisons. Optical origin and depth axis remain unverified.',
              'sets': {}}
    for split, values in checks.items():
        report['sets'][split] = dict(saved=sum(counts[split].values()), compared=len(values),
                                     status_counts=counts[split],
                                     median_signed_m=sample_median(values) if values else None,
                                     median_absolute_m=sample_median(map(abs, values)) if values else None,
                                     max_absolute_m=max(map(abs, values)) if values else None)
    return buf.getvalue(), report


class Workspace:
    def __init__(self, root, camera=read_camera):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.camera = camera
        self.lock = threading.Lock()
        self.live_cache = None
        self.live_at = 0.0

    def directory(self, sid):
        if not isinstance(sid, str) or not re.fullmatch('[a-f0-9]{32}', sid): raise ValueError('invalid session id')
        path = self.root/sid
        if not (path/'session.json').is_file(): raise ValueError('session not found')
        return path

    def load(self, sid):
        return json.loads((self.directory(sid)/'session.json').read_text())

    def list(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('*/session.json'), reverse=True)]

    def create(self, data):
        config = setup(data)
        if len(list(self.root.glob('*/session.json'))) >= 20: raise ValueError('20-session limit: archive sessions on the Pi first')
        sid = uuid.uuid4().hex; path = self.root/sid; path.mkdir()
        value = dict(id=sid, created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                     config=config, tool_revision=REVISION, tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), opencv=cv2.__version__, captures=[],
                     geometry_verified=False, applied=False)
        atomic_json(path/'session.json', value)
        return value

    def capture(self, sid, data):
        session = self.load(sid); path = self.directory(sid)
        if len(session['captures']) >= MAX_CAPTURES: raise ValueError('60-capture session limit reached')
        if sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file()) > MAX_BYTES-16*1024**2:
            raise ValueError('workspace storage limit: export and archive sessions on the Pi')
        split = data.get('split')
        if split not in ('tune', 'validate'): raise ValueError('choose tuning or validation')
        distance = data.get('distance_m')
        if distance is not None: distance = number(distance, .05, 100, 'distance_m')
        note = data.get('note', '')
        if not isinstance(note, str) or len(note)>500: raise ValueError('notes limited to 500 characters')
        if session['config'].get('goal') == 'depth' and distance is None:
            raise ValueError('measure and enter the tape distance before a depth-check capture')
        measured = data.get('tape_measured')
        if measured is not None and type(measured) is not bool:
            raise ValueError('tape confirmation must be true or false')
        if session['config'].get('require_measured_tape') and measured is not True:
            raise ValueError('confirm you measured this placement with the tape')
        if measured and distance is None:
            raise ValueError('a confirmed tape reading needs a distance')
        uncertainty = data.get('tape_uncertainty_mm')
        if uncertainty is not None:
            if measured is not True:
                raise ValueError('uncertainty requires a measured tape reading')
            uncertainty = number(uncertainty, .01, 1000, 'tape_uncertainty_mm')
        image, depth, meta = self.camera()
        if session['config'].get('require_depth') and depth is None:
            raise ValueError('no matching depth arrived; check stereo status and retry')
        binding = {k:meta.get(k) for k in ('mode','epoch','calibration_id','geometry')}
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        if session['captures']:
            if binding != session['binding']: raise ValueError('camera mode, epoch or geometry changed: create a new session')
            if any(c['image_sha256']==digest or c['sequence']==meta['sequence'] for c in session['captures']):
                raise ValueError('duplicate frame: move the board and capture a new view')
        points = detect(image, session['config'])
        cid = uuid.uuid4().hex
        record = dict(id=cid, split=split, distance_m=distance, note=note,
                      tape_measured=measured, tape_uncertainty_mm=uncertainty,
                      capture_revision=REVISION, captured_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                      sequence=meta['sequence'], provenance=meta, image_sha256=digest,
                      geometry_eligible=session['config']['board_condition']=='rigid_flat',
                      pattern_found=points is not None, points_px=points.tolist() if points is not None else None,
                      image_size=[image.shape[1],image.shape[0]], has_depth=depth is not None,
                      image_file=cid+'.png', depth_file=cid+'-depth.png' if depth is not None else None)
        if depth is not None and points is not None:
            mask = np.zeros(depth.shape, np.uint8)
            cv2.fillConvexPoly(mask, cv2.convexHull(points.astype(np.int32)), 255)
            mask = cv2.erode(mask, np.ones((9,9),np.uint8))>0
            valid = mask & (depth>0)
            record['board_depth'] = dict(valid_fraction=float(valid.sum()/max(1,mask.sum())),
                median_m=float(np.median(depth[valid])/1000) if valid.any() else None,
                note='Median over board interior; axis/origin and distance accuracy unverified. Not a person estimate.')
        if not cv2.imwrite(str(path/record['image_file']), image): raise ValueError('image write failed')
        if depth is not None and not cv2.imwrite(str(path/record['depth_file']), depth): raise ValueError('depth write failed')
        preview = image.copy()
        if points is not None: cv2.drawChessboardCorners(preview, (session['config']['columns'],session['config']['rows']),points.reshape(-1,1,2),True)
        cv2.imwrite(str(path/(cid+'-preview.jpg')),preview)
        if depth is not None: cv2.imwrite(str(path/(cid+'-depth-preview.jpg')), depth_preview(depth))
        session['binding']=binding; session['captures'].append(record)
        atomic_json(path/'session.json',session)
        return session

    def solve(self, sid):
        s=self.load(sid)
        if s['config']['board_condition'] != 'rigid_flat': raise ValueError('candidate solve requires a confirmed rigid, flat board; create a new session')
        tune=[c for c in s['captures'] if c['split']=='tune' and c['pattern_found']]
        held=[c for c in s['captures'] if c['split']=='validate' and c['pattern_found']]
        if len(tune)<12 or len(held)<3: raise ValueError('need at least 12 detected tuning views and 3 separate detected validation views')
        size=tuple(tune[0]['image_size']);obj=object_points(s['config'])
        def points(c): return np.array(c['points_px'],np.float32).reshape(-1,1,2)
        # Reject near-identical captures even when sensor noise changes the image hash.
        def near(a,b): return np.sqrt(np.mean(np.sum((points(a)-points(b))**2,axis=2))) < 8
        for i,c in enumerate(tune):
            if any(near(c,p) for p in tune[:i]): raise ValueError('tuning views are too similar: vary board position, distance and tilt')
        if any(near(c,t) for c in held for t in tune): raise ValueError('validation view duplicates a tuning pose; capture separate held-out views')
        rms,k,dist,rvecs,tvecs=cv2.calibrateCamera([obj]*len(tune),[points(c) for c in tune],size,None,None,
                     criteria=(cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER,50,1e-8))
        if not np.isfinite(k).all() or not np.isfinite(dist).all() or not math.isfinite(rms) or k[0,0]<=0 or k[1,1]<=0:
            raise ValueError('solver produced invalid intrinsics')
        checks=[]
        for c in held:
            ok,r,t=cv2.solvePnP(obj,points(c),k,dist)
            if not ok: raise ValueError('held-out pose solve failed')
            projected,_=cv2.projectPoints(obj,r,t,k,dist)
            error=float(np.sqrt(np.mean(np.sum((projected-points(c))**2,axis=2))))
            if not math.isfinite(error): raise ValueError('invalid held-out reprojection')
            checks.append(dict(capture_id=c['id'],rms_px=error))
        result=dict(candidate_only=True,applied=False,geometry_verified=False,
            image_mode=s['binding']['mode'],image_size=list(size),camera_matrix=k.tolist(),
            distortion=dist.tolist(),tuning_rms_px=float(rms),validation=checks,
            tune_ids=[c['id'] for c in tune],validation_ids=[c['id'] for c in held],
            note='RGB delivered-image model only. Held-out reprojection fits each view pose; it does not validate metric depth, stereo extrinsics or IMU. Never written to device.')
        atomic_json(self.directory(sid)/'candidate.json',result)
        return result

    def live(self):
        if self.live_cache is not None and time.monotonic()-self.live_at < 1:
            return self.live_cache
        image, depth, meta = self.camera()
        def jpeg(img):
            img=cv2.resize(img,(640,360),interpolation=cv2.INTER_NEAREST)
            ok,b=cv2.imencode('.jpg',img,[cv2.IMWRITE_JPEG_QUALITY,75])
            if not ok:raise ValueError('preview encode failed')
            return 'data:image/jpeg;base64,'+base64.b64encode(b).decode()
        result=dict(rgb=jpeg(image),depth=jpeg(depth_preview(depth, step=2)) if depth is not None else None,
                    depth_present=depth is not None,
                    depth_backend=meta.get('pair_metadata',{}).get('depth_backend','unknown'),
                    measurement_qualified=meta.get('pair_metadata',{}).get('measurement_qualified',False),
                    valid_fraction=float(np.count_nonzero(depth)/depth.size) if depth is not None else None,
                    epoch=meta['epoch'],sequence=meta['sequence'],mode=meta['mode'],
                    calibration_id=meta.get('calibration_id'), geometry=meta.get('geometry'),
                    capture_host_ns=meta['capture_host_ns'])
        self.live_cache=result;self.live_at=time.monotonic()
        return result

    def export(self, sid):
        path=self.directory(sid); output=io.BytesIO()
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as z:
            csv_data, report = board_tape_report(self.load(sid))
            z.writestr('board-tape.csv', csv_data)
            z.writestr('board-tape-summary.json', json.dumps(report, indent=2, allow_nan=False))
            for f in sorted(path.iterdir()):
                if f.suffix in ('.json','.png','.jpg'):z.write(f,f.name)
        return output.getvalue()


def handler(workspace):
    assets=Path(__file__).with_name('demo')
    class H(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,code,kind,data):
            self.send_response(code);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        def json(self,code,value):self.send(code,'application/json',json.dumps(value,allow_nan=False).encode())
        def do_GET(self):
            p=urlsplit(self.path).path
            try:
                if p in HANDOUTS:
                    self.send(200,HANDOUTS[p],(assets/p[1:]).read_bytes());return
                if p in ('/','/camera','/centre.js','/calibration.js','/calibration.css','/calibration-guide.js'):
                    name={'/':'calibration-centre.html','/camera':'calibration.html'}.get(p,p[1:]);mime={'.html':'text/html','.js':'text/javascript','.css':'text/css'}[Path(name).suffix]
                    self.send(200,mime,(assets/name).read_bytes());return
                if p=='/api/sessions':self.json(200,workspace.list());return
                if p=='/api/live':
                    if not workspace.lock.acquire(False):self.json(409,{'error':'capture in progress'});return
                    try:self.json(200,workspace.live())
                    finally:workspace.lock.release()
                    return
                m=re.fullmatch(r'/api/session/([a-f0-9]{32})/(export|[a-f0-9]{32}-(?:depth-)?preview.jpg)',p)
                if m:
                    sid,kind=m.groups()
                    locked = kind == 'export'
                    if locked and not workspace.lock.acquire(False):self.json(409,{'error':'another calibration operation is running'});return
                    try:
                        if kind=='export':self.send(200,'application/zip',workspace.export(sid))
                        else:
                            path=workspace.directory(sid)/kind
                            if not path.exists() and kind.endswith('-depth-preview.jpg'):
                                depth=cv2.imread(str(path.with_name(kind.replace('-depth-preview.jpg','-depth.png'))),cv2.IMREAD_UNCHANGED)
                                if depth is None:raise ValueError('saved depth not available')
                                ok,jpg=cv2.imencode('.jpg',depth_preview(depth))
                                if not ok:raise ValueError('depth preview encode failed')
                                self.send(200,'image/jpeg',jpg.tobytes())
                            else:self.send(200,'image/jpeg',path.read_bytes())
                    finally:
                        if locked:workspace.lock.release()
                    return
                self.json(404,{'error':'not found'})
            except (ValueError,OSError) as e:self.json(400,{'error':str(e)})
        def do_POST(self):
            # No permissive CORS on this writer. Require same-origin browser requests.
            if self.headers.get('Origin') != 'http://'+self.headers.get('Host',''):
                self.json(403,{'error':'same-origin browser request required'});return
            if self.headers.get('Content-Type')!='application/json':self.json(400,{'error':'JSON required'});return
            if not workspace.lock.acquire(False):self.json(409,{'error':'another calibration operation is running'});return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=8192:raise ValueError('invalid request size')
                data=json.loads(self.rfile.read(n))
                if not isinstance(data,dict):raise ValueError('JSON object required')
                p=urlsplit(self.path).path
                if p=='/api/sessions':result=workspace.create(data)
                else:
                    m=re.fullmatch(r'/api/session/([a-f0-9]{32})/(capture|solve)',p)
                    if not m:raise ValueError('unknown operation')
                    sid,op=m.groups();result=workspace.capture(sid,data) if op=='capture' else workspace.solve(sid)
                self.json(200,result)
            except (ValueError,OSError,cv2.error,KeyError) as e:self.json(400,{'error':str(e)})
            finally:workspace.lock.release()
    return H

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8099);p.add_argument('--data',default=str(Path.home()/'.local/share/garuda/calibration'));a=p.parse_args()
    cv2.setNumThreads(1)
    ThreadingHTTPServer(('0.0.0.0',a.port),handler(Workspace(a.data))).serve_forever()
