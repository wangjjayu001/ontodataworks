import React,{useCallback,useEffect,useState} from 'react';
import {CheckCircle2,RefreshCw} from 'lucide-react';
import './pipeline.css';

const names={PREVIEW:'待发布',RUNNING:'发布中',SUCCEEDED:'发布成功',FAILED:'发布失败',UNKNOWN:'待核对'};
const date=value=>new Date(value).toLocaleString('zh-CN',{hour12:false});
const Button=({children,primary=false,...props})=><button className={`btn ${primary?'primary':''}`} {...props}>{children}</button>;
const Status=({value})=><span className={`badge ${value==='SUCCEEDED'?'green':['UNKNOWN','FAILED'].includes(value)?'orange':'blue'}`}>{names[value]||value}</span>;

export function ModelPublishing({models,selectedModel,view,request,notify,role,go,onPublished}){
 const [sources,setSources]=useState([]),[records,setRecords]=useState([]),[modelId,setModelId]=useState(selectedModel?String(selectedModel):''),[sourceId,setSourceId]=useState(''),[plan,setPlan]=useState(null),[result,setResult]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[loaded,setLoaded]=useState(false);
 const load=useCallback(async()=>{
  const [connections,history,details]=await Promise.all([request('/pipeline/sources'),request('/pipeline/publications'),request('/datasources')]);
  setSources(connections.map(s=>({...details.find(d=>d.id===s.id),...s})));setRecords(history);setLoaded(true);
 },[request]);
 useEffect(()=>{load().catch(e=>setError(e.message));},[load]);
 useEffect(()=>{if(selectedModel){setModelId(String(selectedModel));setPlan(null);setResult(null);}},[selectedModel]);
 const model=models.find(m=>String(m.id)===modelId);
 const eligible=sources.filter(s=>s.purpose==='ddl'&&(!model||(s.schemas||[]).includes(model.spec.namespace||'public')));
 useEffect(()=>{
  if(!loaded)return;
  if(!eligible.some(s=>String(s.id)===sourceId))setSourceId(eligible.length===1?String(eligible[0].id):'');
 },[loaded,modelId,sources,sourceId]);
 async function action(fn){
  setBusy(true);setError('');
  try{return await fn();}catch(e){setError(e.message);return null;}finally{setBusy(false);}
 }
 function choose(set,value){set(value);setPlan(null);setResult(null);setError('');}
 async function preview(){await action(async()=>{
  setPlan(null);setResult(null);
  const next=await request('/pipeline/publications/preview','POST',{model_id:Number(modelId),datasource_id:Number(sourceId)});setPlan(next);
  await load();
 });}
 async function execute(){await action(async()=>{
  const next=await request(`/pipeline/publications/${plan.id}/execute`,'POST',{});setPlan(null);setResult(next);await load();
  if(next.status==='SUCCEEDED'){notify('建表发布成功，实际结构已核对并登记数据地图');onPublished?.();}
 });}
 async function reconcile(row){await action(async()=>{
  const next=await request(`/pipeline/publications/${row.id}/reconcile`,'POST',{});setResult(next);await load();
  if(next.status==='SUCCEEDED')onPublished?.();
 });}
 function inspect(row){
  setModelId(String(row.model_id));setSourceId(String(row.datasource_id));setError('');
  if(row.status==='PREVIEW'){setPlan(row);setResult(null);}else{setPlan(null);setResult(row);}
  if(view==='history')go('model/publish');
 }
 const sourceName=id=>sources.find(s=>s.id===id)?.name||`连接 #${id}`;
 return <>
  {error&&<div className="form-error" role="alert">{error}<p>更新模型或目标表后，请重新预检。</p></div>}
  {view!=='history'&&<section className="panel model-publish-form">
   <ol className="publish-steps"><li>选择模型与连接</li><li>预检结构差异</li><li>发布并核对</li></ol>
   <div className="form-grid">
    <label className="field"><span>发布模型</span><select aria-label="发布模型" disabled={busy} value={modelId} onChange={e=>choose(setModelId,e.target.value)}><option value="">选择已保存的模型</option>{models.map(m=><option key={m.id} value={m.id}>{m.name} · v{m.revision}</option>)}</select></label>
    <label className="field"><span>建表连接</span><select aria-label="建表连接" disabled={busy} value={sourceId} onChange={e=>choose(setSourceId,e.target.value)}><option value="">选择建表专用连接</option>{eligible.map(s=><option key={s.id} value={s.id}>{s.name} · {s.database}</option>)}</select></label>
   </div>
   {model&&<p className="muted">目标表 <code>{model.spec.namespace||'public'}.{model.code}</code> · 模型 v{model.revision} · {model.spec.fields.length} 个字段。仅建空表或新增可空字段。</p>}
   {loaded&&!eligible.length&&<div className="notice">{sources.some(s=>s.purpose==='ddl')?'没有覆盖目标 Schema 的建表连接。':'当前空间没有建表专用连接。'}<button className="text-link" onClick={()=>go('datasource')}>配置数据源</button><span>，在 </span><button className="text-link" onClick={()=>go('pipelines')}>数据任务</button><span> 登记“物理建表”用途后即可预检。</span></div>}
   <Button primary disabled={busy||role==='viewer'||!modelId||!sourceId} onClick={preview}>{busy?'处理中…':'预检并预览 SQL'}</Button>
   {role!=='admin'&&<p className="muted">实际建表发布由管理员执行。</p>}
  </section>}
  {view!=='history'&&plan&&<section className="panel model-publish-plan" aria-label="建表发布预检">
   <h2>预检通过 · {plan.plan.mode==='CREATE'?'创建新表':plan.plan.commands.length?'新增字段':'结构已一致'}</h2>
   <p>{sourceName(plan.datasource_id)} → <code>{plan.plan.namespace}.{plan.plan.table_name}</code> · 固定模型 v{plan.model_revision}</p>
   <pre className="code-block">{plan.plan.commands.join(';\n')||'结构已一致，无需修改；发布将核对并登记目录。'}</pre>
   <p className="muted">执行前重新核对模型修订和目标结构。删列、改类型及改键会被阻止。</p>
   <Button primary disabled={busy||role!=='admin'} onClick={execute}>{busy?'发布中…':'确认发布并核对'}</Button>
  </section>}
  {view!=='history'&&result&&<section className="panel model-publish-result" role="status">
   <div className="publish-result-heading">{result.status==='SUCCEEDED'&&<CheckCircle2 size={22}/>}<h2><Status value={result.status}/></h2></div>
   <p><code>{result.plan.namespace}.{result.plan.table_name}</code> · 模型 v{result.model_revision} · {sourceName(result.datasource_id)}</p>
   {result.status==='SUCCEEDED'?<p>实际表结构、字段类型及主外键已回读匹配，数据地图已更新。</p>:<p>{result.evidence.error||'请核对目标表和连接。'}{['UNKNOWN','RUNNING'].includes(result.status)&&'核对操作只检查实际结构，不重复执行建表 SQL。'}</p>}
   <div className="head-actions">{result.status==='SUCCEEDED'&&<Button onClick={()=>go('assets')}>查看数据地图</Button>}<Button onClick={()=>go('model/history')}>查看发布记录</Button>{['UNKNOWN','RUNNING'].includes(result.status)&&role==='admin'&&<Button disabled={busy} onClick={()=>reconcile(result)}>核对实际结构</Button>}</div>
   <details><summary>发布证据</summary><pre className="code-block">{JSON.stringify(result.evidence,null,2)}</pre></details>
  </section>}
  {view==='history'&&<section className="panel list-panel">
   <div className="list-toolbar"><strong>建表发布记录</strong><Button disabled={busy} onClick={()=>action(load)}><RefreshCw size={14}/>刷新</Button></div>
   <div className="table-scroll"><table><thead><tr>{['模型 / 修订','目标表','连接','状态','时间','操作'].map(n=><th key={n}>{n}</th>)}</tr></thead><tbody>{records.map(r=><tr key={r.id}><td>{models.find(m=>m.id===r.model_id)?.name||`模型 #${r.model_id}`} · v{r.model_revision}</td><td><code>{r.plan.namespace}.{r.plan.table_name}</code></td><td>{sourceName(r.datasource_id)}</td><td><Status value={r.status}/></td><td>{date(r.created_at)}</td><td><button className="text-link" onClick={()=>inspect(r)}>{r.status==='PREVIEW'?'继续发布':'查看结果'}</button></td></tr>)}</tbody></table></div>
   {!records.length&&<div className="empty"><h3>{loaded?'暂无发布记录':'正在加载…'}</h3><p>保存模型后，选择“发布建表”完成预检与发布。</p></div>}
  </section>}
 </>;
}
