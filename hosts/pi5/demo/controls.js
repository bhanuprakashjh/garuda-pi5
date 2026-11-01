'use strict';
const controlStyle=document.createElement('link');controlStyle.rel='stylesheet';controlStyle.href='/demo/controls.css';document.head.append(controlStyle);
const controlPanel=document.createElement('section');controlPanel.className='flightcontrols';
controlPanel.innerHTML=`<div><div class="tuning">
<label>Follow gap <output id="gapValue">2.0 m</output><input id="gapSetting" type="range" min="0.5" max="5" step="0.1" value="2" aria-label="Requested follow gap"></label>
<label>Max travel from home <output id="radiusValue">6.0 m</output><input id="radiusSetting" type="range" min="1" max="12" step="0.5" value="6" aria-label="Maximum simulated travel"></label>
<label>Speed cap <output id="speedValue">2.2 m/s</output><input id="speedSetting" type="range" min="0.25" max="3" step="0.05" value="2.2" aria-label="Simulated speed cap"></label>
</div><div class="controlnote" id="tuningNote">Simulation settings. Calibrate the floor to relate map distances to metres.</div>
<div class="positionChoice">Follow position <button id="sideLeft">Left</button><button id="sideBehind" class="active">Behind</button><button id="sideRight">Right</button><button id="tuningReset">Reset settings</button></div></div>
<div><div class="controlmodes"><label><input id="gestureEnabled" type="checkbox"> Gesture control</label><label><input id="faceEnabled" type="checkbox"> Face-confirmed return</label><div class="faceactions"><button id="faceEnroll" disabled>Enroll selected face</button><button id="faceForget" disabled>Forget face</button></div></div>
<p class="identityline" id="faceStatus">Face matching off</p><p class="gesturehelp" id="gestureHelp">One arm up → take off/follow · arms crossed → land · T pose → circle · both hands overhead → hold. Enable gestures and hold each pose for one second.</p><p class="controlnote">Pose: Hailo-10H · Optional face: Pi CPU · Enrollment stays in this tab. Camera modes are shared by viewers.</p></div>`;
document.querySelector('.stage').before(controlPanel);
const gesturePanel=document.createElement('div');gesturePanel.className='gestureReadout';
gesturePanel.innerHTML='<div><small>GESTURE · HAILO-10H POSE</small><strong id="gestureName">GESTURE CONTROL OFF</strong><span id="gestureDetail">Enable gesture control to begin.</span></div><button id="gestureStart">Enable gestures</button><p>ONE ARM UP → TAKE OFF / FOLLOW · ARMS CROSSED → LAND · T POSE → CIRCLE · BOTH HANDS OVERHEAD → HOLD<br>Keep both hands and shoulders visible. Hold for one second. Follow gap sets circle radius.</p>';
document.querySelector('.cameraPanel .panelhead').after(gesturePanel);
$('gestureStart').onclick=()=>$('gestureEnabled').click();
const flightActions=document.createElement('div');flightActions.className='flightCommands';
flightActions.innerHTML='<span id="airState">GROUNDED · SIMULATED</span><button id="takeoff" class="primary">Take off</button><button id="land">Land</button><button id="followTarget">Follow target</button><button id="circleTarget">Circle target</button>';
document.querySelector('.flightPanel .actions').before(flightActions);
$('takeoff').onclick=()=>F.takeoff();$('land').onclick=()=>F.land();
$('followTarget').onclick=()=>F.setFlightStyle('follow');$('circleTarget').onclick=()=>F.setFlightStyle('circle');
try{const saved=JSON.parse(localStorage.getItem('garuda-flight-settings'));if(saved)F.configure(saved);}catch(e){}
function syncSettings(){for(const name of ['gap','radius','speed']){$(name+'Setting').value=F.settings[name];$(name+'Value').textContent=F.settings[name].toFixed(name==='speed'?2:1)+(name==='speed'?' m/s':' m');}}
syncSettings();
for(const name of ['gap','radius','speed'])$(name+'Setting').oninput=()=>{F.configure({[name]:$(name+'Setting').value});syncSettings();try{localStorage.setItem('garuda-flight-settings',JSON.stringify(F.settings));}catch(e){}};
$('tuningReset').onclick=()=>{F.configure({gap:2,radius:6,speed:2.2});F.side=0;syncSettings();localStorage.removeItem('garuda-flight-settings');};
for(const [id,side]of[['sideLeft',-1],['sideBehind',0],['sideRight',1]])$(id).onclick=()=>F.side=side;
let gestureBusy=false,faceBusy=false,controlError='';
$('gestureEnabled').onchange=async()=>{gestureBusy=true;controlError='';const enabled=$('gestureEnabled').checked;
  try{const response=await fetch('/api/detector?mode='+(enabled?'pose':'primary'),{method:'POST'}),result=await response.json();if(!response.ok||!result.ok)throw Error(result.reason||'Pose switch failed');F.gesturesEnabled=enabled;F.gestureKey='';F.reset();}
  catch(e){$('gestureEnabled').checked=!enabled;controlError=e.message;}
  finally{gestureBusy=false;}};
