'use strict';
const $=id=>document.getElementById(id),G=CalibrationGuide;
const demo=new URL(location.href);demo.port='8098';demo.pathname='/demo';demo.search='';$('demoLink').href=demo.href;
let selected=null,sessions=[],busy=false,live=null,lastLive=0,polling=false;
$('name').value='Camera check · '+new Date().toLocaleDateString();
function message(text,error=false){$('message').textContent=text;$('message').className=error?'error':'';}
function changedMode(){return !!(selected?.binding&&live&&(selected.binding.epoch!==live.epoch||selected.binding.mode!==live.mode||selected.binding.calibration_id!==live.calibration_id));}
function controls(){
 $('sessions').disabled=busy;$('newSession').disabled=busy;
 $('captureButton').disabled=busy||!selected||!live?.depth_present||changedMode();
 for(const id of ['solve','export'])$(id).disabled=busy||!selected;
 document.querySelector('#create button').disabled=busy;
 const change=changedMode();$('modeWarning').hidden=!change;
 if(change)$('modeWarning').textContent='Camera mode or run changed. This older session is preserved. Use “New depth session with these details” before taking new captures.';
}
async function api(path,data){const r=await fetch(path,{signal:AbortSignal.timeout(60000),...(data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})});const value=await r.json();if(!r.ok)throw Error(value.error||'Request failed');return value;}
async function operation(fn){if(busy)return;busy=true;controls();try{await fn();}catch(e){message(e.message,true);}finally{busy=false;controls();}}
function diagram(kind,cols,rows,mark=true){
 const g=G.patternGeometry(kind,cols,rows),pad=1;
 let shapes=`<rect x="-1" y="-1" width="${g.width+2}" height="${g.height+2}" fill="white"/>`;
 if(kind==='checkerboard'){
  shapes+=g.squares.map(([x,y])=>`<rect x="${x}" y="${y}" width="1" height="1" fill="#18202b"/>`).join('');
  if(mark)shapes+=g.points.map(([x,y])=>`<circle cx="${x}" cy="${y}" r=".09" fill="#ed3848"/>`).join('');
 }else shapes+=g.points.map(([x,y])=>`<circle cx="${x+.5}" cy="${y+.5}" r=".23" fill="#18202b"/>`).join('');
 return `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Pattern illustration" viewBox="-${pad} -${pad} ${g.width+2*pad} ${g.height+2*pad}">${shapes}</svg>`;
}
function patternUI(){
 const kind=$('pattern').value,cols=$('columns').value,rows=$('rows').value,g=G.patternGeometry(kind,cols,rows);
 $('patternDiagram').innerHTML=diagram(kind,cols,rows);$('patternHint').textContent=g.pitchText+(kind==='checkerboard'?` ${g.points.length} marked intersections; ${g.width} × ${g.height} squares.`:'');
 $('columnLabel').firstChild.textContent=kind==='checkerboard'?'Inner corners across':'Centres per row';
 $('rowLabel').firstChild.textContent=kind==='checkerboard'?'Inner corners down':'Number of rows';
 $('pitchLabel').firstChild.textContent=kind==='checkerboard'?'Measured square side (mm)':kind==='circles'?'Centre-to-centre spacing (mm)':'Perpendicular row spacing (mm)';
 for(const button of document.querySelectorAll('[data-pattern]'))button.setAttribute('aria-pressed',String(button.dataset.pattern===kind));
}
for(const mini of document.querySelectorAll('[data-mini]'))mini.innerHTML=diagram(mini.dataset.mini,4,3,false);
const sheetPatterns={
 '1':['checkerboard',9,6],'2':['checkerboard',11,8],
 '3':['circles',7,5],'4':['asymmetric_circles',4,7]
};
$('printedSheet').onchange=()=>{
 const sheet=sheetPatterns[$('printedSheet').value];
 if(sheet){[$('pattern').value,$('columns').value,$('rows').value]=sheet;$('pitch').value='';patternUI();}
};
function customPattern(){$('printedSheet').value='';$('pitch').value='';patternUI();}
for(const button of document.querySelectorAll('[data-pattern]'))button.onclick=()=>{$('pattern').value=button.dataset.pattern;customPattern();};
for(const id of ['columns','rows'])$(id).oninput=customPattern;
$('mountContext').onchange=()=>{$('mountId').required=$('mountContext').value==='grounded_airframe';};
patternUI();
function guide(){
 if(!selected){$('phase').textContent='PREPARE';$('guideTitle').textContent='Mount the board and enter its dimensions';$('guideText').textContent='The diagram marks the points we detect. Check the count and measure the pattern pitch before starting.';return;}
 const step=G.nextStep(selected.config,selected.captures);$('phase').textContent=step.split==='tune'?'SETUP / TUNING':'INDEPENDENT VALIDATION';
 $('guideTitle').textContent=step.title;$('guideText').textContent=step.instruction;$('split').value=step.split;$('distance').required=step.goal==='depth';
 $('counts').textContent=`${step.tune}/${step.needed} setup · ${step.validation}/3 checks · ${selected.captures.length}/60 saved`;
}
function clearTape(){
 $('distance').value='';$('tapeUncertainty').value='';$('measured').checked=false;
}
$('distance').addEventListener('input',()=>{$('measured').checked=false;});
function render(){
 const depthCheck=selected?.config?.goal==='depth';
 $('tapeCheck').hidden=!depthCheck;$('measured').required=!!depthCheck;
 clearTape();
 $('create').hidden=!!selected;$('sessionInfo').hidden=!selected;$('newSession').hidden=!selected;$('captures').replaceChildren();$('empty').hidden=!!selected?.captures.length;
 $('preview').hidden=true;$('depthPreview').hidden=true;$('captureInfo').textContent='';$('resultSection').hidden=true;
 if(!selected){$('counts').textContent='No session selected';guide();controls();return;}
 const c=selected.config;
 $('sessionInfo').textContent=`${c.name}\n${c.operator} · ${c.camera}\n${c.pattern} · ${c.columns} × ${c.rows} · ${c.pitch_mm} mm\nMount: ${c.mount_context||'unspecified'} ${c.mount_id||''}\nBoard: ${c.board_id} · ${c.board_condition}\nTape reference: ${c.tape_reference}\n${selected.binding?.mode||'Waiting for first capture'}`;
 selected.captures.forEach((c,i)=>{const row=document.createElement('tr'),cell=document.createElement('td'),button=document.createElement('button');button.textContent=`View ${i+1}`;button.onclick=()=>{
 $('preview').src=`/api/session/${selected.id}/${c.id}-preview.jpg`;$('preview').hidden=false;$('depthPreview').hidden=!c.has_depth;
 if(c.has_depth)$('depthPreview').src=`/api/session/${selected.id}/${c.id}-depth-preview.jpg`;
 const d=c.board_depth;$('captureInfo').textContent=`${c.note||'No note'} · ${c.geometry_eligible?'Board declared flat; geometry not verified':'Exploratory: flatness not established'}${d?` · ${Math.round(d.valid_fraction*100)}% of board interior has valid depth`:''}`;};cell.append(button);row.append(cell);
 const depth=c.board_depth?.median_m!=null?`${c.board_depth.median_m.toFixed(3)} m · unvalidated`:c.has_depth?'Saved; no board value':'Not available';
 for(const value of [c.split==='tune'?'Setup':'Validation',c.distance_m==null?'—':`${c.distance_m} m`,c.pattern_found?'Detected':'Not found',depth]){const td=document.createElement('td');td.textContent=value;row.append(td);}$('captures').append(row);});guide();controls();
}
async function refresh(id){sessions=await api('/api/sessions');$('sessions').replaceChildren(new Option('Start a new session',''));for(const s of sessions)$('sessions').add(new Option(s.config.name+' · '+s.id.slice(0,6),s.id));selected=sessions.find(s=>s.id===id)||null;$('sessions').value=selected?.id||'';render();}
$('sessions').onchange=()=>{selected=sessions.find(s=>s.id===$('sessions').value)||null;render();message(selected?'Session loaded. Follow the next step below.':'Prepare your pattern and start a new session.');};
$('newSession').onclick=()=>{if(!selected)return;const c=selected.config;for(const name of ['operator','camera','board_id','pattern','columns','rows','pitch_mm','board_condition','tape_reference'])document.querySelector(`#create [name="${name}"]`).value=c[name];$('mountContext').value=c.mount_context||'unspecified';$('mountId').value=c.mount_id||'';$('mountContext').onchange();$('printedSheet').value='';$('goal').value=c.goal||'depth';$('name').value=c.name+' · depth';$('distance').value='';$('note').value='';selected=null;$('sessions').value='';patternUI();render();message('Board details copied. Check them, then start the new session. Older images are preserved.');};
$('create').onsubmit=e=>{e.preventDefault();operation(async()=>{const s=await api('/api/sessions',Object.fromEntries(new FormData(e.target)));await refresh(s.id);message('Session ready. Follow the highlighted step and capture RGB + depth.');});};
$('capture').onsubmit=e=>{e.preventDefault();if(!selected)return;operation(async()=>{message('Capturing a matched RGB + depth pair and detecting the pattern…');const id=selected.id;const s=await api(`/api/session/${id}/capture`,{split:$('split').value,distance_m:$('distance').value===''?null:Number($('distance').value),note:$('note').value,
 tape_measured:selected.config.goal==='depth'&&$('measured').checked,
 tape_uncertainty_mm:selected.config.goal==='depth'&&$('tapeUncertainty').value!==''?Number($('tapeUncertainty').value):null});await refresh(id);const c=s.captures.at(-1);message(c.pattern_found?'Pattern detected. Inspect the saved colour and depth images, then follow the next step.':'Image and depth saved, but pattern not found. Check the illustrated count, full visibility, glare and motion.',!c.pattern_found);$('captures').lastChild?.querySelector('button').click();$('distance').value='';});};
