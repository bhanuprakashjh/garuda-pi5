'use strict';
const $=id=>document.getElementById(id), records=[];
const demo=new URL(location.href);demo.port='8098';demo.pathname='/demo';demo.search='';demo.hash='';
$('demo').href=demo.href;$('openDemo').href=demo.href;
const guides={
 detection:['Object detection check','Use the primary detector. Place one known object fully in view, then try multiple objects, different lighting and partial occlusion. Record expected class/count and the labels actually shown. Record false positives in an empty scene too. This is a functional check, not model retraining or a precision/recall benchmark.'],
 gesture:['Gesture check','Select Pose mode in the main demo. Keep the entire upper body visible. Test one gesture at a time, return to a neutral pose between trials, and include neutral/no-gesture trials. Record the person/track ID, intended gesture and actual gesture label. Mode changes affect other viewers. Existing rules are frozen; this form does not change them.'],
 mono:['Monocular distance check','Keep the camera fixed. Select one person, identify the active metric depth engine, measure the reference distance and wait for a fresh estimate. Repeat at several measured positions. Keep separate recordings for later validation. Guesses may be recorded as practice only, without a numeric tape reference.'],
 tracking:['Tracking and selection check','Select a person and record the track ID. Walk across the view, briefly occlude, leave and return; include a second person crossing. Record lost locks or identity switches. Check camera-restart behavior only in a separately coordinated bench session. These observations do not authorize flight follow.']};
function render(){const [title,text]=guides[$('kind').value];$('instructions').replaceChildren();const h=document.createElement('h3'),p=document.createElement('p');h.textContent=title;p.textContent=text;$('instructions').append(h,p);$('distanceFields').hidden=$('kind').value!=='mono';}
$('kind').onchange=render;render();
$('check').onsubmit=e=>{e.preventDefault();const f=new FormData(e.target), kind=$('kind').value;
 const row={kind,recorded_at:new Date().toISOString(),evidence_type:'manual_operator_observation',applied:false};
 for(const key of ['operator','recording','conditions','expected','observed','split','outcome'])row[key]=String(f.get(key)||'').trim();
 if(kind==='mono'){
  const measured=f.has('measured'), tape=String(f.get('tape')||'');
  if((tape||row.split!=='practice')&&(!measured||!tape||!String(f.get('reference')||'').trim())){$('status').textContent='A measured mono record needs a tape reading, its reference, and confirmation that it was measured. Use Practice with no tape value for guesses.';return;}
  row.tape_m=tape?Number(tape):null;row.tape_measured=measured&&Boolean(tape);row.tape_reference=String(f.get('reference')||'');row.displayed_estimate_m=f.get('estimate')?Number(f.get('estimate')):null;
  if([row.tape_m,row.displayed_estimate_m].some(v=>v!==null&&(!Number.isFinite(v)||v<=0))){$('status').textContent='Distances must be finite and positive.';return;}
 }
 records.push(row);const item=document.createElement('li');item.textContent=`${row.kind} · ${row.outcome} · ${row.recorded_at}`;$('records').append(item);$('download').disabled=false;$('status').textContent=`${records.length} manual observations in this tab. Download to keep them.`;
 for(const key of ['tape','estimate'])e.target.elements[key].value='';e.target.elements.measured.checked=false;
};
$('download').onclick=()=>{const blob=new Blob([JSON.stringify({schema:1,automatic_validation:false,records},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='garuda-operator-checks-'+Date.now()+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
