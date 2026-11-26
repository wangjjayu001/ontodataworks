import {useEffect,useRef} from 'react';
import {queryStep,scrollQueryStep} from './queryStepScroll.js';

export function useQueryStepScroll(containerRef,run,submission,mobilePane){
 const following=useRef(null),frame=useRef(null);
 const follow=(requirementId=null,afterVersion=null,newRun=false)=>{following.current={runId:newRun?null:run?.run_id||run?.id,requirementId,afterVersion,last:null,newRun};};
 const stop=()=>{following.current=null;};
 useEffect(()=>{
  const container=containerRef.current;if(!container)return;
  const interrupt=event=>{if(event.type!=='keydown'||['PageUp','PageDown','Home','End','ArrowUp','ArrowDown'].includes(event.key))stop();};
  container.addEventListener('wheel',interrupt,{passive:true});container.addEventListener('touchstart',interrupt,{passive:true});container.addEventListener('keydown',interrupt);
  return()=>{container.removeEventListener('wheel',interrupt);container.removeEventListener('touchstart',interrupt);container.removeEventListener('keydown',interrupt);};
 },[containerRef]);
 useEffect(()=>{
  const tracking=following.current,container=containerRef.current;if(!tracking||!container||mobilePane!=='chat')return;
  const runId=run?.run_id||run?.id;
  if(!submission&&tracking.newRun){tracking.runId=runId;tracking.newRun=false;}
  if(!submission&&tracking.runId&&runId!==tracking.runId){stop();return;}
  const requirements=run?.requirements||[];
  const req=submission?null:tracking.requirementId?requirements.find(r=>(r.requirement_id||r.id)===tracking.requirementId):requirements[0];
  if(req&&tracking.afterVersion!==null&&req.requirement_version<=tracking.afterVersion)return;
  const step=submission||(!req&&run?.state==='DRAFT')?{name:'progress',key:submission?'submission':run?.model_progress?.phase||'QUEUED'}:queryStep(req);
  const identity=`${runId}:${req?.id||req?.requirement_id}:${step.key}`;
  if(identity===tracking.last)return;
  frame.current=requestAnimationFrame(()=>{
   if(following.current!==tracking)return;
   tracking.last=identity;
   const card=req?[...container.querySelectorAll('[data-requirement-id]')].find(el=>el.dataset.requirementId===(req.requirement_id||req.id)):container;
   const target=card?.querySelector(`[data-query-step="${step.name}"]`)||card;
   if(target){scrollQueryStep(container,target,matchMedia('(prefers-reduced-motion: reduce)').matches);target.classList.add('sq-current-step');setTimeout(()=>target.classList.remove('sq-current-step'),1600);}
  });
  return()=>{if(frame.current)cancelAnimationFrame(frame.current);};
 },[run,submission,mobilePane,containerRef]);
 return {follow,stop};
}
