export const recordLabel=record=>({recomputed_match:'重算验证一致',declared_only:'静态生成声明',executed:'成功查询来源',committed:'任务提交已确认',SOURCE_CHANGED:'结构已变化'})[record?.status]||'已记录关系';
export function connectedFields(edges,anchor){
 if(!anchor?.column)return null;
 const keys=new Set([`${anchor.node}.${anchor.column}`]);let changed=true;
 while(changed){changed=false;for(const edge of edges){const a=`${edge.source}.${edge.source_column}`,b=`${edge.target}.${edge.target_column}`;if(keys.has(a)||keys.has(b)){if(!keys.has(a)||!keys.has(b))changed=true;keys.add(a);keys.add(b);}}}
 return keys;
}
export function lineageLayout(data,{level='table',direction='both',field='',anchor,kind='all'}={}){
 const records=new Map(data.records.filter(r=>kind==='all'||(kind==='query'?r.kind==='query':r.kind!=='query')).map(r=>[r.id,r]));
 const edges=data.table_edges.filter(e=>records.has(e.record_id)),signed=new Map([[data.focus,0]]);
 const distances=upstream=>{const result=new Map([[data.focus,0]]),queue=[data.focus];for(let i=0;i<queue.length;i++){const current=queue[i];for(const edge of edges){if((upstream?edge.target:edge.source)!==current)continue;const next=upstream?edge.source:edge.target;if(!result.has(next)){result.set(next,result.get(current)+1);queue.push(next);}}}return result;};
 if(direction!=='downstream')for(const [id,distance] of distances(true))if(distance)signed.set(id,-distance);
 if(direction!=='upstream')for(const [id,distance] of distances(false))if(distance&&!signed.has(id))signed.set(id,distance);
 let visible=data.nodes.filter(n=>signed.has(n.id));
 const ids=new Set(visible.map(n=>n.id));
 const columnEdges=data.column_edges.filter(e=>records.has(e.record_id)&&ids.has(e.source)&&ids.has(e.target));
 const connected=connectedFields(columnEdges,anchor),positions=new Map(),ports=new Map();
 const matchingColumns=node=>node.columns.filter(c=>(!field||`${c.label} ${c.name}`.toLowerCase().includes(field.toLowerCase()))&&(!connected||connected.has(`${node.id}.${c.name}`)));
 if(level==='column'&&(field||connected))visible=visible.filter(n=>n.id===data.focus||matchingColumns(n).length);
 const shown=new Set(visible.map(n=>n.id)),tableEdges=edges.filter(e=>shown.has(e.source)&&shown.has(e.target));
 const ranks=[...new Set(visible.map(n=>signed.get(n.id)))].sort((a,b)=>a-b),offsets=new Map(ranks.map(r=>[r,32]));
 const nodes=visible.map(node=>{const rank=signed.get(node.id),x=32+ranks.indexOf(rank)*350,y=offsets.get(rank);
  const columns=level==='column'?matchingColumns(node):[];
  const height=92+columns.length*36;offsets.set(rank,y+height+38);
  positions.set(node.id,{x,y,width:280,height});columns.forEach((c,i)=>ports.set(`${node.id}.${c.name}`,{x,y:y+92+i*36+18}));
  return {...node,x,y,width:280,height,columns};
 });
 return {nodes,tableEdges,columnEdges:columnEdges.filter(e=>ports.has(`${e.source}.${e.source_column}`)&&ports.has(`${e.target}.${e.target_column}`)),positions,ports,
  records,width:Math.max(680,ranks.length*350),height:Math.max(330,...offsets.values()),connected};
}
