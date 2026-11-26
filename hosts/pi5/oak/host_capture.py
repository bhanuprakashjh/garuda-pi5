"""Bounded OAK capture, independent RGB delivery and host stereo worker."""
import hashlib,json,time,threading,uuid
from pathlib import Path
import cv2
import numpy as np
import depthai as dai
from host_stereo import HostStereo
from host_flow import Trace,LatestJob,TripleSelector,FrameRing,require_exclusive_hailo
from pairing import Pairing,Frame


def capture(owner,video=None,record_dir=None,stereo_hef=None,map_interval_ms=0,map_on_demand=False):
    cv2.setNumThreads(2)
    s=owner.state;c=owner.condition;backend=None;model=None;thread=None
    jobs=LatestJob();selector=TripleSelector(rgb_skew_ns=33_333_333);trace=Trace();owner.server.trace=trace
    # Preview RGB association uses the published pair limit; stereo mates retain 5 ms.
    # Neither this association nor factory projection qualifies an object measurement.
    s.update(stereo_pair_limit_ns=5_000_000,preview_rgb_span_limit_ns=33_333_333,map_interval_ms=map_interval_ms,map_on_demand=bool(map_on_demand))
    # The full map can be made deliberately slower; frames, RGB delivery and /box-depth are unaffected.
    map_interval_ns=int(map_interval_ms*1e6);last_map_ns=0;map_skips=0
    backend_name='hailo-stereonet' if stereo_hef else 'pi-sgbm'
    saved=0;record_path=None;worker_error=[]
    ring=FrameRing();box_lock=threading.Lock()
    def box_depth(epoch,rgb_host_ns,roi):
        """Stereo for one RGB region of one already-captured colour frame. Display only:
        the answer is an unvalidated preview and goes nowhere but the caller."""
        if backend is None or owner.pairing is None:return 503,dict(reason='not_ready'),b''
        if backend_name!='pi-sgbm':return 503,dict(reason='box_depth_is_cpu_stereo_only'),b''
        if epoch!=owner.pairing.epoch:return 409,dict(reason='epoch_changed'),b''
        triple,reason=ring.find(rgb_host_ns)
        if triple is None:return 409,dict(reason=reason),b''
        if not box_lock.acquire(blocking=False):return 429,dict(reason='busy'),b''
        try:
            started=time.monotonic_ns()
            patch,info=backend.box_depth(triple['left']['frame'].getCvFrame(),triple['right']['frame'].getCvFrame(),roi)
            done=time.monotonic_ns()
        except ValueError as exc:return 409,dict(reason=str(exc)),b''
        finally:box_lock.release()
        rgb,left,right=(triple[k] for k in ('rgb','left','right'));stamps=[f['device_ns'] for f in (rgb,left,right)]
        meta=dict(schema='garuda.oak-box-depth.1',epoch=epoch,roi=list(roi),width=int(patch.shape[1]),height=int(patch.shape[0]),
            format='Z16LE',length=int(patch.size*2),depth_backend='pi-sgbm-box',preview_only=True,measurement_qualified=False,
            qualification_reason=backend.metadata['qualification_reason'],
            rgb={k:rgb[k] for k in ('seq','device_ns','host_ns')},left={k:left[k] for k in ('seq','device_ns','host_ns')},
            right={k:right[k] for k in ('seq','device_ns','host_ns')},
            left_right_skew_ns=abs(left['device_ns']-right['device_ns']),rgb_span_ns=max(stamps)-min(stamps),
            limits=dict(left_right_skew_ns=ring.skew_ns,rgb_span_ns=ring.rgb_skew_ns),
            calibration_id=owner.pairing.calibration_id,geometry=owner.pairing.geometry,depth_reference_frame='unverified',
            compute_ns=done-started,served_host_ns=done,**info)
        trace.add('box_depth',rgb_seq=rgb['seq'],left_seq=left['seq'],right_seq=right['seq'],compute_start_ns=started,compute_done_ns=done,
            stages={k:info[k] for k in ('rectify_ns','match_ns','project_ns')},strip=info['strip'])
        return 200,meta,patch.astype('<u2').tobytes()
    def worker():
        nonlocal saved
        try:
            while True:
                job=jobs.take()
                if job is None:return
                rgb,left,right=(job[k] for k in ('rgb','left','right'))
                started=time.monotonic_ns()
                if started-min(f['host_ns'] for f in job.values())>500_000_000:
                    trace.add('depth_rejected',reason='stale_before_compute',rgb_seq=rgb['seq'],host_ns=started);continue
                raw_l=left['frame'].getCvFrame();raw_r=right['frame'].getCvFrame();decoded=time.monotonic_ns()
                image,depth,disparity,l,r=backend.compute(raw_l,raw_r,rgb['image'],rgb_is_undistorted=True)
                done=time.monotonic_ns()
                row=dict(rgb_seq=rgb['seq'],left_seq=left['seq'],right_seq=right['seq'],
                    capture_host_ns={k:f['host_ns'] for k,f in job.items()},
                    capture_device_ns={k:f['device_ns'] for k,f in job.items()},
                    dequeue_ns={k:f['dequeue_ns'] for k,f in job.items()},compute_start_ns=started,
                    compute_done_ns=done,stages=dict(backend.last_timing,input_decode_ns=decoded-started),rgb_publish_ns=rgb['publish_ns'])
                if done-min(f['host_ns'] for f in job.values())>500_000_000:
                    trace.add('depth_rejected',reason='stale_after_compute',**row);continue
                payload=image.tobytes();depth_bytes=depth.astype('<u2').tobytes()
                with c:
                    arrived=time.monotonic_ns()
                    owner.record_stream('depth',left['frame'])
                    owner.pairing.add('depth',Frame(left['seq'],left['device_ns'],left['host_ns'],depth_bytes,arrived))
                    owner.pairing.add('rgb',Frame(rgb['seq'],rgb['device_ns'],rgb['host_ns'],payload,arrived))
                    owner.pairing.advance(arrived);published=time.monotonic_ns();owner.pairing.publish_host_ns=published
                    s.update(depth_last_ns=published,depth_processed=s.get('depth_processed',0)+1,
                        stereo_compute_ms=(done-started)/1e6,depth_valid_fraction=float(np.mean(depth>0)),
                        depth_nonzero_fraction=float(np.mean(depth>0)),measurement_qualified=False,
                        depth_quality='unqualified_preview' if np.any(depth) else 'empty_preview',
                        matcher_positive_fraction=float(np.mean(disparity>0)),depth_error=None)
                    c.notify_all()
                trace.add('depth',publish_host_ns=published,**row)
                if record_path and saved<5:
                    # Separate run directory; restart never overwrites old records.
                    np.savez_compressed(record_path/f'pair-{saved:02d}.npz',left=raw_l,right=raw_r,rgb=rgb['raw_image'],
                        rectified_left=l,rectified_right=r,disparity=disparity,depth=depth)
                    saved+=1
        except Exception as exc:
            worker_error.append(str(exc))
            with c:s['depth_error']=str(exc);c.notify_all()
            trace.add('depth_error',error=str(exc),host_ns=time.monotonic_ns())
    try:
        if stereo_hef:
            # Reserve/validate NPU before opening the camera. Busy/missing model is
            # a configuration failure, never a recurring camera restart.
            from hailo_host_stereo import StereoNetModel
            try:
                require_exclusive_hailo()
                model=StereoNetModel(stereo_hef)
            except Exception as exc:
                print('Hailo preflight failed:',repr(exc),flush=True);raise SystemExit(78)
        if record_dir:
            record_path=Path(record_dir)/uuid.uuid4().hex;record_path.mkdir(parents=True)
        with dai.Device() as dev:
            dev.setIrLaserDotProjectorIntensity(0);dev.setIrFloodLightIntensity(0)
            cal=dev.readCalibration()
            lr=np.array(cal.getCameraExtrinsics(dai.CameraBoardSocket.CAM_B,dai.CameraBoardSocket.CAM_C,False,dai.LengthUnit.METER))
            la=np.array(cal.getCameraExtrinsics(dai.CameraBoardSocket.CAM_B,dai.CameraBoardSocket.CAM_A,False,dai.LengthUnit.METER))
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            with c:s.update(stereo_enabled=True,imu_enabled=False,imu_config=None,rgb_undistorted=True,
                usb=str(dev.getUsbSpeed()).split('.')[-1],depth_backend=backend_name,camera_compute='capture-and-encoding-only',
                runtime_versions=dict(depthai=dai.__version__,opencv=cv2.__version__,numpy=np.__version__))
            with dai.Pipeline(dev) as pipeline:
                queues={}
                camera=None
                for name,socket,size,typ in [('rgb',dai.CameraBoardSocket.CAM_A,(1280,720),dai.ImgFrame.Type.BGR888i),('left',dai.CameraBoardSocket.CAM_B,(640,400),dai.ImgFrame.Type.GRAY8),('right',dai.CameraBoardSocket.CAM_C,(640,400),dai.ImgFrame.Type.GRAY8)]:
                    cam=pipeline.create(dai.node.Camera).build(socket)
                    out=cam.requestOutput(size,type=typ,fps=15,enableUndistortion=False)
                    queues[name]=out.createOutputQueue(maxSize=2,blocking=False)
                    if name=='rgb':camera=cam
                video_queue=None
                if video:
                    from video_link import VideoLink
                    owner.video_link=VideoLink(video['codec'],video['host'],video['port'])
                    out=camera.requestOutput((1920,1080),type=dai.ImgFrame.Type.NV12,fps=15,enableUndistortion=False)
                    enc=pipeline.create(dai.node.VideoEncoder)
                    profile=dai.VideoEncoderProperties.Profile.H265_MAIN if video['codec']=='h265' else dai.VideoEncoderProperties.Profile.H264_MAIN
                    enc.setDefaultProfilePreset(15,profile);enc.setRateControlMode(dai.VideoEncoderProperties.RateControlMode.CBR)
                    enc.setBitrateKbps(video['kbps']);enc.setKeyframeFrequency(15);enc.setNumBFrames(0)
                    out.link(enc.input);video_queue=enc.bitstream.createOutputQueue(maxSize=4,blocking=False)
                pipeline.start();ready_ns=None;start=time.monotonic();count=0
                while pipeline.isRunning():
                    if video_queue:
                        v=video_queue.tryGet()
                        if v is not None:
                            owner.video_link.submit(v.getData().tobytes())
                            with c:owner.record_stream('video',v)
                    received=False
                    for name,q in queues.items():
                        for _ in range(4):
                            f=q.tryGet()
                            if f is None:break
                            received=True;dequeued=time.monotonic_ns()
                            packet=dict(seq=f.getSequenceNum(),device_ns=int(f.getTimestampDevice().total_seconds()*1e9),
                                host_ns=int(f.getTimestamp().total_seconds()*1e9),dequeue_ns=dequeued,frame=f)
                            with c:owner.record_stream(name,f)
                            trace.add('dequeue',stream=name,**{k:v for k,v in packet.items() if k!='frame'})
                            if ready_ns and packet['host_ns']<ready_ns:
                                trace.add('startup_discard',stream=name,seq=packet['seq']);continue
                            if ready_ns and name=='rgb':
                                t0=time.monotonic_ns();raw=f.getCvFrame();decoded=time.monotonic_ns()
                                image=cv2.remap(raw,*backend.rgb_map,cv2.INTER_LINEAR);t1=time.monotonic_ns()
                                ok,jpeg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,75]);t2=time.monotonic_ns()
                                if not ok:raise RuntimeError('JPEG encode failed')
                                payload=image.tobytes()
                                with c:
                                    published=time.monotonic_ns()
                                    s.update(raw=payload,raw_seq=packet['seq'],capture_ns=packet['host_ns'],jpeg=jpeg.tobytes(),
                                        seq=s['seq']+1,last=time.monotonic(),error=None)
                                    owner.server.rgb_pairing.publish_rgb(Frame(packet['seq'],packet['device_ns'],packet['host_ns'],payload,published))
                                    owner.server.rgb_pairing.publish_host_ns=published
                                    count+=1
                                    if time.monotonic()-start>=1:s['fps']=count/(time.monotonic()-start);count=0;start=time.monotonic()
                                    c.notify_all()
                                packet.update(image=image,raw_image=raw,publish_ns=published)
                                trace.add('rgb',seq=packet['seq'],capture_host_ns=packet['host_ns'],dequeue_ns=dequeued,
                                    publish_host_ns=published,decode_ns=decoded-t0,remap_ns=t1-decoded,jpeg_ns=t2-t1)
                            selector.add(name,packet)
                            if ready_ns:ring.add(name,packet if name!='rgb' else {k:packet[k] for k in ('seq','device_ns','host_ns')})
                    triple=selector.select()
                    if triple and backend is None:
                        left,right,rgb=(triple[k]['frame'] for k in ('left','right','rgb'))
                        transforms=[f.getTransformation() for f in (left,right,rgb)]
                        matrices=[np.array(t.getIntrinsicMatrix(),dtype=np.float64) for t in transforms]
                        distortions=[np.array(t.getDistortionCoefficients(),dtype=np.float64) for t in transforms]
                        cls=HostStereo;extra={}
                        if model:
                            from hailo_host_stereo import HailoHostStereo
                            cls=HailoHostStereo;extra={'model':model}
                        backend=cls(matrices[0],distortions[0],matrices[1],distortions[1],lr,la,matrices[2],distortions[2],**extra)
                        sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
                        identity=dict(factory=cal.eepromToJson(),k=[k.tolist() for k in matrices],d=[d.tolist() for d in distortions],backend=backend.metadata,sources=sources)
                        cid=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
                        pairing=Pairing(boot,cid);pairing.depth_backend=backend_name
                        pairing.measurement_qualified=False;pairing.qualification_reason=backend.metadata['qualification_reason']
                        k=matrices[2]
                        pairing.geometry=dict(schema_version=1,epoch=pairing.epoch,calibration_id=cid,width=1280,height=720,
                            fx=float(k[0,0]),fy=float(k[1,1]),cx=float(k[0,2]),cy=float(k[1,2]),distortion='none',
                            source='host-opencv-factory-calibration',reference_frame='unverified',verified=False)
                        rgb_pairing=Pairing(boot,cid);rgb_pairing.epoch=pairing.epoch
                        rgb_pairing.geometry=pairing.geometry;rgb_pairing.measurement_qualified=False
                        rgb_pairing.depth_backend=backend_name;rgb_pairing.qualification_reason=pairing.qualification_reason
                        owner.server.rgb_pairing=rgb_pairing
                        with c:owner.pairing=pairing;s.update(epoch=pairing.epoch,geometry=pairing.geometry,stereo_config=backend.metadata,source_hashes=sources)
                        if record_path:(record_path/'calibration.json').write_text(json.dumps(identity))
                        ready_ns=time.monotonic_ns();selector.clear();ring.clear();owner.server.box_depth=box_depth
                        trace.add('ready',host_ns=ready_ns,epoch=pairing.epoch,sources=sources)
                        thread=threading.Thread(target=worker,name='host-stereo-worker');thread.start()
                    elif triple and not worker_error:
                        now_ns=time.monotonic_ns()
                        # On demand: a full map is computed only while something is asking for one.
                        wanted=not map_on_demand or now_ns-getattr(owner.server,'map_demand_ns',0)<3_000_000_000
                        if wanted and now_ns-last_map_ns>=map_interval_ns:jobs.put(triple);last_map_ns=now_ns
                        else:map_skips+=1
                    with c:s['flow_counters']=dict(selector.stats,worker_overwrites=jobs.overwrites,map_interval_skips=map_skips)
                    if not received:time.sleep(.001)
    except Exception as exc:
        with c:s.update(error=str(exc),jpeg=None,raw=None);c.notify_all()
        print('Host stereo error:',repr(exc),flush=True)
    finally:
        owner.server.box_depth=None
        jobs.close()
        if thread:thread.join()
        if model:model.close()
        if owner.video_link:owner.video_link.close()
