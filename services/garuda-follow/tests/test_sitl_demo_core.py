import math
import pathlib
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from sitl_demo_core import DemoControl

def snapshot(seq=1,gesture=None):
    return {'age_ms':10,'frame':{'seq':seq,'epoch':'camera','width':1280,'height':720},
        'tracks':[{'id':7,'x':600,'y':100,'w':80,'h':126,'conf':.9,'coasting':False,'gesture':{'name':gesture}}]}

def selected():
    c=DemoControl();c.receive(snapshot(),0);c.select(7);c.gestures=True;return c

def test_gestures_require_selection_and_are_onset_only():
    c=DemoControl();c.gestures=True
    assert c.receive(snapshot(1,'follow_me'),0) is None
    c.select(7)
    assert c.receive(snapshot(2,'follow_me'),.1) is None
    assert c.receive(snapshot(3,'follow_me'),.7) is None
    assert c.receive(snapshot(4,'follow_me'),1.4)=='takeoff_follow'
    assert c.receive(snapshot(5,'follow_me'),1.5) is None
    assert c.receive(snapshot(6,None),1.6) is None
    assert c.receive(snapshot(7,'circle'),1.7) is None
    assert c.receive(snapshot(8,'circle'),2.3) is None
    assert c.receive(snapshot(9,'circle'),3.)=='circle'

def test_stale_epoch_change_and_bystanders_cannot_command():
    for kind in ('stale','epoch','bystander','coast'):
        c=selected();s=snapshot(2,'land')
        if kind=='stale':s['age_ms']=900
        elif kind=='epoch':s['frame']['epoch']='new'
        elif kind=='bystander':s['tracks'][0]['id']=8
        else:s['tracks'][0]['coasting']=True
        assert c.receive(s,.1) is None

def test_stale_camera_and_lost_target_hold_and_freeze_orbit():
    c=selected();c.mode='circle';c.velocity([0,0,-1.5],.1,.05);angle=c.angle
    assert c.velocity([0,0,-1.5],2,.05)==(0,0,None)
    assert c.angle==angle
    s=snapshot(3);s['tracks']=[];c.receive(s,2.1)
    assert c.velocity([0,0,-1.5],2.1,.05)==(0,0,None)

def test_orbit_goes_around_target_and_obeys_speed_cap():
    c=selected();c.mode='circle';p=[0.,0.,-1.5];quadrants=set()
    for i in range(2400):
        now=i*.05;c.receive(snapshot(i+2),now)
        vn,ve,yaw=c.velocity(p,now,.05)
        assert math.hypot(vn,ve)<=c.speed+1e-9
        p[0]+=vn*.05;p[1]+=ve*.05
        if i>400:quadrants.add((math.copysign(1,p[0]-4),math.copysign(1,p[1])))
    assert len(quadrants)==4

def test_identical_frame_does_not_keep_camera_fresh():
    c=selected();c.mode='follow'
    c.receive(snapshot(),3)
    assert c.velocity([0,0,0],3,.05)==(0,0,None)

def onboard(distance=6):
    s=snapshot();s['kind']='gazebo';s['frame']['height']=720
    s['camera']={'hfov':1.2,'pitch':0,'depth_paired':True}
    s['tracks'][0].update(y=275,h=200,range_m=distance)
    c=DemoControl();c.receive(s,0);c.select(7);c.mode='follow'
    return c

def test_onboard_projection_rotates_with_vehicle_attitude():
    c=onboard();c.velocity([0,0,-1.5],.1,.05,[1,0,0,0])
    assert c.target[0]>6 and abs(c.target[1])<.01
    c.velocity([0,0,-1.5],.1,.05,[math.sqrt(.5),0,0,math.sqrt(.5)])
    assert c.target[1]>6 and abs(c.target[0])<.01

def test_onboard_requires_depth_and_replay_never_commands_motion():
    c=onboard(None)
    assert c.velocity([0,0,0],.1,.05,[1,0,0,0])==(0,0,None)
    c=onboard();c.snapshot['kind']='replay'
    assert c.velocity([0,0,0],.1,.05,[1,0,0,0])==(0,0,None)

def test_acceleration_and_orbit_direction():
    c=onboard();c.mode='circle'
    vn,ve,yaw=c.velocity([0,0,-1.5],.1,.05,[1,0,0,0])
    assert math.hypot(vn,ve)<=c.acceleration*.05+1e-9
    sign=math.copysign(1,ve);c.direction=-1;c.last_velocity=(0,0)
    assert math.copysign(1,c.velocity([0,0,-1.5],.1,.05,[1,0,0,0])[1])!=sign

def test_hold_is_immediate_and_stall_resets_gesture_countdown():
    c=selected();assert c.receive(snapshot(2,'stop'),.1)=='hold'
    c.receive(snapshot(3,'land'),.2)
    assert c.receive(snapshot(4,'land'),2.) is None
    assert c.gesture_pending['remaining_s']==c.confirm_s

def test_source_reset_clears_authority():
    c=selected();c.reset()
    assert c.target_id is None and c.mode=='hold' and c.gesture_pending is None

def test_booth_mapping_responds_to_distance_like_original_demo():
    c=selected();c.mode='follow';c.velocity([0,0,0],.1,.05);far=c.target[0]
    c.snapshot['tracks'][0]['h']=400
    c.velocity([0,0,0],.1,.05)
    assert c.target[0]<far

def test_confident_appearance_return_requires_distinct_frames_and_dwell():
    c=selected();profile=[1.]*128;c.snapshot['tracks'][0]['appearance']=profile;c.track(0)
    c.mode='follow'
    for i,now in enumerate((.2,.5,.8,1.1)):
        s=snapshot(i+2);s['tracks'][0].update(id=9,appearance=profile)
        c.receive(s,now);c.track(now)
        if i<3:assert c.target_id==7
    assert c.target_id==9 and c.mode=='follow' and c.reacquired

def test_similar_bystanders_never_auto_reacquire():
    c=selected();profile=[1.]*128;c.snapshot['tracks'][0]['appearance']=profile;c.track(0)
    for i in range(10):
        s=snapshot(i+2);s['tracks'][0].update(id=9,appearance=profile)
        s['tracks'].append(dict(s['tracks'][0],id=10));c.receive(s,i*.2);assert c.track(i*.2) is None
    assert c.target_id==7 and c.candidate['ambiguous']
