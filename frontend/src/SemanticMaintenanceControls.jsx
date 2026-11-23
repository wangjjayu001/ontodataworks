import React,{useState} from 'react';
import {FileText,Plus,Trash2} from 'lucide-react';

export function EvidenceAttachment({items=[],onChange}){
 const [title,setTitle]=useState(''),[raw,setRaw]=useState(''),[kind,setKind]=useState('BUSINESS_SPEC'),[classification,setClassification]=useState('INTERNAL'),[error,setError]=useState('');
 if(!onChange)return null;
 async function readFile(event){
  const file=event.target.files?.[0];if(!file)return;
  try{
   if(file.size>2_000_000)throw new Error('文件超过 2MB，请选取较小的原文范围。');
   const value=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await file.arrayBuffer());
   setRaw(value);setTitle(file.name);setError('');
  }catch(e){setError(e.message);}
 }
 function add(){
  if(!title.trim()||!raw.trim())return;
  if(new TextEncoder().encode(raw).byteLength>2_000_000){setError('本次材料超过 2MB。');return;}
  onChange([...items,{client_id:crypto.randomUUID(),title:title.trim(),text:raw,source_kind:kind,classification}]);
  setTitle('');setRaw('');setError('');
 }
 return <div className="wide sm-attachments">
  {items.length>0&&<div className="sm-pending-list" aria-label="待保存依据">{items.map((item,index)=><div key={item.client_id||index}><FileText size={16}/><span><strong>{item.title}</strong><small>随本次修订一同保存 · 尚未审批</small></span><button type="button" className="icon-btn" aria-label={'移除依据 '+item.title} onClick={()=>onChange(items.filter((_,i)=>i!==index))}><Trash2 size={15}/></button></div>)}</div>}
  <details className="sw-more"><summary>附上新依据 · 上传或粘贴文本</summary><p className="sem-form-hint">支持 UTF-8 的 TXT、Markdown、JSON 或 SQL 文本。系统随资产一起保存来源、原文和证据片段，自动生成定位及摘要；取消编辑不会留下新附件。最多 3 份，总计 2MB。</p>
   <div className="sw-form-grid">
    <label className="field"><span>选择文本文件</span><input type="file" accept=".txt,.md,.json,.sql,text/plain,text/markdown,application/json" onChange={readFile}/></label>
    <label className="field"><span>依据名称</span><input value={title} maxLength={200} onChange={e=>setTitle(e.target.value)} placeholder="例如：订单统计口径说明"/></label>
    <label className="field"><span>依据类别</span><select value={kind} onChange={e=>setKind(e.target.value)}><option value="BUSINESS_SPEC">业务制度 / 口径说明</option><option value="TECHNICAL_QA">技术核验材料</option><option value="SQL">SQL 材料</option><option value="CATALOG">目录观测</option><option value="FEEDBACK">使用反馈</option></select></label>
    <label className="field"><span>材料敏感级别</span><select value={classification} onChange={e=>setClassification(e.target.value)}><option value="INTERNAL">内部</option><option value="PUBLIC">公开</option><option value="SENSITIVE">敏感</option></select></label>
    <label className="field wide"><span>材料原文 / 依据片段</span><textarea rows={5} value={raw} onChange={e=>setRaw(e.target.value)} placeholder="粘贴支持当前业务定义的原文，保留原始内容。"/></label>
   </div>{error&&<p role="alert" className="sem-error">{error}</p>}<button type="button" className="btn" disabled={!title.trim()||!raw.trim()||items.length>=3} onClick={add}><Plus size={14}/>加入本次编辑</button>
  </details>
 </div>;
}

export function PreservedCapability({title,description,value}){
 const configured=value!==null&&value!==undefined&&(!Array.isArray(value)||value.length>0);
 return <div className="sm-capability wide"><strong>{title}</strong><p>{description}</p>{configured&&<details><summary>查看保留的现有配置</summary><pre>{JSON.stringify(value,null,2)}</pre></details>}</div>;
}
