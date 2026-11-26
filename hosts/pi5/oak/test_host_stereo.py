import numpy as np
import pytest
cv2=pytest.importorskip('cv2')
from host_stereo import HostStereo

def backend(rgb_translation=0):
 k=np.array([[400.,0,160],[0,400,100],[0,0,1]])
 lr=np.eye(4);lr[0,3]=-.075
 la=np.eye(4);la[0,3]=rgb_translation
 return HostStereo(k,np.zeros(5),k,np.zeros(5),lr,la,k,np.zeros(5),size=(320,200),rgb_size=(320,200),disparities=64)

def test_metric_disparity_and_invalid():
 b=backend();d=np.full((200,320),15,dtype=np.float32);d[:10]=0
 z=b.project(d)
 assert np.median(z[z>0])==2000
 assert np.all(z[:10]==0)
 assert not np.any(b.project(np.zeros_like(d)))

def test_rgb_projection_translation():
 b=backend(.05);d=np.zeros((200,320),np.float32);d[100,160]=15
 z=b.project(d)
 assert z[100,170]==2000 and np.count_nonzero(z)==1

def test_sgbm_synthetic_shift():
 b=backend();rng=np.random.default_rng(10);left=rng.integers(0,256,(200,320),np.uint8)
 right=np.zeros_like(left);right[:,:-15]=left[:,15:]
 _,depth,d,_,_=b.compute(left,right,np.repeat(left[:,:,None],3,axis=2))
 assert abs(float(np.median(d[30:-30,100:-30]))-15)<.2
 assert abs(float(np.median(depth[depth>0]))-2000)<30

def test_wrong_dimensions():
 with pytest.raises(ValueError):backend().rectify(np.zeros((10,10),np.uint8),np.zeros((10,10),np.uint8))

def test_projection_uses_rgb_z_not_original_left_z():
 b=backend();b.left_to_rgb[2,3]=.1
 d=np.zeros((200,320),np.float32);d[100,160]=15
 z=b.project(d)
 assert z[100,160]==2100

def test_nonfinite_disparity_is_not_valid_depth():
 b=backend();d=np.full((200,320),np.nan,np.float32)
 assert not np.any(b.project(d))

def test_nonidentity_rotation_and_transform_direction():
 b=backend();angle=.1
 b.r1=np.array([[np.cos(angle),0,np.sin(angle)],[0,1,0],[-np.sin(angle),0,np.cos(angle)]])
 b.left_to_rgb[:3,:3]=b.r1.T
 b.left_to_rgb[:3,3]=[.04,.02,.1]
 d=np.zeros((200,320),np.float32);d[100,160]=15
 # Rectified central ray at Z=2 -> inverse R1 -> left-to-RGB.
 point=b.left_to_rgb[:3,:3]@(b.r1.T@np.array([0.,0.,2.]))+b.left_to_rgb[:3,3]
 uv=b.rgb_k@point;u,v=np.rint(uv[:2]/uv[2]).astype(int)
 z=b.project(d)
 assert z[v,u]==round(point[2]*1000)
 assert np.count_nonzero(z)==1


def reference_project(b,disparity):
 # Frozen pre-optimization projection, including nearest-Z collisions and sentinel.
 xyz=cv2.reprojectImageTo3D(disparity.astype(np.float32),b.q)
 valid=(disparity>0)&np.isfinite(xyz).all(axis=2)&(xyz[:,:,2]>0)
 points=xyz[valid]@b.r1
 points=points@b.left_to_rgb[:3,:3].T+b.left_to_rgb[:3,3]
 points=points[np.isfinite(points).all(axis=1)&(points[:,2]>.05)&(points[:,2]<65.535)]
 z=points[:,2];pixels=points@b.rgb_k.T
 uv=np.rint(pixels[:,:2]/z[:,None]).astype(np.int32)
 w,h=b.rgb_size;inside=(uv[:,0]>=0)&(uv[:,0]<w)&(uv[:,1]>=0)&(uv[:,1]<h)
 result=np.full(h*w,65535,dtype=np.uint16)
 np.minimum.at(result,uv[inside,1]*w+uv[inside,0],np.rint(z[inside]*1000).astype(np.uint16))
 result[result==65535]=0
 return result.reshape(h,w)

@pytest.mark.parametrize('angle',[0,.1,-.3])
def test_optimized_projection_matches_reference(angle):
 b=backend(.05)
 b.r1=np.array([[np.cos(angle),0,np.sin(angle)],[0,1,0],[-np.sin(angle),0,np.cos(angle)]])
 b.left_to_rgb[:3,:3]=b.r1.T
 rng=np.random.default_rng(24)
 d=rng.uniform(-1,64,(200,320)).astype(np.float32)
 d[0,:]=np.nan;d[1,:]=0;d[2,:]=np.inf
 # Bit-exactness is the property of v2; v3 is held to the boundary tolerance below.
 np.testing.assert_array_equal(b.project_v2(d),reference_project(b,d))
 old=reference_project(b,d);new=b.project(d);changed=old!=new;both=changed&(old>0)&(new>0)
 # Random disparity has unrelated neighbours, so a one-pixel boundary shift can change
 # which point wins a collision; the count is bounded here, the size on a smooth scene below.
 assert np.count_nonzero(changed)<=.001*np.count_nonzero(old)


