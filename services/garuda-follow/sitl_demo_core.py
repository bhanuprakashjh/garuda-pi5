"""Pure behavior logic for the isolated PC demo. Positions are PX4 local NED.

The booth-to-world target mapping is illustrative, not metric camera localization.
"""
import math

def similarity(a,b):
    if not isinstance(a,list) or not isinstance(b,list) or len(a)!=128 or len(b)!=128:return 0.
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in a+b):return 0.
    aa=sum(x*x for x in a);bb=sum(x*x for x in b)
    return sum(x*y for x,y in zip(a,b))/math.sqrt(aa*bb) if aa>0 and bb>0 else 0.


class DemoControl:
    def __init__(self):
        self.target_id=None; self.epoch=None; self.sequence=None; self.progress=-math.inf
        self.snapshot={}; self.received=-math.inf; self.gesture_key=None
        self.gestures=False; self.mode='hold'; self.gap=2.; self.speed=1.; self.limit=10.
        self.angle=None; self.target=None; self.reason='Select a target'
        self.gesture_since=None;self.gesture_sent=False;self.gesture_pending=None
        self.confirm_s=1.2;self.direction=1;self.acceleration=.8;self.last_velocity=(0.,0.)
        self.home=(0.,0.);self.lock_state='UNSELECTED';self.command_yaw=None
        self.profiles=[];self.excluded=set();self.last_seen=-math.inf;self.last_remembered=None
        self.candidate=None;self.reacquired=False

    def select(self, tid):
        self.target_id=tid; self.epoch=self.snapshot.get('frame',{}).get('epoch')
        self.angle=None; self.gesture_key=None; self.mode='hold'
        self.gesture_since=None;self.gesture_sent=False;self.gesture_pending=None
        self.target=None;self.last_velocity=(0.,0.)
        self.profiles=[];self.excluded=set();self.last_seen=-math.inf;self.last_remembered=None
        self.candidate=None;self.reacquired=False

    def reset(self):
        self.select(None);self.snapshot={};self.sequence=None;self.epoch=None
        self.received=self.progress=-math.inf;self.lock_state='UNSELECTED'

    def receive(self, data, now):
        if now-self.progress>.8:self.gesture_key=None;self.gesture_since=None;self.gesture_sent=False
        self.snapshot=data;self.received=now
        frame=data.get('frame',{});key=(frame.get('epoch'),frame.get('seq'))
        if key==self.sequence:return None
        self.sequence=key;self.progress=now
        if not self.gestures:
            self.gesture_pending=None;self.gesture_key=None;self.gesture_since=None;return None
        t=self.track(now)
        name=t.get('gesture',{}).get('name') if t else None
        if name!=self.gesture_key:
            self.gesture_key=name;self.gesture_since=now;self.gesture_sent=False
        command={'follow_me':'takeoff_follow','stop':'hold','land':'land','circle':'circle'}.get(name)
        if not command:self.gesture_pending=None;return None
        remaining=max(0.,self.confirm_s-(now-self.gesture_since))
        self.gesture_pending=dict(name=name,remaining_s=remaining,confirmed=self.gesture_sent)
        if (name=='stop' or remaining==0) and not self.gesture_sent:
            self.gesture_sent=True;self.gesture_pending['confirmed']=True;return command
        return None

    def track(self, now):
        s=self.snapshot;age=s.get('age_ms')
        if self.target_id is None:self.lock_state='UNSELECTED';self.reason='Select your person ID';return None
        if now-self.received>.8 or now-self.progress>.8 or not isinstance(age,(float,int)) or not math.isfinite(age) or age+(now-self.received)*1000>700:
            self.lock_state='STALE';self.gesture_pending=None;self.gesture_key=None;self.gesture_since=None;self.candidate=None
            self.reason='Perception stale • hold';return None
        if self.epoch!=s.get('frame',{}).get('epoch'):
            self.lock_state='RESELECT';self.reason='Camera restarted • select target again';return None
        t=next((t for t in s.get('tracks',[]) if t['id']==self.target_id),None)
        if not t or t.get('coasting'):
            t=self.return_match(now)
        if not t or t.get('conf',0)<.5:
            self.lock_state='LOST';self.reason='Target lost • hold; click to confirm any new ID';return None
        frame_seq=s.get('frame',{}).get('seq')
        if frame_seq!=self.last_remembered:
            self.last_remembered=frame_seq;self.last_seen=now
            self.excluded.update(other['id'] for other in s.get('tracks',[]) if other['id']!=t['id'] and not other.get('coasting'))
            profile=t.get('appearance')
            if similarity(profile,profile)>.99 and (not self.profiles or similarity(self.profiles[0],profile)>.88):
                self.profiles.append(profile)
                if len(self.profiles)>5:self.profiles.pop(1)
        self.candidate=None
        self.lock_state='LOCKED'
        return t

    def return_match(self,now):
        # Same conservative clothing-appearance criteria as the preserved browser demo.
        # No face identity claim; uncertain returns require an operator click.
        if not self.profiles or now-self.last_seen>30:self.candidate=None;return None
        candidates=[]
        for t in self.snapshot.get('tracks',[]):
            if t['id']==self.target_id or t.get('coasting') or t.get('conf',0)<.5:continue
            candidates.append((max(similarity(p,t.get('appearance')) for p in self.profiles),t))
        candidates.sort(key=lambda p:p[0],reverse=True)
        if not candidates:self.candidate=None;return None
        score,t=candidates[0]
        if score<.9 or t['id'] in self.excluded:self.candidate=None;return None
        if len(candidates)>1 and score-candidates[1][0]<.1:
            self.candidate=dict(id=t['id'],score=score,ambiguous=True);return None
        seq=self.snapshot.get('frame',{}).get('seq')
        if not self.candidate or self.candidate.get('id')!=t['id'] or self.candidate.get('ambiguous'):
            self.candidate=dict(id=t['id'],score=score,since=now,seq=seq,frames=1,ambiguous=False)
        elif self.candidate['seq']!=seq:
            self.candidate.update(seq=seq,frames=self.candidate['frames']+1,score=score)
        if now-self.candidate['since']<.8 or self.candidate['frames']<4:return None
        self.target_id=t['id'];self.reacquired=True;self.angle=None;self.last_velocity=(0.,0.)
        return t

    def stop(self):
        self.last_velocity=(0.,0.);self.command_yaw=None;return (0.,0.,None)

    def velocity(self, position, now, dt, quaternion=None):
        if self.mode=='hold':self.reason='Holding position';return self.stop()
        t=self.track(now)
        if t is None:return self.stop()
        frame=self.snapshot['frame'];w=frame.get('width',0)
        if w<=0:self.reason='Invalid camera dimensions';return (0.,0.,None)
        kind=self.snapshot.get('kind','pi')
        if kind=='replay':self.reason='Replay analysis only • flight disabled';return self.stop()
        if kind=='gazebo':
            distance=t.get('range_m');camera=self.snapshot.get('camera',{})
            if not camera.get('depth_paired') or not isinstance(distance,(float,int)) or not math.isfinite(distance) or not .3<distance<30:
                self.reason='No paired depth • hold';return self.stop()
            if quaternion is None or len(quaternion)!=4 or not all(math.isfinite(v) for v in quaternion):
                self.reason='Attitude unavailable • hold';return self.stop()
            qw,qx,qy,qz=quaternion
            focal=w/(2*math.tan(camera.get('hfov',1.2)/2))
            forward=distance;right=((t['x']+t['w']/2)-w/2)*distance/focal
            down=((t['y']+t['h']*.425)-frame['height']/2)*distance/focal
            pitch=camera.get('pitch',.12)
            bx=math.cos(pitch)*forward-math.sin(pitch)*down+.15
            by=right;bz=math.sin(pitch)*forward+math.cos(pitch)*down-.18
            north=position[0]+(1-2*(qy*qy+qz*qz))*bx+2*(qx*qy-qw*qz)*by+2*(qx*qz+qw*qy)*bz
            east=position[1]+2*(qx*qy+qw*qz)*bx+(1-2*(qx*qx+qz*qz))*by+2*(qy*qz-qw*qx)*bz
        else:
            # Booth camera is explicitly schematic; it is not onboard localization.
            height=frame.get('height',720)
            if height<=0 or t.get('h',0)<=0:self.reason='Invalid person size • hold';return self.stop()
            # Port of Garuda.project from the preserved demo: box size drives range,
            # horizontal centre drives bearing. This remains explicitly schematic.
            depth=max(1.8,min(7.,5.5-t['h']/height*4))
            north=self.home[0]+depth-.8
            east=self.home[1]+((t['x']+t['w']/2)/w-.5)*7
        self.target=(north,east)
        radius=min(self.gap,max(0.,self.limit-math.hypot(north-self.home[0],east-self.home[1])-.2))
        if kind=='gazebo':
            dn,de=position[0]-north,position[1]-east;distance=max(.1,math.hypot(dn,de))
            radial=(self.gap-distance)*.8
            tangent=self.direction*self.speed*.65 if self.mode=='circle' else 0.
            if self.mode=='circle' and radius<self.gap-.1:
                self.reason='Orbit would cross travel boundary • hold';return self.stop()
            vn=radial*dn/distance-tangent*de/distance
            ve=radial*de/distance+tangent*dn/distance
            self.reason='Following onboard camera target' if self.mode=='follow' else 'Orbiting onboard camera target'
            return self.bounded(vn,ve,position,math.atan2(east-position[1],north-position[0]),dt,quaternion)
        if self.mode=='circle':
            if radius<.5:self.reason='Target too close to travel boundary';return (0.,0.,None)
            if self.angle is None:self.angle=math.atan2(position[1]-east,position[0]-north)
            goal=(north+radius*math.cos(self.angle),east+radius*math.sin(self.angle))
            if math.hypot(goal[0]-position[0],goal[1]-position[1])<.5:self.angle+=self.direction*min(.5,self.speed*.65/radius)*dt
            self.reason='Orbiting virtual target from live booth tracking'
        else:
            # Standoff lies on the ray from the booth camera to the person, matching
            # the original illustration instead of an arbitrary constant north offset.
            cn,ce=self.home[0]-.8,self.home[1];dn,de=north-cn,east-ce;distance=max(.01,math.hypot(dn,de))
            offset=min(self.gap,max(0.,distance-.8))
            goal=(north-offset*dn/distance,east-offset*de/distance)
            self.reason='Following live camera target'+(' • appearance return matched' if self.reacquired else '')
        vn,ve=(goal[0]-position[0])*1.4,(goal[1]-position[1])*1.4
        return self.bounded(vn,ve,position,math.atan2(east-position[1],north-position[0]),dt,quaternion)

    def bounded(self,vn,ve,position,yaw,dt,quaternion):
        speed=math.hypot(vn,ve)
        if speed>self.speed:vn*=self.speed/speed;ve*=self.speed/speed
        pn,pe=position[0]-self.home[0],position[1]-self.home[1]
        dist=math.hypot(pn,pe)
        if dist>=self.limit-.25 and vn*pn+ve*pe>0:
            self.reason='Home travel boundary • hold';return self.stop()
        oldn,olde=self.last_velocity;delta=math.hypot(vn-oldn,ve-olde)
        cap=self.acceleration*min(.1,max(0.,dt))
        if delta>cap:vn=oldn+(vn-oldn)*cap/delta;ve=olde+(ve-olde)*cap/delta
        self.last_velocity=(vn,ve)
        if quaternion:
            w,x,y,z=quaternion;heading=math.atan2(2*(w*z+x*y),1-2*(y*y+z*z))
            if self.command_yaw is None:self.command_yaw=heading
            error=(yaw-self.command_yaw+math.pi)%(2*math.pi)-math.pi
            self.command_yaw+=max(-.6*dt,min(.6*dt,error))
            yaw=self.command_yaw
        return vn,ve,yaw
