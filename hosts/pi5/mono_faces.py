"""Optional, rate-limited CPU face descriptors using OpenCV YuNet + SFace.

Models: YuNet MIT; SFace Apache-2.0. No face images or enrolled identities are saved.
Camera/person inference remains on Hailo. A bounded worker never queues a backlog.
"""
from pathlib import Path
import queue
import threading
import time
import cv2
import numpy as np


def associate_face(face, tracks):
    x, y, w, h = map(float, face[:4])
    cx, cy = x + w / 2, y + h / 2
    candidates = [t for t in tracks if t[1] == "person" and not t[7] and t[6] >= .5
                  and t[2] <= cx <= t[2]+t[4] and t[3] <= cy <= t[3]+t[5]*.65]
    # Overlapping person boxes are ambiguous; don't enroll a bystander's face.
    return candidates[0][0] if len(candidates) == 1 else None


class FaceWorker:
    def __init__(self, model_dir):
        self.model_dir = Path(model_dir)
        self.enabled = False
        self.ready = False
        self.error = None
        self.generation = 0
        self.last_submit = 0
        self.result = ({}, 0, None)
        self.jobs = queue.Queue(maxsize=1)
        self.detector = self.recognizer = None
        threading.Thread(target=self._run, daemon=True).start()

    def configure(self, enabled):
        self.enabled = bool(enabled)
        self.generation += 1
        self.result = ({}, 0, None)
        self.error = None
        return self.status()

    def status(self):
        available = all((self.model_dir / name).is_file() for name in ('yunet.onnx', 'sface.onnx'))
        return dict(enabled=self.enabled, available=available, ready=self.ready,
                    error=self.error, processor="Pi CPU / OpenCV YuNet + SFace")

    def submit(self, image, tracks, seq, epoch, captured):
        now = time.monotonic()
        if not self.enabled or now-self.last_submit < .75 or self.jobs.full():
            return
        self.last_submit = now
        self.jobs.put_nowait((image, tracks, seq, epoch, captured, self.generation))

    def snapshot(self, epoch):
        data, captured, result_epoch = self.result
        age = (time.monotonic()-captured)*1000
        if not self.enabled or result_epoch != epoch or age > 2000:
            return {}
        return {tid: dict(face, age_ms=int(age)) for tid, face in data.items()}

    def _load(self):
        if self.detector is None or self.recognizer is None:
            self.detector = cv2.FaceDetectorYN_create(str(self.model_dir/'yunet.onnx'), '', (640, 360), .85, .3, 100)
            self.recognizer = cv2.FaceRecognizerSF_create(str(self.model_dir/'sface.onnx'), '')
        self.ready = True

    def _infer(self, image, tracks, sample):
        h, w = image.shape[:2]
        scale = min(1, 640/w)
        small = cv2.resize(image, (int(w*scale), int(h*scale)))
        self.detector.setInputSize((small.shape[1], small.shape[0]))
        _, faces = self.detector.detect(small)
        out = {}
        if faces is None:
            return out
        for f in sorted(faces, key=lambda f: -f[2]*f[3])[:4]:
            f = f.copy(); f[:14] /= scale
            x,y,fw,fh = f[:4]
            if min(fw,fh)<48 or x<0 or y<0 or x+fw>w or y+fh>h:
                continue
            tid = associate_face(f, tracks)
            if tid is None or tid in out:
                continue
            points=f[4:14].reshape(5,2)
            eyes=np.linalg.norm(points[0]-points[1])
            if eyes<12 or abs(points[0,1]-points[1,1])>eyes*.4:
                continue
            if abs(points[2,0]-(points[0,0]+points[1,0])/2)>eyes*.45:
                continue
            aligned = self.recognizer.alignCrop(image, f)
            if cv2.Laplacian(cv2.cvtColor(aligned,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var()<35:
                continue
            v=self.recognizer.feature(aligned).ravel().astype(float)
            norm=np.linalg.norm(v)
            if len(v)!=128 or not np.isfinite(v).all() or norm<1e-9:
                continue
            out[tid]=dict(embedding=(v/norm).round(6).tolist(), sample=sample,
                          bbox=[float(x),float(y),float(fw),float(fh)])
        return out

    def _run(self):
        while True:
            image,tracks,seq,epoch,captured,generation=self.jobs.get()
            try:
                if not self.enabled or generation!=self.generation:
                    continue
                self._load()
                result=self._infer(image,tracks,f'{epoch}:{seq}:{generation}')
                if self.enabled and generation==self.generation:
                    self.result=(result,captured,epoch)
                    self.error=None
            except Exception as e:
                self.error=f'{type(e).__name__}: {e}'
                self.ready=False
                self.result=({},0,None)
            finally:
                self.jobs.task_done()