$('faceEnabled').onchange=async()=>{faceBusy=true;controlError='';const enabled=$('faceEnabled').checked;
  try{const response=await fetch('/api/demo_face?enabled='+(enabled?'1':'0'),{method:'POST'}),result=await response.json();if(!response.ok||!result.available&&enabled)throw Error('Face models unavailable');F.setFaceMode(enabled);}
  catch(e){$('faceEnabled').checked=!enabled;controlError=e.message;}
  finally{faceBusy=false;}};
$('faceEnroll').onclick=()=>F.enroll();
$('faceForget').onclick=()=>F.setFaceMode(F.faceEnabled);
function updateControls(o,now=performance.now()){
  $('airState').textContent=F.airState+' · '+F.altitude.toFixed(1)+' m simulated altitude';
  $('takeoff').disabled=F.airState!=='GROUNDED';$('land').disabled=['GROUNDED','LANDING'].includes(F.airState);
  $('circleTarget').disabled=F.id===null;
  $('circleTarget').className=F.flightStyle==='circle'?'selected':'';$('followTarget').className=F.flightStyle==='follow'?'selected':'';
  $('gestureEnabled').disabled=gestureBusy;$('faceEnabled').disabled=faceBusy;
  $('faceEnroll').disabled=!F.faceEnabled||F.id===null||F.enrolling||!S?.face_service?.ready;
  $('faceForget').disabled=!F.faceEnabled;
  let msg=controlError||F.faceMessage;
  if(F.faceEnabled&&!S?.face_service?.enabled)msg='Face service off • toggle face mode to restart';
  else if(F.faceEnabled&&S?.face_service?.error)msg='Face service: '+S.face_service.error;
  else if(F.faceEnabled&&!S?.face_service?.ready)msg='Loading face models on Pi CPU…';
  $('faceStatus').textContent=gestureBusy?'Switching Hailo pose mode…':msg;
  const t=S?.tracks.find(t=>t.id===F.id),gesture=t?.gesture?.name;
  const fresh=S&&Number.isFinite(S.age_ms)&&S.age_ms+now-F.received<700&&now-F.progress<800&&now-F.received<800;
  let title='GESTURE CONTROL OFF',detail='Enable gesture control to begin.',active=false;
  if(gestureBusy){title='STARTING POSE DETECTION';detail='Loading the Hailo pose model…';}
  else if(F.gesturesEnabled){
    if(!fresh){title='WAITING FOR FRESH VIDEO';detail='Gesture commands are unavailable while observations are stale.';}
    else if(S?.detector?.mode!=='pose'){title='POSE MODE NOT ACTIVE';detail='Toggle Gesture control off and on to restart.';}
    else if(F.id===null){title='SELECT A PERSON';detail='Click your box, or hold one arm straight above your head to begin.';}
    else if(!t||t.coasting){title='PERSON #'+F.id+' OUT OF VIEW';detail='Return to view or select your current person box.';}
    else if(!t.kpts?.length){title='WAITING FOR BODY POSE';detail='Person #'+F.id+' · Keep your upper body and hands visible.';}
    else if([5,6,9,10].some(i=>!t.kpts[i]||t.kpts[i][2]<.35||t.kpts[i][0]<0||t.kpts[i][0]>S.frame.width||t.kpts[i][1]<0||t.kpts[i][1]>S.frame.height)){title='KEEP BOTH ARMS IN VIEW';detail='Step back so both shoulders and hands fit in the camera. Arm pose is uncertain.';}
    else if(gesture){title=({follow_me:'TAKE OFF / FOLLOW',circle:'CIRCLE TARGET',land:'LAND',stop:'STOP / HOLD'})[gesture]||gesture.replaceAll('_',' ').toUpperCase();detail='Person #'+F.id+' · Gesture ID: '+gesture+' · '+(F.paused?'Simulation paused':'Simulation gesture control active');active=true;}
    else{title='NO GESTURE DETECTED';detail='Person #'+F.id+' · Raise your arm above your head and hold for one second.';}
  }
  $('gestureName').textContent=title;$('gestureDetail').textContent=detail;gesturePanel.dataset.active=String(active);
  $('gestureStart').hidden=F.gesturesEnabled;$('gestureStart').disabled=gestureBusy;
  if(F.gesturesEnabled&&S?.detector.mode!=='pose')$('gestureHelp').textContent='Pose mode changed on another display. Toggle Gesture control to restart.';
  else if(F.gesturesEnabled)$('gestureHelp').textContent='One arm up: take off/follow · crossed arms: land · T pose: circle · both hands overhead: hold · point: change side. Only the selected person controls an active lock.';
  $('tuningNote').textContent=cal?'Calibrated floor estimate. Gap is a goal; the home travel boundary takes priority.':'Requested simulation settings; map is schematic until floor calibration. Values are not measured separation.';
  for(const [id,side]of[['sideLeft',-1],['sideBehind',0],['sideRight',1]])$(id).className=F.side===side?'active':'';
}
