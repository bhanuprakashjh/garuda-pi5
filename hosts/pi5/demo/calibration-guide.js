/* Pure operator guidance and pattern geometry; no camera or calibration writes. */
(function(root){
function patternGeometry(kind,columns,rows){
 const cols=Math.max(2,Math.min(20,Math.trunc(Number(columns)||9))), r=Math.max(2,Math.min(20,Math.trunc(Number(rows)||6)));
 const points=[],squares=[];
 if(kind==='checkerboard'){
  for(let y=0;y<=r;y++)for(let x=0;x<=cols;x++)if((x+y)%2===0)squares.push([x,y]);
  for(let y=1;y<=r;y++)for(let x=1;x<=cols;x++)points.push([x,y]);
  return {width:cols+1,height:r+1,points,squares,pitchText:'Measure one square side. Count the marked inner intersections, not the squares.'};
 }
 for(let y=0;y<r;y++)for(let x=0;x<cols;x++)points.push([kind==='asymmetric_circles'?2*x+y%2:x,y]);
 return {width:kind==='asymmetric_circles'?2*cols:cols,height:r,points,squares,
  pitchText:kind==='asymmetric_circles'?'Measure perpendicular row spacing. Centres in the same row are twice this distance apart.':'Measure the distance between neighbouring circle centres.'};
}
function nextStep(config,captures){
 const goal=config.goal||'practice';
 const useful=captures.filter(c=>c.pattern_found&&c.has_depth&&(goal!=='depth'||((!config.require_measured_tape||c.tape_measured===true)&&Number.isFinite(c.distance_m)&&c.distance_m>0&&c.board_depth?.median_m>0)));
 const tune=useful.filter(c=>c.split==='tune').length, validation=useful.filter(c=>c.split==='validate').length;
 const needed=goal==='lens'?12:3, split=tune<needed?'tune':'validate';
 const depthSteps=['Near position · centre the board','Middle position · move the board farther away','Far position · keep the entire pattern visible'];
 const lensSteps=['Centre · board facing camera','Centre · move farther away','Centre · move closer, keep all corners visible','Left side of image','Right side of image','Upper part of image','Lower part of image','Centre · tilt left','Centre · tilt right','Centre · tilt up','Centre · tilt down','A new position and tilt'];
 let title, instruction;
 if(goal==='practice'){title='Practice framing the board';instruction='Keep every corner visible, hold still, then capture. These images are exploratory.';}
 else if(tune>=needed&&validation>=3){title='Capture set ready to review';instruction='Review the saved images and export. This count is not an accuracy pass; more captures can be added.';}
 else if(split==='validate'){title=`Independent check ${validation+1} of 3`;instruction='Move the board away, reposition it at a separately measured distance, and capture a new view. Do not reuse the tuning images.';}
 else {title=(goal==='lens'?lensSteps:depthSteps)[tune];instruction=goal==='lens'?'Keep the full pattern visible and vary its angle. Hold still before capture.':'Keep the board facing the camera. Measure the distance to its centre from your stated tape reference, enter it below, then capture.';}
 return {goal,tune,validation,needed,split,title,instruction};
}
const api={patternGeometry,nextStep};if(typeof module!=='undefined')module.exports=api;else root.CalibrationGuide=api;
})(typeof globalThis!=='undefined'?globalThis:this);