$('solve').onclick=()=>operation(async()=>{message('Estimating delivered-RGB candidate…');const r=await api(`/api/session/${selected.id}/solve`,{});$('resultSection').hidden=false;$('result').textContent=JSON.stringify(r,null,2);message('Candidate saved for review. No camera settings changed.');});
$('export').onclick=()=>operation(async()=>{message('Preparing download…');const r=await fetch(`/api/session/${selected.id}/export`,{signal:AbortSignal.timeout(60000)});if(!r.ok){const d=await r.json();throw Error(d.error);}const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download=`garuda-calibration-${selected.id}.zip`;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);message('ZIP downloaded with images and session metadata.');});
function offline(reason){live=null;$('depthStatus').textContent='DEPTH NOT READY';$('depthStatus').className='badge off';$('live').removeAttribute('src');$('liveDepth').hidden=true;$('depthUnavailable').hidden=false;$('depthUnavailable').textContent=reason;$('pairStatus').textContent='No fresh pair';$('depthQuality').textContent='';controls();}
async function poll(){
 if(polling||busy||document.hidden)return;polling=true;
 try{const r=await fetch('/api/live',{signal:AbortSignal.timeout(4000)});if(r.status===409)return;const v=await r.json();if(!r.ok)throw Error(v.error||'Camera unavailable');live=v;lastLive=performance.now();$('live').src=v.rgb;
 $('depthStatus').textContent=v.depth_present?`DEPTH PREVIEW · ${v.depth_backend||'unknown source'}`:'DEPTH OFF / NO MATCHING FRAME';$('depthStatus').className='badge '+(v.depth_present?'on':'off');
 $('liveDepth').hidden=!v.depth_present;$('depthUnavailable').hidden=v.depth_present;if(v.depth_present)$('liveDepth').src=v.depth;else $('depthUnavailable').textContent='Stereo is not producing a matching depth frame. Capture is disabled until depth returns.';
 $('depthQuality').textContent=v.depth_present?`${Math.round(v.valid_fraction*100)}% nonzero image pixels · not confidence`:'';$('pairStatus').textContent=`${v.mode==='undistorted_rgb'?'Undistorted RGB + stereo':'RGB only'} · frame ${v.sequence}`;controls();
 }catch(e){offline(e.message);}finally{polling=false;}
}
setInterval(poll,1200);setInterval(()=>{if(lastLive&&performance.now()-lastLive>5000)offline('Live preview is stale. Waiting for the camera…');},500);
operation(async()=>{await refresh();message('Choose your task and match the diagram to your target.');}).then(poll);
