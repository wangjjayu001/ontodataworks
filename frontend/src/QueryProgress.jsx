import React,{useEffect,useState} from 'react';
import {progressSeconds} from './queryInput.js';

export function QueryProgress({progress,startedAt,onCancel,busy=false}){
 const [now,setNow]=useState(Date.now());useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
 const phase=progress?.phase||'QUEUED',steps=[['QUEUED','已受理'],['CONNECTING','连接模型'],['MODEL_RUNNING',progress?.section==='metadata'?'核对表字段':'识别问题'],['VALIDATING','核对结果']],position=Math.max(0,steps.findIndex(([key])=>key===phase)),seconds=progressSeconds(progress?.started_at||startedAt,now);
 return <section className="sq-query-progress" aria-label="问题识别进度" aria-busy="true">
  <div className="sq-progress-heading"><strong role="status" aria-live="polite">{progress?.label||'问题已受理，等待识别'}</strong><span aria-hidden="true">已等待 {seconds} 秒</span></div>
  <ol>{steps.map(([key,label],index)=><li key={key} className={index<position?'done':index===position?'current':''} aria-current={index===position?'step':undefined}><i/>{label}</li>)}</ol>
  <div className="sq-progress-foot"><p>{seconds>=15?'模型仍在处理，完成后会自动显示结果。你可以继续等待或取消本次识别。':'识别完成后会展示指标、时间及需要确认的内容。'}</p>{onCancel&&<button type="button" disabled={busy} onClick={onCancel}>取消本次识别</button>}</div>
 </section>;
}
