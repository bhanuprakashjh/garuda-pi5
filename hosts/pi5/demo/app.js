'use strict';
const $=id=>document.getElementById(id),F=new Garuda.Flight();
let S=null,cal=null,savedCal=null,calPoints=null,calSignature=null,previousMode='',lastPaint=0,lastTrail=0;
let trail=[],targetTrail=[],events=[],reduced=matchMedia('(prefers-reduced-motion:reduce)').matches,hadHold=false;
let lastPeople='',gx=0.5,gy=0.5,gh=1,videoError=false,syncedId=null;
const signature=s=>[s.frame.source,s.frame.width,s.frame.height].join(':');
try{savedCal=JSON.parse(localStorage.getItem('garuda-floor-v1'));if(savedCal)$('calConfirm').hidden=false;}catch(e){}
$('motionToggle').checked=reduced;
$('motionToggle').onchange=e=>reduced=e.target.checked;
const es=new EventSource('/api/events');
es.onmessage=e=>{try{const next=JSON.parse(e.data);if(!next.frame||!Array.isArray(next.tracks))return;
  S=next;F.receive(S,performance.now());
  if(cal&&(calSignature!==signature(S)||cal.cameraEpoch!==S.frame.epoch)){const id=F.id;clearCalibration();F.id=id;F.requiresSelection=true;$('calStatus').textContent='Camera or tracker restarted. Mark the floor corners again.';F.paused=true;}
}catch(err){console.warn('Invalid perception update',err);}};
es.onerror=()=>{$('connection').textContent='RECONNECTING';};
$('feed').onerror=()=>{videoError=true;setTimeout(()=>{$('feed').src='/stream?clean=1&retry='+Date.now();},2500);};
$('feed').onload=()=>videoError=false;
function select(id){
  if(!S)return;const t=S.tracks.find(t=>t.id===id&&!t.coasting&&t.label==='person');if(!t)return;
  F.select(id,performance.now());trail=[];targetTrail=[];hadHold=false;
  // Backend selection supplies the optional range estimate; local identity stays pinned.
  syncTarget(t);
}
function syncTarget(t){syncedId=t.id;fetch('/api/mono_click?x='+(t.x+t.w/2)+'&y='+(t.y+t.h/2),{method:'POST'}).catch(()=>{});}
function clearCalibration(){cal=null;calSignature=null;savedCal=null;calPoints=null;localStorage.removeItem('garuda-floor-v1');$('calConfirm').hidden=true;$('calStatus').textContent='Schematic view. No metric position claim.';F.reset();trail=[];targetTrail=[];}
$('reset').onclick=()=>{F.reset();hadHold=false;trail=[];targetTrail=[];};
$('pause').onclick=()=>{F.paused=!F.paused;F.goodSince=null;};
$('fault').onclick=()=>{if(F.id===null){$('story').textContent='Select a person first, then test signal loss.';return;}F.fault=true;F.goodSince=null;};
$('resume').onclick=()=>{F.fault=false;F.goodSince=null;};
$('fullscreen').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen().catch(()=>{});};
$('calClear').onclick=clearCalibration;
$('calStart').onclick=()=>{if(!S)return;F.reset();calPoints=[];$('calStatus').textContent='Click NEAR-LEFT floor corner in the live image.';};
$('calConfirm').onclick=()=>{if(!S||!savedCal)return;if(savedCal.signature!==signature(S)){clearCalibration();$('calStatus').textContent='Saved camera differs. Mark four corners again.';return;}
  try{cal=Garuda.homography(savedCal.points,savedCal.width,savedCal.depth);cal.cameraEpoch=S.frame.epoch;calSignature=signature(S);$('calStatus').textContent='Saved floor calibration confirmed for this camera position.';$('calConfirm').hidden=true;F.reset();}catch(e){clearCalibration();}};
