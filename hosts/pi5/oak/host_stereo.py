"""CPU stereo and calibrated RGB projection. No camera/NPU dependency.

Depth output is Z in the undistorted RGB optical frame, in millimetres.
Factory geometry is used; physical validation remains separate.
"""
import time
import cv2
import numpy as np


class HostStereo:
    def __init__(self, left_k, left_d, right_k, right_d, left_to_right, left_to_rgb,
                 rgb_k, rgb_d, size=(640,400), rgb_size=(1280,720), disparities=96):
        self.size,self.rgb_size=size,rgb_size
        self.rgb_k=np.asarray(rgb_k,dtype=np.float64)
        self.left_to_rgb=np.asarray(left_to_rgb,dtype=np.float64)
        lr=np.asarray(left_to_right,dtype=np.float64)
        if not .001 < np.linalg.norm(lr[:3,3]) < 1:raise ValueError('stereo translation must be metres')
        self.r1,r2,p1,p2,self.q,_,_=cv2.stereoRectify(np.asarray(left_k),np.asarray(left_d),
            np.asarray(right_k),np.asarray(right_d),size,lr[:3,:3],lr[:3,3],
            flags=cv2.CALIB_ZERO_DISPARITY,alpha=0)
        self.p1=p1;self.disparities=disparities
        if p2[0,3]>=0:raise ValueError('positive-disparity backend requires left-to-right negative X baseline')
        self.left_map=cv2.initUndistortRectifyMap(np.asarray(left_k),np.asarray(left_d),self.r1,p1,size,cv2.CV_16SC2)
        self.right_map=cv2.initUndistortRectifyMap(np.asarray(right_k),np.asarray(right_d),r2,p2,size,cv2.CV_16SC2)
        self.rgb_map=cv2.initUndistortRectifyMap(self.rgb_k,np.asarray(rgb_d),None,self.rgb_k,rgb_size,cv2.CV_16SC2)
        self.matcher=cv2.StereoSGBM_create(minDisparity=0,numDisparities=disparities,
            blockSize=5,P1=8*25,P2=32*25,disp12MaxDiff=1,uniquenessRatio=10,
            speckleWindowSize=100,speckleRange=2,mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
        # Separate instance: the box route runs on another thread than the full-frame worker.
        self.box_matcher=cv2.StereoSGBM_create(minDisparity=0,numDisparities=disparities,
            blockSize=5,P1=8*25,P2=32*25,disp12MaxDiff=1,uniquenessRatio=10,
            speckleWindowSize=100,speckleRange=2,mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
        self.metadata=dict(backend='pi-sgbm',opencv=cv2.__version__,native_size=list(size),
            disparities=disparities,block_size=5,uniqueness_ratio=10,
            output_axis='undistorted-rgb-optical-z',physical_geometry_verified=False,
            alignment='project-3d-z-buffer-no-hole-fill',P1=200,P2=800,disp12MaxDiff=1,
            speckleWindowSize=100,speckleRange=2,mode='SGBM_3WAY',alpha=0,
            remap_interpolation='linear',projection_version=3,measurement_qualified=False,
            qualification_reason='sparse_registration_not_validated')

    def rectify(self,left,right):
        if left.shape!=self.size[::-1] or right.shape!=left.shape:raise ValueError('unexpected stereo size')
        return tuple(cv2.remap(im,*maps,cv2.INTER_LINEAR) for im,maps in [(left,self.left_map),(right,self.right_map)])

    def fused_matrix(self):
        """One 3x4 map from a rectified pixel and its disparity (u, v, d, 1) to
        W*z*(u', v', 1) in the RGB image, where W is the homogeneous scale of Q and z the
        RGB optical depth. It composes exactly the transforms project_v2 applies in turn.
        Returns None when Q is not the usual stereoRectify form, so the caller keeps v2."""
        q=self.q
        if np.any(q[2,:3]!=0) or np.any(q[3,:2]!=0) or not q[2,3]>0 or not np.allclose(self.rgb_k[2],[0,0,1]):return None
        pose=np.hstack([self.left_to_rgb[:3,:3]@self.r1.T,self.left_to_rgb[:3,3:4]])
        return self.rgb_k@pose@q

    def project(self, disparity):
        """Projection v3: same geometry and nearest-Z collision rule as v2, evaluated per
        valid pixel in one pass instead of building and filtering 3-D point arrays.
        Arithmetic is float64 on the float32 disparity; v2 rounded the 3-D point to
        float32 first, so a pixel on a rounding boundary can differ (see benchlog)."""
        m=getattr(self,'_fused',False)
        if m is False:
            m=self._fused=self.fused_matrix()
            if m is not None:
                w,h=self.size;u,v=np.meshgrid(np.arange(w,dtype=np.float64),np.arange(h,dtype=np.float64))
                self._base=[(m[k,0]*u+m[k,1]*v+m[k,3]).ravel() for k in range(3)]
        if m is None:return self.project_v2(disparity)
        w,h=self.rgb_size;flat=disparity.ravel();index=np.flatnonzero((flat>0)&np.isfinite(flat))
        result=np.full(h*w,65535,dtype=np.uint16)
        if len(index):
            d=flat[index].astype(np.float64);scale=self.q[3,2]*d+self.q[3,3]
            hz=self._base[2][index]+m[2,2]*d
            with np.errstate(divide='ignore',invalid='ignore'):
                z=hz/scale
                keep=(scale>0)&(z>.05)&(z<65.535)
                x=np.rint((self._base[0][index]+m[0,2]*d)/hz);y=np.rint((self._base[1][index]+m[1,2]*d)/hz)
            keep&=(x>=0)&(x<w)&(y>=0)&(y<h)
            np.minimum.at(result,(y[keep]*w+x[keep]).astype(np.intp),np.rint(z[keep]*1000).astype(np.uint16))
        result[result==65535]=0
        return result.reshape(h,w)

    def box_window(self,roi,pad=6):
        """Rows and columns of the rectified left image that can hold the RGB region
        `roi` (x0,y0,x1,y1, undistorted RGB pixels) at any depth the matcher can report:
        from its nearest range (largest disparity) out to 100 m. Returns (r0,r1,c0,c1) or None."""
        x0,y0,x1,y1=roi;w,h=self.size
        rays=np.linalg.inv(self.rgb_k)@np.array([[x,y,1.] for x in (x0,(x0+x1)/2,x1) for y in (y0,(y0+y1)/2,y1)]).T
        near=self.p1[0,0]*abs(1/self.q[3,2])/self.disparities
        rotation=self.r1@self.left_to_rgb[:3,:3].T
        points=np.hstack([rotation@(rays*z-self.left_to_rgb[:3,3:4]) for z in (near,100.)])
        if np.any(points[2]<=0):return None
        u=self.p1[0,0]*points[0]/points[2]+self.p1[0,2];v=self.p1[1,1]*points[1]/points[2]+self.p1[1,2]
        r0,r1=max(0,int(np.floor(v.min()))-pad),min(h,int(np.ceil(v.max()))+pad+1)
        c0,c1=max(0,int(np.floor(u.min()))-pad),min(w,int(np.ceil(u.max()))+pad+1)
        return (r0,r1,c0,c1) if r1-r0>=16 and c1-c0>=16 else None

    def box_depth(self,left,right,roi):
        """Stereo for one RGB region only. Rectifies and matches just the strip of the
        pair that can contain `roi`, then places each valid pixel in the RGB image with
        the same geometry and nearest-Z rule as project(). Returns a uint16 millimetre
        patch covering exactly `roi`, or raises ValueError. Same matcher settings as the
        full frame, but a strip has different borders, so values need not be identical.
        Unvalidated preview, like the full map."""
        if left.shape!=self.size[::-1] or right.shape!=left.shape:raise ValueError('unexpected stereo size')
        x0,y0,x1,y1=roi;W,H=self.rgb_size
        if not (all(type(v) is int for v in roi) and 0<=x0<x1<=W and 0<=y0<y1<=H):raise ValueError('roi outside the RGB image')
        m=self.fused_matrix()
        if m is None:raise ValueError('unsupported rectification geometry')
        window=self.box_window(roi)
        if window is None:raise ValueError('roi does not map into the stereo image')
        t0=time.monotonic_ns();r0,r1,c0,c1=window
        # The matcher cannot report the first `disparities` columns of what it is given.
        s0=max(0,c0-self.disparities)
        l,r=(cv2.remap(im,np.ascontiguousarray(maps[0][r0:r1,s0:c1]),np.ascontiguousarray(maps[1][r0:r1,s0:c1]),cv2.INTER_LINEAR)
             for im,maps in [(left,self.left_map),(right,self.right_map)])
        t1=time.monotonic_ns()
        disparity=self.box_matcher.compute(l,r).astype(np.float32)/16.;t2=time.monotonic_ns()
        d=disparity[:,c0-s0:];rows,cols=np.nonzero((d>0)&np.isfinite(d))
        patch=np.full((y1-y0)*(x1-x0),65535,dtype=np.uint16)
        if len(rows):
            d=d[rows,cols].astype(np.float64);u=cols+float(c0);v=rows+float(r0)
            scale=self.q[3,2]*d+self.q[3,3];hz=m[2,0]*u+m[2,1]*v+m[2,2]*d+m[2,3]
            with np.errstate(divide='ignore',invalid='ignore'):
                z=hz/scale
                keep=(scale>0)&(z>.05)&(z<65.535)
                x=np.rint((m[0,0]*u+m[0,1]*v+m[0,2]*d+m[0,3])/hz);y=np.rint((m[1,0]*u+m[1,1]*v+m[1,2]*d+m[1,3])/hz)
            keep&=(x>=x0)&(x<x1)&(y>=y0)&(y<y1)
            np.minimum.at(patch,((y[keep]-y0)*(x1-x0)+(x[keep]-x0)).astype(np.intp),np.rint(z[keep]*1000).astype(np.uint16))
        patch[patch==65535]=0
        timing=dict(rectify_ns=t1-t0,match_ns=t2-t1,project_ns=time.monotonic_ns()-t2)
        return patch.reshape(y1-y0,x1-x0),dict(window=[r0,r1,c0,c1],strip=[int(l.shape[1]),int(l.shape[0])],**timing)

    def project_v2(self, disparity):
        xyz=cv2.reprojectImageTo3D(disparity.astype(np.float32),self.q)
        valid=(disparity>0)&np.isfinite(xyz).all(axis=2)&(xyz[:,:,2]>0)
        # OpenCV R1 maps original left camera to rectified left camera.
        points=xyz[valid].astype(np.float64)
        # Small fixed transforms use OpenCV's direct kernels, avoiding BLAS
        # dispatch/thread overhead for tall Nx3 matrix multiplications.
        if not len(points):return np.zeros(self.rgb_size[::-1],dtype=np.uint16)
        points=cv2.transform(points[:,None,:],self.r1.T)[:,0,:]
        points=cv2.transform(points[:,None,:],self.left_to_rgb[:3,:])[:,0,:]
        points=points[np.isfinite(points).all(axis=1)&(points[:,2]>.05)&(points[:,2]<65.535)]
        if not len(points):return np.zeros(self.rgb_size[::-1],dtype=np.uint16)
        z=points[:,2];pixels=cv2.transform(points[:,None,:],self.rgb_k)[:,0,:]
        uv=np.rint(pixels[:,:2]/z[:,None]).astype(np.int32)
        w,h=self.rgb_size;inside=(uv[:,0]>=0)&(uv[:,0]<w)&(uv[:,1]>=0)&(uv[:,1]<h)
        result=np.full(h*w,65535,dtype=np.uint16)
        np.minimum.at(result,uv[inside,1]*w+uv[inside,0],np.rint(z[inside]*1000).astype(np.uint16))
        result[result==65535]=0
        return result.reshape(h,w)

    def compute(self,left,right,rgb,rgb_is_undistorted=False):
        t0=time.monotonic_ns();l,r=self.rectify(left,right);t1=time.monotonic_ns()
        disparity=self.matcher.compute(l,r).astype(np.float32)/16.;t2=time.monotonic_ns()
        depth=self.project(disparity);t3=time.monotonic_ns()
        image=rgb if rgb_is_undistorted else cv2.remap(rgb,*self.rgb_map,cv2.INTER_LINEAR)
        self.last_timing=dict(rectify_ns=t1-t0,match_ns=t2-t1,project_ns=t3-t2,rgb_remap_ns=time.monotonic_ns()-t3)
        return image,depth,disparity,l,r
