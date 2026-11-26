export function queryStep(requirement){
 const state=requirement?.state;
 if(state==='DRAFT')return {name:'progress',key:`progress:${requirement.model_progress?.phase||'QUEUED'}`};
 if(state==='NEED_CLARIFICATION')return {name:['time','time_periods'].includes(requirement.clarifications?.[0]?.slot)?'time':'clarification',key:`clarification:${requirement.clarifications?.[0]?.id||''}`};
 if(state==='TARGET_CONFIRMATION')return {name:'intent',key:state};
 if(state==='NEED_CONFIRMATION')return {name:'metric',key:state};
 if(state==='PREVIEW_READY'||state==='VERIFIED')return {name:'sql',key:state};
 if(['GATE_PASSED','AUTHORIZED','EXECUTING','VALIDATING'].includes(state))return {name:'gate',key:state};
 if(state==='DELIVERED')return {name:'result',key:state};
 return {name:'notice',key:state};
}

export function scrollQueryStep(container,element,reducedMotion=false){
 if(!container||!element)return;
 const top=element.getBoundingClientRect().top-container.getBoundingClientRect().top+container.scrollTop-18;
 container.scrollTo({top:Math.max(0,top),behavior:reducedMotion?'instant':'smooth'});
}
