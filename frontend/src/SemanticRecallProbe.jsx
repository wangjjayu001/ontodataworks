import React,{useState} from 'react';
import {Search} from 'lucide-react';
import {assetLabel} from './semanticUiModel.js';

export function SemanticRecallProbe({command,context}){
 const [question,setQuestion]=useState(''),[result,setResult]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function run(event){
  event.preventDefault();if(!question.trim()||busy)return;
  setBusy(true);setError('');setResult(null);
  try{const response=await command('/retrieve',{intent:{question:question.trim()}});setResult((response.data??response).bundle);}catch(e){setError(e.message);}finally{setBusy(false);}
 }
 const candidates=(result?.candidates||[]).filter(item=>item.role==='metric').slice(0,5);
 return <details className="sm-recall"><summary><Search size={15}/>试问召回 · 检查已激活快照</summary><p>输入真实问法，查看当前快照选中了什么。未发布草稿不会自动进入检索；此处只检查召回，不执行查询。</p><form onSubmit={run}><label className="field"><span>试问问题</span><input value={question} onChange={e=>setQuestion(e.target.value)} maxLength={8000} placeholder="例如：上个月的购买人数有多少？"/></label><button className="btn primary" disabled={busy||!question.trim()}>{busy?'检查中…':'检查召回'}</button></form>{error&&<p role="alert" className="sem-error">{error}</p>}{result&&<div className="sm-recall-result" role="status"><strong>{result.selected_metric_ref?'选中指标：'+assetLabel(result.selected_metric_ref,context):'没有选中唯一指标'}</strong><span>下一步：{({NEED_CLARIFICATION:'需要澄清',GOVERNANCE_REQUIRED:'需要补充治理',NEED_CONFIRMATION:'需要确认',BLOCKED_EVIDENCE:'依据不满足',BLOCKED_PERMISSION:'权限不满足'})[result.next_action]||result.next_action}</span><ol>{candidates.map(item=><li key={item.target_ref.asset_id}>{assetLabel(item.target_ref,context)}</li>)}</ol>{!candidates.length&&<p>未找到指标候选。</p>}<details><summary>本次快照与覆盖说明</summary><p>发布：{result.release_id}</p>{result.coverage.map((item,index)=><p key={index}>{item.reason}</p>)}</details></div>}</details>;
}