def test_projection_v3_matches_v2_except_rounding_boundaries():
    """v3 composes the same transforms in one pass. v2 rounded 3-D points to float32
    first, so only pixels on a rounding boundary may differ, by one pixel or 1 mm."""
    k=np.array([[400.,0,160],[0,400,100],[0,0,1]]);rgb_k=np.array([[790.,0,330],[0,788,170],[0,0,1]])
    lr=np.eye(4);lr[0,3]=-.075
    angle=.02;la=np.eye(4);la[:3,:3]=cv2.Rodrigues(np.array([angle,-angle,angle/2]))[0];la[:3,3]=[.0375,-.001,.002]
    b=HostStereo(k,np.zeros(5),k,np.zeros(5),lr,la,rgb_k,np.zeros(5),size=(320,200),rgb_size=(640,360),disparities=64)
    assert b.fused_matrix() is not None
    rng=np.random.default_rng(7);disparity=rng.uniform(.5,60,(200,320)).astype(np.float32)
    disparity[rng.random((200,320))<.3]=0;disparity[5,5]=-1;disparity[6,6]=np.nan
    old=b.project_v2(disparity);new=b.project(disparity)
    changed=old!=new;both=changed&(old>0)&(new>0)
    assert np.count_nonzero(old)>10000 and abs(int(np.count_nonzero(old))-int(np.count_nonzero(new)))<=4
    assert np.count_nonzero(changed)<=.001*np.count_nonzero(old)
    # Smooth scene (a slanted surface): neighbours are close in depth, so any changed
    # pixel that both versions fill differs by at most a few millimetres.
    u,v=np.meshgrid(np.arange(320,dtype=np.float32),np.arange(200,dtype=np.float32))
    smooth=(8+.05*u+.02*v).astype(np.float32)
    old=b.project_v2(smooth);new=b.project(smooth);changed=old!=new;both=changed&(old>0)&(new>0)
    assert np.count_nonzero(old)>10000 and np.count_nonzero(changed)<=.001*np.count_nonzero(old)
    assert not both.any() or np.abs(old[both].astype(int)-new[both].astype(int)).max()<=3


def test_projection_v3_empty_and_unusual_q_fall_back():
    b=backend()
    assert not b.project(np.zeros((200,320),np.float32)).any()
    b._fused=False;b.q=b.q.copy();b.q[3,0]=1e-3
    disparity=np.full((200,320),10,np.float32)
    assert b.fused_matrix() is None and np.array_equal(b.project(disparity),b.project_v2(disparity))


def textured_pair(shift=15):
    rng=np.random.default_rng(10);left=rng.integers(0,256,(200,320),np.uint8)
    right=np.zeros_like(left);right[:,:-shift]=left[:,shift:]
    return left,right

def test_box_depth_matches_only_a_strip_and_agrees_with_full_frame():
    # 0.05 m gives a whole-pixel shift at this depth; a half-pixel one rounds to even columns only.
    b=backend(.05);left,right=textured_pair()
    roi=(150,60,230,140)
    patch,info=b.box_depth(left,right,roi)
    assert patch.shape==(80,80) and patch.dtype==np.uint16
    valid=patch[patch>0]
    assert valid.size>.5*patch.size and abs(float(np.median(valid))-2000)<30
    # Strip is smaller than the frame it replaces.
    assert info['strip'][0]*info['strip'][1]<.8*320*200
    _,depth,_,_,_=b.compute(left,right,np.repeat(left[:,:,None],3,axis=2))
    full=depth[60:140,150:230];both=(full>0)&(patch>0)
    assert both.sum()>.5*patch.size and abs(float(np.median(patch[both]))-float(np.median(full[both])))<=10

def test_box_window_contains_the_region_at_any_reportable_depth():
    b=backend(.0375);roi=(150,60,230,140);r0,r1,c0,c1=b.box_window(roi)
    m=b.fused_matrix()
    for d in (1.,15.,63.):
        # Rectified pixels that land on the region corners at this disparity must be inside the window.
        for u in range(0,320,4):
            for v in range(0,200,4):
                h=m@np.array([u,v,d,1.]);x,y=h[0]/h[2],h[1]/h[2]
                if 150<=x<230 and 60<=y<140:assert r0<=v<r1 and c0<=u<c1,(d,u,v)

def test_box_depth_refuses_bad_regions_and_sizes():
    b=backend();left,right=textured_pair()
    for roi in [(0,0,0,10),(-1,0,10,10),(0,0,321,10),(10.,0,20,10)]:
        with pytest.raises(ValueError):b.box_depth(left,right,roi)
    with pytest.raises(ValueError):b.box_depth(left[:10],right[:10],(0,0,10,10))

def test_box_depth_empty_scene_gives_empty_patch():
    b=backend();flat=np.full((200,320),128,np.uint8)
    patch,_=b.box_depth(flat,flat,(100,50,200,150))
    assert patch.shape==(100,100) and not patch.any()
