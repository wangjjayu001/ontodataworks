export function timeRangeAnswer(start,endExclusive){
 const isDate=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&!Number.isNaN(Date.parse(value+'T00:00:00Z'))&&new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value;
 if(!isDate(start)||!isDate(endExclusive)||start>=endExclusive)return null;
 return {start,end_exclusive:endExclusive};
}

export function clarificationChoice(option){
 if(option===null||typeof option!=='object')return {value:option,label:option,description:null};
 const value=option.value??option.id??option.text??option.label;
 return {value,label:option.label??option.text??value,description:option.description??null};
}

export function shiftDate(value,days){
 if(!timeRangeAnswer(value,'9999-12-31'))return '';
 const result=new Date(value+'T00:00:00Z');result.setUTCDate(result.getUTCDate()+days);
 return result.toISOString().slice(0,10);
}
export function periodDrafts(periods){return (periods||[]).map(period=>({...period,end:shiftDate(period.end_exclusive,-1)}));}
export function periodAnswer(drafts,comparison=false){
 if(!Array.isArray(drafts)||drafts.length<1||drafts.length>8||(comparison&&drafts.length<2))return null;
 const result=drafts.map((draft,index)=>{const dates=timeRangeAnswer(draft.start,shiftDate(draft.end,1));return dates?{label:draft.label||`时间段 ${index+1}`,...dates}:null;});
 return result.every(Boolean)?result:null;
}
export function periodsText(periods){return (periods||[]).map((period,index)=>`${periods.length>1?(index===0?'基期':'对比期 '+index)+'：':''}${period.start} 至 ${shiftDate(period.end_exclusive,-1)}（含）`).join('；');}

export function progressSeconds(startedAt,now=Date.now()){
 const start=Date.parse(startedAt);return Number.isFinite(start)?Math.max(0,Math.floor((now-start)/1000)):0;
}
