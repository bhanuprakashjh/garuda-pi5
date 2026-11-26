"""Live experimental StereoNet backend. Official HEF only; not product-cleared.
Pi rectification/projection, Hailo disparity. No on-camera depth or flight output.
"""
import hashlib,time
from pathlib import Path
import cv2
import numpy as np
from host_stereo import HostStereo


def prepare(gray):
    if gray.ndim!=2 or gray.dtype!=np.uint8:raise ValueError('expected mono uint8 image')
    h,w=gray.shape;crop_h=min(h,round(w*368/1232));y0=(h-crop_h)//2
    resized=cv2.resize(gray[y0:y0+crop_h],(1232,368))
    return np.ascontiguousarray(np.repeat(resized[:,:,None],3,axis=2)),(y0,crop_h,1232/w)


def restore(disparity,shape,crop):
    if disparity.shape!=(368,1232):raise ValueError('unexpected model output shape')
    y,h,scale=crop;out=np.zeros(shape,np.float32)
    clean=np.where(np.isfinite(disparity)&(disparity>0),disparity,0).astype(np.float32)
    out[y:y+h]=cv2.resize(clean,(shape[1],h),interpolation=cv2.INTER_NEAREST)/scale
    return out


class StereoNetModel:
    def __init__(self,hef):
        from hailo_platform import VDevice,FormatType
        path=Path(hef)
        if not path.is_file():raise FileNotFoundError(path)
        self.path=path;self.vd=None;self.cm=None
        try:
            self.vd=VDevice(VDevice.create_params())
            self.im=self.vd.create_infer_model(str(path));self.im.set_batch_size(1)
            if set(self.im.input_names)!={'stereonet/input_layer1','stereonet/input_layer2'}:raise ValueError('unexpected StereoNet inputs')
            if list(self.im.output_names)!=['stereonet/conv53']:raise ValueError('unexpected StereoNet output')
            for name in self.im.input_names:
                if tuple(self.im.input(name).shape)!=(368,1232,3):raise ValueError('unexpected input shape')
            if tuple(self.im.output('stereonet/conv53').shape)!=(368,1232,1):raise ValueError('unexpected output shape')
            self.im.output('stereonet/conv53').set_format_type(FormatType.FLOAT32)
            self.cm=self.im.configure()
        except Exception:
            self.close();raise
    def infer(self,li,ri):
        b=self.cm.create_bindings();b.input('stereonet/input_layer1').set_buffer(li);b.input('stereonet/input_layer2').set_buffer(ri)
        out=np.empty((368,1232,1),np.float32);b.output('stereonet/conv53').set_buffer(out)
        self.cm.run([b],5000)
        return out[:,:,0]

    def close(self):
        try:
            if self.cm is not None:
                self.cm.shutdown();self.cm=None
        finally:
            if self.vd is not None:
                self.vd.release();self.vd=None


class HailoHostStereo(HostStereo):
    def __init__(self,*args,model,**kwargs):
        super().__init__(*args,**kwargs);self.model=model;self.matcher=None
        self.metadata={k:v for k,v in self.metadata.items() if k not in ('disparities','block_size','uniqueness_ratio','P1','P2','disp12MaxDiff','speckleWindowSize','speckleRange','mode')}
        self.metadata.update(backend='hailo-stereonet',bench_only=True,accuracy_validated=False,
            model_sha256=hashlib.sha256(model.path.read_bytes()).hexdigest(),output='FLOAT32-dequantized',
            model_input_size=[1232,368],coverage='central-aspect-crop-only',disparity_resampling='nearest',
            confidence_available=False,measurement_qualified=False,qualification_reason='no_prediction_reliability_or_occlusion_validation')

    def compute(self,left,right,rgb,rgb_is_undistorted=False):
        t0=time.monotonic_ns();l,r=self.rectify(left,right);t1=time.monotonic_ns()
        li,crop=prepare(l);ri,_=prepare(r);t2=time.monotonic_ns()
        out=self.model.infer(li,ri);t3=time.monotonic_ns()
        disparity=restore(out,left.shape,crop);t4=time.monotonic_ns()
        depth=self.project(disparity);t5=time.monotonic_ns()
        image=rgb if rgb_is_undistorted else cv2.remap(rgb,*self.rgb_map,cv2.INTER_LINEAR)
        self.last_timing=dict(rectify_ns=t1-t0,prepare_ns=t2-t1,infer_ns=t3-t2,restore_ns=t4-t3,project_ns=t5-t4,rgb_remap_ns=time.monotonic_ns()-t5)
        return image,depth,disparity,l,r
