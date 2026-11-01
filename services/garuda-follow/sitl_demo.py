#!/usr/bin/env python3
"""PC-only ROS 2/PX4 demo, reading the working Pi service without mutating it."""
import argparse
import json
import math
import os
from pathlib import Path
import queue
import threading
import time
import urllib.request
import urllib.error
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from sitl_demo_core import DemoControl

ROOT=Path(__file__).resolve().parents[2]

def main():
    if os.environ.get('GARUDA_SIM')!='1' or os.environ.get('ROS_DOMAIN_ID')!='42':
        raise SystemExit('Refused: requires GARUDA_SIM=1 and isolated ROS_DOMAIN_ID=42')
    import rclpy
    from rclpy.node import Node
    from rclpy.executors import ExternalShutdownException
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from px4_msgs.msg import (OffboardControlMode,TrajectorySetpoint,VehicleCommand,
        VehicleCommandAck,VehicleOdometry,VehicleStatus,VehicleLandDetected)
    p=argparse.ArgumentParser();p.add_argument('--source',default='http://10.42.0.128:8098')
    p.add_argument('--port',type=int,default=8099);args=p.parse_args()
    commands=queue.Queue(maxsize=50);observations=queue.Queue(maxsize=1)
    published={};running=threading.Event();running.set()
    source={'kind':'pi','url':args.source,'generation':0};events=deque(maxlen=30)

    def read_source():
        while running.is_set():
            try:
                selected=dict(source);started=time.monotonic()
                with urllib.request.urlopen(selected['url']+'/api/state',timeout=1) as r:data=json.load(r)
                if selected['kind']=='pi':
                    data['tracks']=[t for t in data.get('tracks',[]) if t.get('label')=='person']
                    data['metrics']={'processing_fps':data.get('stats',{}).get('fps')}
                age=data.get('age_ms')
                if isinstance(age,(float,int)):data['age_ms']=age+(time.monotonic()-started)*1000
                data['kind']=selected['kind']
                if observations.full():observations.get_nowait()
                observations.put_nowait((selected['generation'],time.monotonic(),data))
            except Exception:pass  # stale watchdog in core does not depend on transport errors
            time.sleep(.2)
    threading.Thread(target=read_source,daemon=True).start()

    class Demo(Node):
        def __init__(self):
            super().__init__('garuda_isolated_demo')
            self.core=DemoControl();self.pose=None;self.pose_at=-math.inf
            self.status=None;self.status_at=-math.inf;self.landed=True
            self.phase='GROUND';self.since=time.monotonic();self.last_cmd=-math.inf
            self.ack=None;self.error='';self.hold_alt=1.5;self.start_z=None;self.target_z=None
            self.hold_xy=None;self.intent=(0.,0.,None)
            qos=QoSProfile(depth=5,reliability=ReliabilityPolicy.BEST_EFFORT,
                           durability=DurabilityPolicy.VOLATILE)
            def topic(direction,name,cls):
                version=getattr(cls,'MESSAGE_VERSION',0)
                return '/garuda_sitl/fmu/'+direction+'/'+name+(f'_v{version}' if version else '')
            self.mode_pub=self.create_publisher(OffboardControlMode,topic('in','offboard_control_mode',OffboardControlMode),qos)
            self.sp_pub=self.create_publisher(TrajectorySetpoint,topic('in','trajectory_setpoint',TrajectorySetpoint),qos)
            self.cmd_pub=self.create_publisher(VehicleCommand,topic('in','vehicle_command',VehicleCommand),qos)
            self.create_subscription(VehicleOdometry,topic('out','vehicle_odometry',VehicleOdometry),self.on_pose,qos)
            self.create_subscription(VehicleStatus,topic('out','vehicle_status',VehicleStatus),self.on_status,qos)
            self.create_subscription(VehicleLandDetected,topic('out','vehicle_land_detected',VehicleLandDetected),self.on_land,qos)
            self.create_subscription(VehicleCommandAck,topic('out','vehicle_command_ack',VehicleCommandAck),self.on_ack,qos)
            self.create_timer(.05,self.tick)

        def on_pose(self,msg):
            if all(math.isfinite(float(x)) for x in msg.position):self.pose=msg;self.pose_at=time.monotonic()
        def on_status(self,msg):self.status=msg;self.status_at=time.monotonic()
        def on_land(self,msg):self.landed=msg.landed
        def on_ack(self,msg):
            if msg.target_system==250:
                self.ack={'command':int(msg.command),'result':int(msg.result)}
        def fresh(self,now):return self.pose is not None and self.status is not None and now-self.pose_at<.5 and now-self.status_at<2
        def armed(self):return self.status is not None and self.status.arming_state==VehicleStatus.ARMING_STATE_ARMED
        def send(self,command,p1=0.,p2=0.):
            m=VehicleCommand();m.timestamp=int(self.get_clock().now().nanoseconds/1000)
            m.command=command;m.param1=float(p1);m.param2=float(p2)
            m.target_system=4;m.target_component=1;m.source_system=250;m.source_component=1;m.from_external=True
            self.cmd_pub.publish(m)
        def action(self,a,now):
            command=a.get('command')
            events.append(dict(time=time.strftime('%H:%M:%S'),command=command))
            if command=='source':
                if self.armed() or self.phase not in ('GROUND','FAULT'):raise ValueError('Land before changing camera source')
                kind=a.get('kind')
                if kind not in ('pi','gazebo','replay'):raise ValueError('Unknown camera source')
                if kind!='pi':
                    config=dict(mode=kind)
                    if kind=='replay':config['clip']=a.get('clip')
                    req=urllib.request.Request('http://127.0.0.1:8101/api/config',data=json.dumps(config).encode(),headers={'Content-Type':'application/json'})
                    try:
                        with urllib.request.urlopen(req,timeout=1):pass
                    except Exception as exc:raise ValueError('Vision source unavailable: '+str(exc))
                else:
                    try:
                        req=urllib.request.Request('http://127.0.0.1:8101/api/config',data=b'{"paused":true}',headers={'Content-Type':'application/json'})
                        with urllib.request.urlopen(req,timeout=.5):pass
                    except Exception:pass  # Pi view remains usable without the optional PC detector.
                source.update(kind=kind,url=args.source if kind=='pi' else 'http://127.0.0.1:8101',generation=source['generation']+1)
                self.core.reset();self.error='';return
            if command in ('replay','benchmark'):
                if source['kind']!='replay' or self.armed():raise ValueError('Replay controls require replay mode and disarmed vehicle')
                config={k:a[k] for k in ('paused','rate') if k in a}
                if command=='benchmark':config={'benchmark':a.get('clip')}
                req=urllib.request.Request('http://127.0.0.1:8101/api/config',data=json.dumps(config).encode(),headers={'Content-Type':'application/json'})
                try:
                    with urllib.request.urlopen(req,timeout=1):pass
                except Exception as exc:raise ValueError(str(exc))
                return
            if command=='select':
                tid=int(a['id']);s=self.core.snapshot
                if not any(t['id']==tid and not t.get('coasting') for t in s.get('tracks',[])):raise ValueError('Target no longer visible')
                if a.get('epoch',s.get('frame',{}).get('epoch'))!=s.get('frame',{}).get('epoch'):raise ValueError('Image source changed; select again')
                if tid==self.core.target_id and self.core.epoch==s.get('frame',{}).get('epoch'):return
                self.core.select(tid);self.core.track(now);return
            if command=='release':self.core.reset();return
            if command=='gestures':self.core.gestures=bool(a.get('enabled'));self.core.gesture_key=None;return
            if command=='settings':
                for name,lo,hi in [('gap',.5,5),('speed',.2,2),('limit',3,15)]:
                    if name in a:
                        v=float(a[name])
                        if not math.isfinite(v):raise ValueError('Nonfinite setting')
                        setattr(self.core,name,max(lo,min(hi,v)))
                if 'direction' in a:self.core.direction=1 if a['direction']=='cw' else -1
                return
            if command=='land':
                if self.armed():self.phase='LANDING';self.last_cmd=-math.inf;self.core.mode='hold'
                return
            if command in ('takeoff','takeoff_follow'):
                if source['kind']=='replay':raise ValueError('Recorded footage is analysis only; takeoff disabled')
                if self.phase=='LANDING':return
                if command=='takeoff' and self.armed():return
                if not self.fresh(now):self.error='PX4 telemetry unavailable';return
                if not self.armed():
                    self.start_z=float(self.pose.position[2]);self.target_z=self.start_z-self.hold_alt
                    self.core.home=tuple(map(float,self.pose.position[:2]))
                    self.phase='PRESTREAM';self.since=now;self.last_cmd=-math.inf;self.hold_xy=tuple(map(float,self.pose.position[:2]))
                self.core.mode='follow' if command=='takeoff_follow' else 'hold';self.error='';return
            if command in ('hold','follow','circle'):
                self.core.mode=command;self.core.angle=None;self.hold_xy=None
        def tick(self):
            nonlocal published
            now=time.monotonic()
            self.intent=(0.,0.,None)
            if not observations.empty():
                generation,received,data=observations.get_nowait()
                if generation==source['generation']:
                    if isinstance(data.get('age_ms'),(int,float)):data['age_ms']+=(now-received)*1000
                    gesture=self.core.receive(data,now)
                    if gesture:self.action({'command':gesture},now)
            while not commands.empty():
                try:self.action(commands.get_nowait(),now)
                except (KeyError,ValueError,TypeError) as e:self.error=str(e)
            fresh=self.fresh(now);armed=self.armed();nav=int(self.status.nav_state) if self.status else None
            if self.phase=='LANDING':
                if not armed and self.landed:self.phase='GROUND'
                elif fresh and now-self.last_cmd>1:
                    self.send(VehicleCommand.VEHICLE_CMD_NAV_LAND);self.last_cmd=now
            active=self.phase in ('PRESTREAM','REQUESTING','FLYING')
            if active and not fresh:
                # Stop offboard heartbeat: PX4's configured offboard-loss action takes over.
                self.error='PX4 telemetry stale • offboard heartbeat stopped'
                self.phase='FAULT'
            elif active:
                if self.phase=='PRESTREAM' and now-self.since>=2:self.phase='REQUESTING'
                if self.phase=='REQUESTING':
                    if armed and nav==VehicleStatus.NAVIGATION_STATE_OFFBOARD:self.phase='FLYING'
                    elif now-self.since>12:self.phase='FAULT';self.error='Offboard/arm not confirmed; inspect PX4 acknowledgement'
                    elif now-self.last_cmd>1:
                        self.send(VehicleCommand.VEHICLE_CMD_DO_SET_MODE,1,6)
                        self.send(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM,1)
                        self.last_cmd=now
                if self.phase=='FLYING' and (not armed or nav!=VehicleStatus.NAVIGATION_STATE_OFFBOARD):
                    self.phase='GROUND' if not armed else 'FAULT';self.error='PX4 left offboard'
                if self.phase in ('PRESTREAM','REQUESTING','FLYING'):
                    mode=OffboardControlMode();mode.timestamp=int(self.get_clock().now().nanoseconds/1000);mode.position=True;mode.velocity=True
                    sp=TrajectorySetpoint();sp.timestamp=mode.timestamp
                    vn,ve,yaw=(0.,0.,None)
                    if self.phase=='FLYING' and abs(float(self.pose.position[2])-self.target_z)<.3:
                        vn,ve,yaw=self.core.velocity(list(map(float,self.pose.position)),now,.05,list(map(float,self.pose.q)))
                    self.intent=(vn,ve,yaw)
                    if vn==0 and ve==0:
                        if self.hold_xy is None:self.hold_xy=tuple(map(float,self.pose.position[:2]))
                        sp.position=[*self.hold_xy,self.target_z];sp.velocity=[math.nan]*3
                    else:
                        self.hold_xy=None;sp.position=[math.nan,math.nan,self.target_z];sp.velocity=[vn,ve,math.nan]
                    sp.acceleration=[math.nan]*3;sp.jerk=[math.nan]*3;sp.yaw=yaw if yaw is not None else math.nan;sp.yawspeed=0.
                    self.mode_pub.publish(mode);self.sp_pub.publish(sp)
            tracks=self.core.snapshot.get('tracks',[])
            self.core.track(now)
            published={'phase':self.phase,'armed':armed,'nav_state':nav,'landed':self.landed,
                'position':list(map(float,self.pose.position)) if self.pose else None,'telemetry_fresh':fresh,
                'intent':self.intent,
                'altitude':self.start_z-float(self.pose.position[2]) if self.pose and self.start_z is not None else 0,
                'target_id':self.core.target_id,'target':self.core.target,'behavior':self.core.mode,
                'reason':self.core.reason,'error':self.error,'ack':self.ack,'gestures':self.core.gestures,
                'tracks':tracks,'frame':self.core.snapshot.get('frame',{}),'lock_state':self.core.lock_state,
                'gesture_pending':self.core.gesture_pending,'events':list(events),
                'return_candidate':self.core.candidate,'reacquired':self.core.reacquired,
                'metrics':self.core.snapshot.get('metrics',{}),'camera':self.core.snapshot.get('camera',{}),
                'vision_error':self.core.snapshot.get('error',''),'rtf':self.core.snapshot.get('rtf'),
                'replay':self.core.snapshot.get('replay'),'paused':self.core.snapshot.get('paused',False),
                'benchmark':self.core.snapshot.get('benchmark','idle'),
                'detector':self.core.snapshot.get('detector',{}),'perception_age_ms':self.core.snapshot.get('age_ms',0)+(now-self.core.received)*1000 if isinstance(self.core.snapshot.get('age_ms'),(int,float)) else None,
                'source':source['url'],'source_kind':source['kind'],'gap':self.core.gap,'speed':self.core.speed,'limit':self.core.limit,
                'home':self.core.home,'direction':'cw' if self.core.direction==1 else 'ccw'}

    class HTTP(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def respond(self,code,body,ctype='application/json'):
            self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path=='/api/state':self.respond(200,json.dumps(published,allow_nan=False).encode())
            elif self.path=='/':self.respond(200,(ROOT/'hosts/pc/demo.html').read_bytes(),'text/html; charset=utf-8')
            elif self.path.startswith('/vision/'):
                path=self.path[len('/vision'):]
                if not (path.startswith('/frame.jpg?') or path in ('/overview.jpg','/api/clips','/api/report')):self.respond(404,b'{}');return
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8101'+path,timeout=2) as r:
                        self.respond(200,r.read(3*1024*1024),r.headers.get('Content-Type','application/json'))
                except urllib.error.HTTPError as exc:self.respond(exc.code,b'{}')
                except Exception:self.respond(503,b'{}')
            else:self.respond(404,b'{}')
        def do_POST(self):
            # Local UI only; JSON plus origin check prevents cross-origin form commands.
            if self.path!='/api/command' or self.headers.get('Content-Type')!='application/json':self.respond(400,b'{}');return
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{args.port}',f'http://localhost:{args.port}'):
                self.respond(403,b'{}');return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<2048:raise ValueError('Invalid body size')
                data=json.loads(self.rfile.read(n))
                if not isinstance(data,dict):raise ValueError('Expected object')
                commands.put_nowait(data);self.respond(202,b'{"queued":true}')
            except (ValueError,queue.Full):self.respond(400,b'{}')

    server=ThreadingHTTPServer(('127.0.0.1',args.port),HTTP)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    rclpy.init();node=Demo();print(f'Isolated SITL dashboard http://127.0.0.1:{args.port}',flush=True)
    try:rclpy.spin(node)
    except (KeyboardInterrupt,ExternalShutdownException):pass
    finally:running.clear();node.destroy_node();rclpy.try_shutdown();server.shutdown()

if __name__=='__main__':main()
