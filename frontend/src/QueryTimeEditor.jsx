import React,{useEffect,useState} from 'react';
import {periodDrafts,periodAnswer} from './queryInput.js';

export function TimePeriodEditor({periods=[],comparison=false,confirmed=false,busy=false,onConfirm}){
 const signature=JSON.stringify(periods),[drafts,setDrafts]=useState(()=>periods.length?periodDrafts(periods):Array.from({length:comparison?2:1},()=>({label:'',start:'',end:''})));
 useEffect(()=>{setDrafts(periods.length?periodDrafts(periods):Array.from({length:comparison?2:1},()=>({label:'',start:'',end:''})));},[signature,comparison]);
 const answer=periodAnswer(drafts,comparison),dirty=JSON.stringify(answer)!==signature;
 function edit(index,key,value){setDrafts(current=>current.map((period,i)=>i===index?{...period,[key]:value}:period));}
 return <section className="sq-time-editor" aria-label="确认或修改时间段">
  <div className="sq-time-heading"><strong>{comparison?'确认对比时间段':'确认统计时间'}</strong><span>{confirmed&&!dirty?'已确认':'请核对日期'}</span></div>
  <p>{comparison?'各时间段分别保留，第一个为基期。可修改日期、调整基期或添加时间段。':'已识别的时间可直接确认，也可修改为任意起止日期。'}</p>
  <div className="sq-period-list">{drafts.map((period,index)=><fieldset key={index} disabled={busy}>
   <legend>{comparison?(index===0?'基期':`对比期 ${index}`):`时间段 ${index+1}`}{period.label&&!['基期','对比期'].includes(period.label)&&<small> · {period.label}</small>}</legend>
   <div className="sq-period-dates"><label>开始日期（含）<input aria-label={`${index===0&&comparison?'基期':'时间段 '+(index+1)}开始日期`} type="date" value={period.start||''} onInput={event=>edit(index,'start',event.target.value)} onChange={event=>edit(index,'start',event.target.value)}/></label><label>结束日期（含）<input aria-label={`${index===0&&comparison?'基期':'时间段 '+(index+1)}结束日期`} type="date" value={period.end||''} onInput={event=>edit(index,'end',event.target.value)} onChange={event=>edit(index,'end',event.target.value)}/></label></div>
   <div className="sq-period-actions">{comparison&&index>0&&<button type="button" onClick={()=>setDrafts(current=>[current[index],...current.filter((_,i)=>i!==index)])}>设为基期</button>}{drafts.length>(comparison?2:1)&&<button type="button" onClick={()=>setDrafts(current=>current.filter((_,i)=>i!==index))}>删除此段</button>}</div>
  </fieldset>)}</div>
  <div className="sq-time-actions"><button type="button" disabled={busy||drafts.length>=8} onClick={()=>setDrafts(current=>[...current,{label:'',start:'',end:''}])}>添加时间段</button><button type="button" className="sq-confirm-button" disabled={busy||!answer||(confirmed&&!dirty)} onClick={()=>onConfirm?.(answer)}>{confirmed&&!dirty?'时间已确认':confirmed?'保存时间修改':'确认这些时间范围'}</button></div>
  {!answer&&<small role="status">请填写有效日期；对比问题至少保留两个时间段。</small>}
 </section>;
}