function fitImage(){const r=$('overlay').getBoundingClientRect(),fw=S?.frame.width||1280,fh=S?.frame.height||720,k=Math.min(r.width/fw,r.height/fh);return{r,k,ox:(r.width-fw*k)/2,oy:(r.height-fh*k)/2,fw,fh};}
$('overlay').onclick=e=>{if(!S)return;const a=fitImage(),x=(e.clientX-a.r.left-a.ox)/a.k,y=(e.clientY-a.r.top-a.oy)/a.k;if(x<0||y<0||x>a.fw||y>a.fh)return;
  if(calPoints){calPoints.push({x:x/a.fw,y:y/a.fh});const names=['NEAR-LEFT','NEAR-RIGHT','FAR-RIGHT','FAR-LEFT'];
    if(calPoints.length<4){$('calStatus').textContent='Click '+names[calPoints.length]+' floor corner.';return;}
    try{const width=Number($('calWidth').value),depth=Number($('calDepth').value);if(width<0.5||width>12||depth<0.5||depth>12)throw Error('Dimensions must be 0.5–12 m');
      cal=Garuda.homography(calPoints,width,depth);cal.cameraEpoch=S.frame.epoch;calSignature=signature(S);savedCal={points:calPoints,width,depth,signature:calSignature};localStorage.setItem('garuda-floor-v1',JSON.stringify(savedCal));$('calStatus').textContent='Floor mapping active. Verify with a person at each marked corner. Only visible feet inside the zone are accepted.';
    }catch(err){cal=null;$('calStatus').textContent=err.message+' — please retry.';}calPoints=null;return;}
  const candidates=S.tracks.filter(t=>t.label==='person'&&!t.coasting&&x>=t.x&&x<=t.x+t.w&&y>=t.y&&y<=t.y+t.h).sort((a,b)=>a.w*a.h-b.w*b.h);
  if(candidates.length)select(candidates[0].id);
};
function canvas(id){const c=$(id),r=c.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,2),w=Math.round(r.width*d),h=Math.round(r.height*d);if(c.width!==w||c.height!==h){c.width=w;c.height=h;}const g=c.getContext('2d');g.setTransform(d,0,0,d,0,0);g.clearRect(0,0,r.width,r.height);return {g,w:r.width,h:r.height};}
function updateUI(o,now){
  const age=S&&Number.isFinite(S.age_ms)?Math.round(S.age_ms+now-F.received):null;
  const fresh=age!==null&&age<700&&now-F.progress<800&&now-F.received<800;
  $('connection').textContent=fresh?'PERCEPTION LIVE':'WAITING FOR FRESH VIDEO';$('liveDot').className=fresh?'live':'';
  $('source').textContent=S?.frame.source?.startsWith('/dev/')?'USB CAMERA':(S?.frame.source==='csi'?'CSI CAMERA':'CAMERA');
  $('videoStatus').hidden=fresh&&!videoError;$('videoStatus').textContent=videoError?'Video connection interrupted':(age===null?'Connecting to the camera…':'Observations stale — simulation holds');
  $('welcome').hidden=F.id!==null||calPoints!==null;
  $('mode').textContent=F.mode;$('reason').textContent=F.reason;$('decision').dataset.mode=F.mode;
  $('motion').textContent=cal?'Simulated speed '+o.speed.toFixed(1)+' m/s · '+F.settings.gap.toFixed(1)+' m gap goal':'Illustrative motion · '+F.settings.gap.toFixed(1)+' gap setting · calibrate for metres';
  $('mapScale').textContent=cal?'FLOOR PLANE ESTIMATE · 1 m GRID · ORIGIN: NEAR-EDGE CENTRE':'SCHEMATIC · NOT TO SCALE · NO METRIC POSITION';
  $('pause').textContent=F.paused?'Resume':'Pause';$('resume').hidden=!F.fault;$('fault').disabled=F.id===null||F.fault;
  $('targetLabel').textContent=F.id===null?'Choose your target':'Selected person #'+F.id;
  const people=(S?.tracks||[]).filter(t=>t.label==='person'&&!t.coasting);
  const key=people.map(t=>t.id).join(',')+':'+F.id;
  if(key!==lastPeople){lastPeople=key;$('targets').replaceChildren();people.forEach(t=>{const b=document.createElement('button');b.textContent='#'+t.id;b.className=t.id===F.id?'selected':'';b.onclick=()=>select(t.id);$('targets').append(b);});}
  $('fps').textContent=fresh&&Number.isFinite(S?.stats.fps)?S.stats.fps.toFixed(1):'—';$('people').textContent=fresh?people.length:'—';$('age').textContent=age!==null?(age>9999?'>9.9 s':age+' ms'):'—';
  const temp=S?.stats.temp_c;$('temp').textContent=Number.isFinite(temp)?temp.toFixed(0)+'°C':'—';$('temp').style.color=temp>=80?'#ffbf69':'';
  $('thermal').textContent=temp>=80?'High temperature — fit active cooling':'';
  const range=Garuda.distance(S,F.id);$('range').textContent='Depth estimate: '+(fresh&&range!==null?range.toFixed(2)+' m (model estimate; verify physically)':'unavailable / invalid');
  if(F.mode==='HOLD'||F.mode==='BRAKE')hadHold=true;
  const j=F.id===null?1:(F.mode==='BRAKE'||F.mode==='HOLD'?3:(hadHold?4:2));for(let i=1;i<=4;i++)$('j'+i).className=i===j?'active':'';
  const stories={IDLE:['Movement earns trust.','Choose yourself in the live view. Walk sideways, hide briefly, then return. The simulated vehicle responds to fresh tracking observations.'],ACQUIRE:['A moment to be sure.','Waiting for a stable sequence of confident observations before movement resumes.'],FOLLOW:[hadHold?'Back in view. Back in motion.':'You lead. Intelligence follows.','The selected track drives this simulated vehicle. Camera perception runs on the Pi with Hailo-10H acceleration.'],BRAKE:['Lost vision. Deliberate stop.','Movement is braking to zero. The simulation does not continue toward the old target destination.'],HOLD:['Trust includes knowing when to stop.',F.reason+'. '+(F.paused?'Resume when ready.':F.faceEnabled?'Return requires a clear enrolled-face match; uncertain matches keep the vehicle holding.':'Waiting for a clear clothing match; similar-looking candidates keep the vehicle holding.') ]};
  stories.GROUND=['Ready on the ground.','Select a person, then press Take off. All flight commands control only this simulation.'];
  stories.TAKEOFF=['Taking off.','The simulated vehicle climbs vertically before following the selected target.'];
  stories.LAND=['Coming down.','The simulated vehicle brakes, then descends at its current position. Take off is required to fly again.'];
  stories.CIRCLE=['A new perspective.','The simulated vehicle circles the selected target. Follow gap sets the circle radius; loss of vision brakes and holds.'];
  const story=stories[F.mode];$('headline').textContent=story[0];$('story').textContent=story[1];
  if(previousMode!==F.mode){events.unshift(new Date().toLocaleTimeString()+'  '+F.mode+' — '+F.reason);events=events.slice(0,5);$('eventlog').replaceChildren(...events.map(e=>{const p=document.createElement('div');p.textContent=e;return p;}));previousMode=F.mode;}
  if(typeof updateControls==='function')updateControls(o,now);
}
function drawOverlay(){const {g,w,h}=canvas('overlay');if(!S)return;const a=fitImage();
  const poly=calPoints||cal?.points;if(poly?.length){g.strokeStyle='#6de3e1';g.fillStyle='#6de3e115';g.lineWidth=2;g.beginPath();poly.forEach((p,i)=>{const x=a.ox+p.x*a.fw*a.k,y=a.oy+p.y*a.fh*a.k;i?g.lineTo(x,y):g.moveTo(x,y);});if(cal)g.closePath();g.stroke();g.fill();poly.forEach((p,i)=>{g.fillStyle='#6de3e1';g.fillText(String(i+1),a.ox+p.x*a.fw*a.k+5,a.oy+p.y*a.fh*a.k-5);});}
  S.tracks.filter(t=>t.label==='person').forEach(t=>{const x=a.ox+t.x*a.k,y=a.oy+t.y*a.k,ww=t.w*a.k,hh=t.h*a.k,selected=t.id===F.id;
    g.strokeStyle=selected?(t.coasting?'#ffbf69':'#6de3e1'):'#ffffff70';g.lineWidth=selected?3:1;g.setLineDash(t.coasting?[6,5]:[]);g.strokeRect(x,y,ww,hh);g.setLineDash([]);g.font='600 12px sans-serif';const text=(selected?'SELECTED ':'PERSON ')+'#'+t.id;g.fillStyle=selected?'#6de3e1':'#edf3fa';g.fillText(text,x+5,Math.max(15,y-7));});
  if(F.gesturesEnabled)for(const t of S.tracks){if(!t.kpts||t.coasting)continue;g.strokeStyle=t.id===F.id?'#6de3e1':'#ffffff66';g.lineWidth=2;for(const [i,j]of[[5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12]]){const p=t.kpts[i],q=t.kpts[j];if(p[2]<.35||q[2]<.35)continue;g.beginPath();g.moveTo(a.ox+p[0]*a.k,a.oy+p[1]*a.k);g.lineTo(a.ox+q[0]*a.k,a.oy+q[1]*a.k);g.stroke();}}
  if(F.faceEnabled)for(const t of S.tracks){if(!t.face||t.face.age_ms>1800)continue;const [x,y,w,h]=t.face.bbox;g.strokeStyle='#ffbf69';g.lineWidth=1;g.strokeRect(a.ox+x*a.k,a.oy+y*a.k,w*a.k,h*a.k);}
}
function drawGimbal(dt){const {g,w,h}=canvas('gimbal'),feed=$('feed');if(!S||!feed.naturalWidth)return;const t=['FOLLOW','CIRCLE'].includes(F.mode)?S.tracks.find(t=>t.id===F.id&&!t.coasting):null,fw=S.frame.width,fh=S.frame.height;
  const k=reduced?1:1-Math.exp(-5*dt);if(t){gx+=((t.x+t.w/2)/fw-gx)*k;gy+=((t.y+t.h/2)/fh-gy)*k;gh+=(Math.min(1,Math.max(.35,t.h/fh*1.8))-gh)*k;}
  let sh=gh*fh,sw=sh*w/h;if(sw>fw){sw=fw;sh=sw*h/w;}const x=Math.max(0,Math.min(fw-sw,gx*fw-sw/2)),y=Math.max(0,Math.min(fh-sh,gy*fh-sh/2));
  try{g.drawImage(feed,x*feed.naturalWidth/fw,y*feed.naturalHeight/fh,sw*feed.naturalWidth/fw,sh*feed.naturalHeight/fh,0,0,w,h);}catch(e){}
  g.strokeStyle='#6de3e1';g.beginPath();g.moveTo(w/2-10,h/2);g.lineTo(w/2+10,h/2);g.moveTo(w/2,h/2-10);g.lineTo(w/2,h/2+10);g.stroke();
  if(F.mode==='HOLD'||F.mode==='BRAKE'){g.fillStyle='#06101a99';g.fillRect(0,0,w,h);g.fillStyle='#ffbf69';g.font='11px sans-serif';g.fillText('GIMBAL HOLD',10,h-12);}
}
function drawMap(o,now){const {g,w,h}=canvas('map');const maxY=cal?cal.depth+2:8,maxX=cal?Math.max(4,cal.width+2):9,scale=Math.min((w-65)/maxX,(h-65)/maxY);
  const pt=(x,y)=>[w/2+x*scale,h-28-y*scale];g.lineWidth=1;g.strokeStyle='#293b5080';
  const gridWidth=(w-45)/scale;
  for(let x=-Math.ceil(gridWidth/2);x<=gridWidth/2;x++){g.beginPath();g.moveTo(...pt(x,0));g.lineTo(...pt(x,maxY));g.stroke();}
  for(let y=0;y<=maxY;y++){g.beginPath();g.moveTo(...pt(-gridWidth/2,y));g.lineTo(...pt(gridWidth/2,y));g.stroke();}
  g.strokeStyle='#ffbf6955';g.setLineDash([6,7]);g.beginPath();g.arc(...pt(0,.8),F.settings.radius*scale,0,Math.PI*2);g.stroke();g.setLineDash([]);
  if(cal){const [x,y]=pt(-cal.width/2,cal.depth+1);g.fillStyle='#6de3e108';g.fillRect(x,y,cal.width*scale,cal.depth*scale);g.strokeStyle='#6de3e155';g.strokeRect(x,y,cal.width*scale,cal.depth*scale);}
  else{const [cx,cy]=pt(0,0);const grad=g.createRadialGradient(cx,cy,0,cx,cy,scale*7);grad.addColorStop(0,'#4a749c26');grad.addColorStop(1,'#4a749c00');g.fillStyle=grad;g.beginPath();g.moveTo(cx,cy);g.lineTo(...pt(-3.6,7));g.lineTo(...pt(3.6,7));g.closePath();g.fill();}
  if(now-lastTrail>100){lastTrail=now;trail.push({x:F.x,y:F.y,t:now});if(o.point)targetTrail.push({...o.point,t:now});}trail=trail.filter(p=>now-p.t<6000);targetTrail=targetTrail.filter(p=>now-p.t<6000);
  function path(arr,color){if(arr.length<2)return;g.strokeStyle=color;g.lineWidth=2;g.beginPath();arr.forEach((p,i)=>{const a=pt(p.x,p.y);i?g.lineTo(...a):g.moveTo(...a);});g.stroke();}path(trail,'#6de3e166');path(targetTrail,'#ed1c2455');
  const t=o.point||(targetTrail.length?targetTrail[targetTrail.length-1]:null);
  if(t){const [x,y]=pt(t.x,t.y);g.fillStyle=o.point?'#ed1c241b':'#ffbf6914';g.strokeStyle=o.point?'#ed1c2480':'#ffbf6955';g.beginPath();g.ellipse(x,y,scale*.42,scale*.62,0,0,Math.PI*2);g.fill();g.stroke();g.fillStyle=o.point?'#ed1c24':'#ffbf69';g.beginPath();g.arc(x,y,5,0,Math.PI*2);g.fill();g.font='10px sans-serif';g.fillText(o.point?'#'+F.id:'LAST OBSERVATION',x+10,y-9);g.strokeStyle='#8fa4bb66';g.setLineDash([4,6]);g.beginPath();g.moveTo(...pt(F.x,F.y));g.lineTo(x,y);g.stroke();g.setLineDash([]);}
  if(t&&F.flightStyle==='circle'){g.strokeStyle='#6de3e155';g.setLineDash([5,5]);g.beginPath();g.arc(...pt(t.x,t.y),(F.orbitRadius??F.settings.gap)*scale,0,Math.PI*2);g.stroke();g.setLineDash([]);}
  const [x,y]=pt(F.x,F.y);g.fillStyle='#6de3e10a';g.strokeStyle='#6de3e133';g.lineWidth=1;g.beginPath();g.arc(x,y,26,0,Math.PI*2);g.fill();g.stroke();g.fillStyle='#afbed1';g.font='10px sans-serif';g.fillText(F.altitude.toFixed(1)+' m SIM',x+22,y+22);g.save();g.translate(x,y);g.scale(1+F.altitude*.12,1+F.altitude*.12);g.rotate(F.heading);g.shadowColor='#6de3e1';g.shadowBlur=reduced?0:14;g.strokeStyle='#b8ffff';g.lineWidth=2.2;g.beginPath();g.moveTo(-10,-10);g.lineTo(10,10);g.moveTo(10,-10);g.lineTo(-10,10);g.stroke();
  for(const [a,b]of[[-10,-10],[10,-10],[-10,10],[10,10]]){g.beginPath();g.arc(a,b,6,0,Math.PI*2);g.stroke();if(!reduced){g.save();g.translate(a,b);g.rotate(now*.025);g.beginPath();g.moveTo(-4,0);g.lineTo(4,0);g.stroke();g.restore();}}
  g.fillStyle='#fff';g.beginPath();g.moveTo(0,-18);g.lineTo(-4,-10);g.lineTo(4,-10);g.closePath();g.fill();g.restore();
  const [cx,cy]=pt(0,0);g.fillStyle='#8399b3';g.fillRect(cx-6,cy-4,12,7);g.font='8px sans-serif';g.fillText(cal?'FLOOR ORIGIN':'CAMERA (ILLUSTRATIVE)',cx+12,cy+3);
}
let last=performance.now();
function tick(now){requestAnimationFrame(tick);if(now-lastPaint<32)return;const dt=Math.min((now-last)/1000,.05);last=now;lastPaint=now;
  const o=F.step(dt,now,(t,s)=>Garuda.project(t,s,cal));if(o.track&&F.id!==syncedId)syncTarget(o.track);updateUI(o,now);drawOverlay();drawMap(o,now);drawGimbal(dt);
}requestAnimationFrame(tick);
