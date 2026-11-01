"""PC-only, fixed-frame VisDrone SOT evaluation. No vehicle or board access.

Annotations are evaluator-only: detector/tracker never see the ground truth.
Identity changes count the tracked ID matched to the annotated SOT object at
IoU >= 0.5, including gaps; this is not multi-object IDF1/HOTA.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hosts/pc'))
from vision import Detector, Replay, catalog
from tracker import Tracker, iou
import cv2
import numpy as np

def main():
    p=argparse.ArgumentParser();p.add_argument('--clip',required=True);p.add_argument('--frames',type=int,default=120)
    p.add_argument('--data-root',default='/media/bhanu1234/Development/hailo-export')
    p.add_argument('--video',type=Path)
    p.add_argument('--model',type=Path,default=ROOT/'.runtime/models/yolox/object_detection_yolox_2022nov.onnx')
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if not 1<=a.frames<=10000:raise SystemExit('frames must be 1–10000')
    entry=dict(path=str(a.video.resolve()),kind='video') if a.video else catalog(a.data_root)[a.clip]
    source=Replay(entry);detector=Detector(a.model);tracker=Tracker(max_age=3)
    annotation=Path(entry['path']).parents[1]/'annotations'/f'{a.clip}.txt'
    truth=[list(map(float,line.strip().split(',')[:4])) for line in annotation.read_text().splitlines()] if not a.video and annotation.is_file() else []
    a.out.parent.mkdir(parents=True,exist_ok=True)
    rows=[];durations=[];inference=[];matched=0;switches=0;loss_events=0;previous=None;was_seen=None
    started=time.monotonic();digest=hashlib.sha256()
    with a.out.with_suffix('.jsonl').open('x') as raw:
        for index in range(min(a.frames,source.total)):
            begin=time.monotonic();rgb=source.read()
            if rgb is None:break
            digest.update(rgb.tobytes());before=time.monotonic();dets=detector.infer(rgb);after=time.monotonic()
            tracks=tracker.update(dets);gt=truth[index] if index<len(truth) else None
            best=max(tracks,key=lambda t:iou(t.bbox,gt),default=None) if gt else None
            overlap=iou(best.bbox,gt) if best else 0.;seen=best is not None and overlap>=.5
            if gt:
                if seen:
                    matched+=1
                    if previous is not None and previous!=best.id:switches+=1
                    previous=best.id
                if was_seen is True and not seen:loss_events+=1
                was_seen=seen
            latency=(time.monotonic()-begin)*1000;durations.append(latency);inference.append((after-before)*1000)
            row=dict(frame=index+1,latency_ms=latency,inference_ms=inference[-1],ground_truth=gt,
                matched_id=best.id if seen else None,iou=overlap,
                tracks=[dict(id=t.id,box=t.bbox,confidence=t.confidence) for t in tracks])
            raw.write(json.dumps(row)+'\n');rows.append(row)
    elapsed=time.monotonic()-started;source.close();n=len(rows);evaluated=sum(r['ground_truth'] is not None for r in rows)
    report=dict(scope='PC CPU, sequential fixed-frame replay; not Hailo performance',clip=a.clip,frames=n,
        model_sha256=detector.sha,engine=detector.engine,source_pixels_sha256=digest.hexdigest(),opencv=cv2.__version__,python=platform.python_version(),
        processed_fps=n/elapsed if elapsed else 0,latency_p50_ms=float(np.percentile(durations,50)) if n else None,
        latency_p95_ms=float(np.percentile(durations,95)) if n else None,
        inference_p95_ms=float(np.percentile(inference,95)) if n else None,
        annotated_frames=evaluated,matched_frames=matched,missed_frames=evaluated-matched,
        target_coverage=matched/evaluated if evaluated else None,loss_events=loss_events,matched_target_id_changes=switches,
        annotation_path=str(annotation) if truth else None,
        limitations='SOT target coverage at IoU >= 0.5; matched ID changes are not IDF1. No real-time frame dropping. Scene/render load affects PC throughput.')
    with a.out.open('x') as output:json.dump(report,output,indent=2)
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
