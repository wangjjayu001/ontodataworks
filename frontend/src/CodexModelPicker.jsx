import React,{useEffect,useRef,useState} from 'react';
import {Check,ChevronDown,RefreshCw,Search,Settings2,X} from 'lucide-react';

const effortLabels={none:'无',minimal:'最低',low:'低',medium:'中',high:'高',xhigh:'很高',max:'最高',ultra:'超高'};
export function defaultModelSelection(catalog){
 const models=catalog?.models||[];
 const model=models.find(item=>item.isDefault)||models[0];
 return model?{model:model.id||model.model,reasoningEffort:model.defaultReasoningEffort||null,serviceTier:model.defaultServiceTier||null}:null;
}
export function CodexModelPicker({value,onChange,catalog,loading,error,onRefresh}){
 const [open,setOpen]=useState(false),[filter,setFilter]=useState('');
 const root=useRef(null),trigger=useRef(null),filterInput=useRef(null);
 const models=catalog?.models||[],selected=models.find(item=>(item.id||item.model)===value?.model);
 const visible=models.filter(item=>`${item.displayName||item.display_name||item.id} ${item.id||item.model}`.toLowerCase().includes(filter.toLowerCase().trim()));
 const efforts=selected?.supportedReasoningEfforts||[],tiers=selected?.serviceTiers||[];
 useEffect(()=>{if(!open)return;filterInput.current?.focus();function outside(event){if(!root.current?.contains(event.target))setOpen(false);}function escape(event){if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setOpen(false);trigger.current?.focus();}}document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};},[open]);
 function selectModel(model){const effort=(model.supportedReasoningEfforts||[]).some(item=>(item.reasoningEffort||item)===value?.reasoningEffort)?value.reasoningEffort:model.defaultReasoningEffort||null;const serviceTier=(model.serviceTiers||[]).some(item=>(item.id||item)===value?.serviceTier)?value.serviceTier:model.defaultServiceTier||null;onChange({model:model.id||model.model,reasoningEffort:effort,serviceTier});}
 return <div className="sq-model-picker" ref={root}>
  <button type="button" ref={trigger} className={`sq-model-trigger ${open?'is-open':''}`} aria-label="选择模型与推理强度" aria-expanded={open} aria-controls="sq-model-config" aria-haspopup="dialog" onClick={()=>{setOpen(!open);setFilter('');}}><Settings2 size={14}/><span className="sq-selected-model">{loading?'加载模型…':selected?.displayName||selected?.id||'选择模型'}</span><span className="sq-selected-effort">{value?.reasoningEffort&&(effortLabels[value.reasoningEffort]||value.reasoningEffort)}{value?.serviceTier==='priority'?' · Fast':''}</span><ChevronDown size={12}/></button>
  {open&&<section id="sq-model-config" className="sq-model-popover" role="dialog" aria-label="模型配置">
   <header><div><strong>模型与推理</strong><span>服务端可用模型目录</span></div><button type="button" aria-label="关闭模型配置" onClick={()=>{setOpen(false);trigger.current?.focus();}}><X size={16}/></button></header>
   <label className="sq-model-filter"><Search size={14}/><input ref={filterInput} value={filter} onChange={event=>setFilter(event.target.value)} placeholder="筛选模型名称或 ID" aria-label="筛选模型"/></label>{error&&<p role="alert" className="sq-inline-error">{error}</p>}
   <div className="sq-model-options" role="radiogroup" aria-label="可选模型">{visible.length?visible.map(model=>{const id=model.id||model.model;return <label className={`sq-model-option ${id===value?.model?'selected':''}`} key={id}><input type="radio" name="codex-model" checked={id===value?.model} onChange={()=>selectModel(model)}/><span><strong>{model.displayName||id}{model.isDefault&&<small>默认</small>}</strong><code>{id}</code></span>{id===value?.model&&<Check size={14}/>}</label>;}):<p className="sq-model-empty">{loading?'正在读取目录…':'没有可用模型，请刷新目录。'}</p>}</div>
   {selected&&<><div className="sq-model-effort"><label htmlFor="sq-model-reasoning">推理强度</label><select id="sq-model-reasoning" value={value?.reasoningEffort||''} onChange={event=>onChange({...value,reasoningEffort:event.target.value})} disabled={!efforts.length}>{efforts.map(item=>{const effort=item.reasoningEffort||item;return <option value={effort} key={effort}>{effortLabels[effort]||effort}</option>;})}</select></div>{tiers.length>0&&<div className="sq-model-effort"><label htmlFor="sq-model-speed">速度档位</label><select id="sq-model-speed" value={value?.serviceTier||''} onChange={event=>onChange({...value,serviceTier:event.target.value||null})}><option value="">标准</option>{tiers.map(item=><option key={item.id||item} value={item.id||item}>{item.name||item.displayName||item.id||item}</option>)}</select></div>}<div className="sq-model-modalities"><span>输入能力</span>{(selected.inputModalities||[]).map(item=><span className="sq-model-capability" key={item}>{item==='text'?'文本':item==='image'?'图像':item}</span>)}</div></>}
   <footer><button type="button" onClick={onRefresh} disabled={loading}><RefreshCw size={12}/>刷新目录</button><span>选择应用于下一条问题</span>{(catalog?.captured_at||catalog?.capturedOn||catalog?.updated_at)&&<small>更新于 {new Date(catalog.captured_at||catalog.capturedOn||catalog.updated_at).toLocaleString('zh-CN')}</small>}</footer>
  </section>}
 </div>;
}
