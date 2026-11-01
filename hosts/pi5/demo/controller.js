/* Deterministic browser-side illustration, not an aircraft flight controller. */
(function(root){
'use strict';
const finite=Number.isFinite;
function similarity(a,b){
  if(!Array.isArray(a)||!Array.isArray(b)||a.length!==128||b.length!==128)return 0;
  let dot=0,aa=0,bb=0;for(let i=0;i<a.length;i++){if(!finite(a[i])||!finite(b[i]))return 0;dot+=a[i]*b[i];aa+=a[i]*a[i];bb+=b[i]*b[i];}
  return aa>0&&bb>0?dot/Math.sqrt(aa*bb):0;
}
class ReturnMatcher {
  constructor(){this.profiles=[];this.lastSeen=-Infinity;this.lastSeq=null;this.candidate=null;this.excluded=new Set();this.reason='';this.faceRequired=false;this.faceProfiles=[];}
  remember(t,s,now){
    if(s.frame.seq===this.lastSeq)return;this.lastSeq=s.frame.seq;this.lastSeen=now;this.candidate=null;
    for(const other of s.tracks)if(other.id!==t.id&&other.label==='person'&&!other.coasting)this.excluded.add(other.id);
    if(similarity(t.appearance,t.appearance)<.99)return;
    // Anchor + a few nearby lighting/view variants, never train on an unrelated jump.
    if(!this.profiles.length)this.profiles.push(t.appearance);
    else if(similarity(this.profiles[0],t.appearance)>.88){
      this.profiles.push(t.appearance);if(this.profiles.length>5)this.profiles.splice(1,1);
    }
  }
  find(s,now,oldId){
    this.reason='Target out of view • waiting for return';
    if(now-this.lastSeen>30000){this.candidate=null;this.reason='Return window expired • select again';return null;}
    const profiles=this.faceRequired?this.faceProfiles:this.profiles;
    if(!profiles.length){this.candidate=null;this.reason=this.faceRequired?'Enroll the selected face before leaving view':'No appearance reference • select again';return null;}
    const candidates=s.tracks.filter(t=>t.label==='person'&&!t.coasting&&t.conf>=.5&&t.id!==oldId)
      .filter(t=>!this.faceRequired||(t.face&&t.face.age_ms<1800))
      .map(t=>({t,score:Math.max(...profiles.map(p=>similarity(p,this.faceRequired?t.face.embedding:t.appearance)))})).sort((a,b)=>b.score-a.score);
    const top=candidates[0];
    if(!top||top.score<(this.faceRequired ? .50 : .90)||this.excluded.has(top.t.id)){this.candidate=null;if(this.faceRequired)this.reason='Waiting for the enrolled face • look toward camera';return null;}
    if(candidates[1]&&top.score-candidates[1].score<(this.faceRequired ? .08 : .10)){this.candidate=null;this.reason='Similar-looking people • waiting for a clear match';return null;}
    this.reason=this.faceRequired?'Returning face candidate • confirming match':'Returning target candidate • checking appearance';
    const sample=this.faceRequired?top.t.face.sample:s.frame.seq;
    if(this.candidate?.id!==top.t.id)this.candidate={id:top.t.id,since:now,seq:sample,frames:1};
    else if(this.candidate.seq!==sample){this.candidate.seq=sample;this.candidate.frames++;}
    if(now-this.candidate.since<800||this.candidate.frames<(this.faceRequired?2:4))return null;
    return top.t;
  }
}
class Flight {
  airState='GROUNDED';altitude=0;flightStyle='follow';orbitAngle=null;
  takeoff(){if(this.airState!=='GROUNDED')return false;this.airState='TAKING OFF';this.paused=false;this.goodSince=null;return true;}
  land(){if(this.airState==='GROUNDED')return;this.airState='LANDING';this.goodSince=null;this.orbitAngle=null;}
  setFlightStyle(style){if(!['follow','circle'].includes(style))return;this.flightStyle=style;this.orbitAngle=null;this.goodSince=null;}
  constructor(){this.x=0;this.y=0.8;this.vx=0;this.vy=0;this.heading=0;this.mode='IDLE';this.reason='Choose a person to begin';this.id=null;this.epoch=null;this.received=-Infinity;this.progress=-Infinity;this.seq=null;this.goodSince=null;this.lastGoodSeq=null;this.fault=false;this.paused=false;this.requiresSelection=false;this.settings={gap:2,radius:6,speed:2.2};this.side=0;this.gesturesEnabled=false;this.gestureKey='';this.faceEnabled=false;this.enrolling=false;this.faceSamples=[];this.faceMessage='Face matching off';}
  configure(settings){for(const [key,min,max]of[['gap',.5,5],['radius',1,12],['speed',.25,3]]){const v=Number(settings[key]);if(finite(v))this.settings[key]=Math.max(min,Math.min(max,v));}}
  setFaceMode(enabled){this.faceEnabled=enabled;this.enrolling=false;this.faceSamples=[];if(this.matcher){this.matcher.faceRequired=enabled;this.matcher.faceProfiles=[];}this.faceMessage=enabled?'Select a person, then enroll their face':'Face matching off';}
  enroll(){if(this.id===null||!this.faceEnabled)return false;this.enrolling=true;this.faceSamples=[];this.enrollSeen=new Set();this.faceMessage='Look toward camera • collecting three face samples';return true;}
  receive(s,now){this.s=s;this.received=now;const seq=s.frame?.seq;if(seq!==this.seq){this.seq=seq;this.progress=now;this.processGestures(s,now);this.processEnrollment(s);}}
  processEnrollment(s){
    if(!this.enrolling)return;
    if(this.epoch!==s.frame.epoch){this.enrolling=false;this.faceMessage='Camera changed • select and enroll again';return;}
    const t=s.tracks.find(t=>t.id===this.id&&!t.coasting),face=t?.face;
    if(!face||face.age_ms>=1800||s.age_ms>700||similarity(face.embedding,face.embedding)<.99)return;
    if(this.enrollSeen.has(face.sample))return;this.enrollSeen.add(face.sample);
    if(this.faceSamples.length&&similarity(this.faceSamples[0],face.embedding)<.5){this.faceSamples=[];this.faceMessage='Face samples inconsistent • keep facing camera';return;}
    this.faceSamples.push(face.embedding);this.faceMessage='Enrolling face '+this.faceSamples.length+'/3';
    if(this.faceSamples.length===3){this.matcher.faceProfiles=[...this.faceSamples];this.enrolling=false;this.faceMessage='Face enrolled • session only';}
  }
  processGestures(s,now){
    if(!this.gesturesEnabled||!finite(s.age_ms)||s.age_ms>700)return;
    if(this.id!==null&&this.epoch!==s.frame.epoch)return;
    let t=s.tracks.find(t=>t.id===this.id&&!t.coasting&&t.conf>=.5);
    if(this.id===null){const raised=s.tracks.filter(t=>!t.coasting&&t.conf>=.5&&t.gesture?.name==='follow_me');if(raised.length===1)t=raised[0];}
    const name=t?.gesture?.name,key=name?t.id+':'+name:'';
    if(key===this.gestureKey)return;this.gestureKey=key;if(!name)return;
    if(name==='follow_me'){if(this.id===null)this.select(t.id,now);if(this.airState==='LANDING')return;this.takeoff();this.setFlightStyle('follow');this.paused=false;this.goodSince=null;}
    else if(name==='stop'&&t.id===this.id)this.paused=true;
    else if(name==='land'&&t.id===this.id)this.land();
    else if(name==='circle'&&t.id===this.id&&this.airState==='AIRBORNE'){this.setFlightStyle('circle');this.paused=false;}
    else if(t.id===this.id&&name==='point_left')this.side=-1;
    else if(t.id===this.id&&name==='point_right')this.side=1;
  }
  select(id,now){this.id=id;this.epoch=this.s?.frame?.epoch;this.goodSince=null;this.lastGoodSeq=null;this.requiresSelection=false;this.paused=false;this.mode='ACQUIRE';this.reason='Confirming fresh observations';this.matcher=new ReturnMatcher();this.matcher.faceRequired=this.faceEnabled;this.enrolling=false;this.faceSamples=[];this.faceMessage=this.faceEnabled?'Enroll selected face before leaving view':'Face matching off';this.reacquired=false;const t=this.s?.tracks.find(t=>t.id===id);if(t)this.matcher.remember(t,this.s,now);}
  reset(){this.id=null;this.mode='IDLE';this.reason='Choose a person to begin';this.goodSince=null;this.fault=false;this.paused=false;this.enrolling=false;this.faceSamples=[];if(this.matcher)this.matcher.faceProfiles=[];this.faceMessage=this.faceEnabled?'Select a person, then enroll their face':'Face matching off';}
  observation(now,project){
    if(this.id===null)return {reason:'Choose a person to begin'};
    if(this.paused)return {reason:'Paused by operator'};
    if(this.fault)return {reason:'Signal-loss test • simulated interruption'};
    const s=this.s;
    if(!s||now-this.received>800)return {reason:'Connection lost'};
    if(!finite(s.age_ms)||s.age_ms>700||now-this.progress>800)return {reason:'Camera observations stale'};
    if(this.epoch!==s.frame?.epoch){this.requiresSelection=true;return {reason:'Camera or tracker changed • select again'};}
    if(this.requiresSelection)return {reason:'Target identity uncertain • select again'};
    let t=s.tracks?.find(t=>t.id===this.id);
    if(!t||t.coasting){
      const match=this.matcher?.find(s,now,this.id);
      if(!match)return {reason:this.matcher?.reason||'Target out of view • waiting for return'};
      this.id=match.id;t=match;this.goodSince=null;this.reacquired=true;
    }
    if(!finite(t.conf)||t.conf<0.45)return {reason:'Target confidence low'};
    this.matcher?.remember(t,s,now);
    const p=project(t,s);
    if(!p||!finite(p.x)||!finite(p.y))return {reason:'Position unavailable • show full body in zone'};
    return {point:p,track:t};
  }
  step(dt,now,project){
    dt=Math.max(0,Math.min(dt,0.05));
    const o=this.observation(now,project);let vx=0,vy=0;
    if(this.airState!=='AIRBORNE'){
      this.goodSince=null;
      if(this.airState==='TAKING OFF'){
        this.altitude=Math.min(1.5,this.altitude+.75*dt);this.mode='TAKEOFF';this.reason='Simulated vertical takeoff';
        if(this.altitude>=1.5)this.airState='AIRBORNE';
      }else if(this.airState==='LANDING'){
        this.mode='LAND';this.reason='Braking, then landing at current position';
        if(Math.hypot(this.vx,this.vy)<.03)this.altitude=Math.max(0,this.altitude-.6*dt);
        if(this.altitude===0){this.airState='GROUNDED';this.mode='GROUND';this.reason='Landed • press Take off to fly again';}
      }else{this.mode='GROUND';this.reason='Grounded • select a target and press Take off';}
    }else if(o.point){
      if(this.goodSince===null){this.goodSince=now;this.lastGoodSeq=this.seq;}
      if(now-this.goodSince>=500&&this.seq!==this.lastGoodSeq){
        this.mode='FOLLOW';this.reason=this.reacquired?(this.faceEnabled?'Target reacquired • enrolled face matched':'Target reacquired • clothing appearance matched'):'Fresh observations • selected track retained';
        const p=o.point,off=this.settings.gap;
        const d=Math.hypot(p.x,p.y)||1;
        const angle=Math.atan2(p.x,p.y)-this.side*Math.PI/4;
        const availableOffset=Math.min(off,Math.max(0,d-.8));
        let gx=p.x-availableOffset*Math.sin(angle),gy=p.y-availableOffset*Math.cos(angle);
        if(this.flightStyle==='circle'){
          // Fit a complete orbit, rather than silently blocking a large requested gap.
          const room=this.settings.radius-Math.hypot(p.x,p.y-.8)-.1;
          const radius=Math.min(this.settings.gap,room);this.orbitRadius=Math.max(0,radius);
          if(this.orbitAngle===null)this.orbitAngle=Math.atan2(this.y-p.y,this.x-p.x);
          gx=p.x+radius*Math.cos(this.orbitAngle);gy=p.y+radius*Math.sin(this.orbitAngle);
          if(radius<.5){
            this.mode='HOLD';this.reason='Circle exceeds travel limit • reduce gap or increase max travel';gx=this.x;gy=this.y;
          }else{
            this.mode='CIRCLE';this.reason=radius<this.settings.gap?'Circling target • radius reduced to fit travel limit':'Circling selected target • gap sets orbit radius';
            if(Math.hypot(gx-this.x,gy-this.y)<.6)this.orbitAngle+=Math.min(.6,this.settings.speed*.65/radius)*dt;
          }
        }
        // Bound simulated travel around home (0, 0.8), independent of target range.
        const travel=Math.hypot(gx,gy-.8);if(travel>this.settings.radius){const k=this.settings.radius/travel;gx*=k;gy=.8+(gy-.8)*k;this.reason='Following within your travel limit';}
        vx=(gx-this.x)*1.4;vy=(gy-this.y)*1.4;
        const speed=Math.hypot(vx,vy),max=this.settings.speed;if(speed>max){vx*=max/speed;vy*=max/speed;}
      }else{this.mode='ACQUIRE';this.reason='Confirming stable observations';}
    }else{this.goodSince=null;this.reason=o.reason;this.mode=this.id===null?'IDLE':(Math.hypot(this.vx,this.vy)>0.03?'BRAKE':'HOLD');}
    // Loss always targets zero VELOCITY, never the previous destination.
    const ax=vx-this.vx,ay=vy-this.vy,a=Math.hypot(ax,ay),lim=3.5*dt;
    const k=a>lim?lim/a:1;this.vx+=ax*k;this.vy+=ay*k;
    const wasInside=Math.hypot(this.x,this.y-.8)<=this.settings.radius;
    this.x+=this.vx*dt;this.y+=this.vy*dt;
    const travel=Math.hypot(this.x,this.y-.8);
    if(wasInside&&travel>this.settings.radius){const nx=this.x/travel,ny=(this.y-.8)/travel;this.x=nx*this.settings.radius;this.y=.8+ny*this.settings.radius;const outward=this.vx*nx+this.vy*ny;if(outward>0){this.vx-=outward*nx;this.vy-=outward*ny;}}
    const speed=Math.hypot(this.vx,this.vy);
    if(speed>0.03){let d=Math.atan2(this.vx,this.vy)-this.heading;d=Math.atan2(Math.sin(d),Math.cos(d));this.heading+=Math.max(-2*dt,Math.min(2*dt,d));}
    return {...o,speed,mode:this.mode,reason:this.reason};
  }
}
function distance(s,id){
  if(s?.lock?.id!==id||!s.distance?.valid)return null;
  const r=s.distance.est_m??s.distance.raw_m;
  return finite(r)&&r>0&&r<=30?r:null;
}
function solve(A,b){
  const n=b.length;A=A.map((r,i)=>[...r,b[i]]);
  for(let i=0;i<n;i++){let p=i;for(let j=i+1;j<n;j++)if(Math.abs(A[j][i])>Math.abs(A[p][i]))p=j;
    [A[i],A[p]]=[A[p],A[i]];if(Math.abs(A[i][i])<1e-10)throw Error('Points are degenerate');
    const d=A[i][i];for(let k=i;k<=n;k++)A[i][k]/=d;
    for(let j=0;j<n;j++)if(j!==i){const f=A[j][i];for(let k=i;k<=n;k++)A[j][k]-=f*A[i][k];}}
  return A.map(r=>r[n]);
}
function homography(points,width,depth){
  if(points.length!==4||!finite(width)||!finite(depth)||width<=0||depth<=0)throw Error('Use four floor corners and positive dimensions');
  // Convex, non-crossed polygon: near-left, near-right, far-right, far-left.
  const cross=points.map((p,i)=>{const q=points[(i+1)%4],r=points[(i+2)%4];return(q.x-p.x)*(r.y-q.y)-(q.y-p.y)*(r.x-q.x);});
  if(cross.some(v=>Math.abs(v)<0.00001)||!cross.every(v=>Math.sign(v)===Math.sign(cross[0])))throw Error('Click the four corners in order');
  const world=[[-width/2,1],[width/2,1],[width/2,1+depth],[-width/2,1+depth]],A=[],b=[];
  points.forEach((p,i)=>{const [x,y]=world[i];A.push([p.x,p.y,1,0,0,0,-x*p.x,-x*p.y]);b.push(x);A.push([0,0,0,p.x,p.y,1,-y*p.x,-y*p.y]);b.push(y);});
  return {h:solve(A,b),points,width,depth};
}
function inside(p,poly){const signs=poly.map((a,i)=>{const b=poly[(i+1)%poly.length];return(b.x-a.x)*(p.y-a.y)-(b.y-a.y)*(p.x-a.x);});return signs.every(x=>x>=-1e-6)||signs.every(x=>x<=1e-6);}
function project(t,s,cal){
  const w=s.frame.width,h=s.frame.height;
  if(!(w>0&&h>0&&t.w>0&&t.h>0))return null;
  if(cal){
    const p={x:(t.x+t.w/2)/w,y:(t.y+t.h)/h};
    if(t.y+t.h>h*0.98||!inside(p,cal.points))return null;
    const a=cal.h,d=a[6]*p.x+a[7]*p.y+1;if(Math.abs(d)<1e-8)return null;
    return {x:(a[0]*p.x+a[1]*p.y+a[2])/d,y:(a[3]*p.x+a[4]*p.y+a[5])/d,metric:true};
  }
  // Deliberately schematic: image position and box size, no pretend metric depth.
  return {x:((t.x+t.w/2)/w-0.5)*7,y:Math.max(1.8,Math.min(7,5.5-t.h/h*4)),metric:false};
}
const api={Flight,ReturnMatcher,similarity,distance,homography,project,inside};
if(typeof module!=='undefined')module.exports=api;root.Garuda=api;
})(typeof globalThis!=='undefined'?globalThis:this);
