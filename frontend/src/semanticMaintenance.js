import {assetFor,exactRefKey,physicalKey} from './semanticUiModel.js';

const clone=value=>JSON.parse(JSON.stringify(value));
const unique=(items,key)=>[...new Map(items.filter(Boolean).map(item=>[key(item),item])).values()];
export const propertyKey=ref=>ref?`${exactRefKey(ref.entity_ref)}|${ref.property_id}`:'';

export function expressionReferences(expression){
 const refs={properties:[],fields:[],metrics:[],distinct:[]};
 function visit(node){
  if(!node||typeof node!=='object')return;
  if(node.op==='property'&&node.property_ref)refs.properties.push(node.property_ref);
  if(node.op==='field'&&node.field_ref)refs.fields.push(node.field_ref);
  if(node.op==='metric'&&node.metric_ref)refs.metrics.push(node.metric_ref);
  if(node.op==='call'&&node.function==='count_distinct')for(const arg of node.args||[])if(arg?.op==='property'&&arg.property_ref)refs.distinct.push(arg.property_ref);
  for(const [key,value] of Object.entries(node))if(!key.endsWith('_ref')){
   if(Array.isArray(value))value.forEach(visit);else if(value&&typeof value==='object')visit(value);
  }
 }
 visit(expression);
 return {properties:unique(refs.properties,propertyKey),fields:unique(refs.fields,physicalKey),metrics:unique(refs.metrics,exactRefKey),distinct:unique(refs.distinct,propertyKey)};
}

export function prepareSemanticSave(asset){
 const next=clone(asset);
 next.name=next.name.trim();next.definition=next.definition.trim();
 next.aliases=(next.aliases||[]).filter(alias=>alias.text.trim());
 if(next.asset_type==='metric'){
  const refs=expressionReferences(next.spec.expression);
  next.spec.depends_on_metric_refs=refs.metrics;
  next.spec.distinct_property_refs=refs.distinct;
  next.spec.metric_kind=refs.metrics.length?'DERIVED':'ATOMIC';
 }
 if(next.asset_type==='binding')for(const implementation of next.spec.dimension_implementations||[]){
  // Preserve explicit additional dependencies; only add missing AST fields.
  implementation.required_field_refs=unique([...(implementation.required_field_refs||[]),...expressionReferences(implementation.expression).fields],physicalKey);
 }
 return next;
}

export function requiredBusinessProperties(metric,context){
 const properties=[];const seen=new Set();
 function visit(asset,depth=0){
  if(!asset||depth>12||seen.has(exactRefKey(asset)))return;
  seen.add(exactRefKey(asset));
  const refs=expressionReferences(asset.spec.expression);properties.push(...refs.properties);
  for(const target of refs.metrics)visit(assetFor(target,context)?.asset_revision,depth+1);
 }
 visit(metric);
 properties.push(...(metric?.spec.grain?.property_refs||[]),metric?.spec.time_policy?.property_ref);
 for(const target of metric?.spec.allowed_dimension_refs||[]){
  const dimension=assetFor(target,context)?.asset_revision;
  if(dimension?.spec.entity_ref&&dimension.spec.property_id)properties.push({entity_ref:dimension.spec.entity_ref,property_id:dimension.spec.property_id});
 }
 return unique(properties,propertyKey);
}

export function completePropertyMappings(binding,metric,context){
 const next=clone(binding),known=new Set(next.spec.property_mappings.map(item=>propertyKey(item.property_ref)));
 for(const property_ref of requiredBusinessProperties(metric,context))if(!known.has(propertyKey(property_ref)))next.spec.property_mappings.push({property_ref:clone(property_ref),expression:{op:'field',field_ref:null}});
 return next;
}

export function deriveBindingDraft(binding,context){
 const next=clone(binding),spec=next.spec,metric=assetFor(spec.metric_ref,context)?.asset_revision;
 if(!metric)throw new Error('先选择业务指标的准确修订。');
 if(!spec.base_dataset_ref)throw new Error('先选择基础数据表。');
 const mapping=new Map();
 for(const item of spec.property_mappings||[]){
  if(!item.property_ref)throw new Error('属性映射中还有未选择的业务属性。');
  const key=propertyKey(item.property_ref);
  if(mapping.has(key))throw new Error('同一业务属性有重复映射，请先合并。');
  mapping.set(key,item.expression);
 }
 function substitute(node,ancestors=[]){
  if(!node||typeof node!=='object')return node;
  if(node.op==='metric'){
   const key=exactRefKey(node.metric_ref),dependency=assetFor(node.metric_ref,context)?.asset_revision;
   if(!dependency||ancestors.includes(key)||ancestors.length>=12)throw new Error('派生指标依赖不可解析、循环或超过展开上限。');
   if(dependency.spec.default_filter!==null||JSON.stringify(dependency.spec.time_policy)!==JSON.stringify(metric.spec.time_policy))throw new Error('派生依赖的筛选或时间口径不一致，不能自动生成。');
   return substitute(dependency.spec.expression,[...ancestors,key]);
  }
  if(node.op==='property'){
   const mapped=mapping.get(propertyKey(node.property_ref));
   if(!mapped||mapped.op==='field'&&!mapped.field_ref)throw new Error('计算所需的业务属性尚未映射到字段。');
   return clone(mapped);
  }
  return Object.fromEntries(Object.entries(node).map(([key,value])=>[key,Array.isArray(value)?value.map(v=>substitute(v,ancestors)):value&&typeof value==='object'?substitute(value,ancestors):value]));
 }
 spec.calculation=substitute(metric.spec.expression);
 const time=mapping.get(propertyKey(metric.spec.time_policy?.property_ref));
 if(time?.op!=='field'||!time.field_ref)throw new Error('业务时间属性还没有映射到明确的时间字段。');
 spec.time_field_ref=clone(time.field_ref);
 const datasetKey=target=>physicalKey(target&&{...target,field_id:undefined});
 function joinsFor(fields){
  const needed=new Set();
  for(const field of fields){
   if(field.catalog_snapshot_id!==spec.base_dataset_ref.catalog_snapshot_id||field.datasource_id!==spec.base_dataset_ref.datasource_id)throw new Error('字段跨数据源或目录快照，请先核对目录映射。');
   const paths=[];
   function walk(current,path,seen){
    if(paths.length>1)return;
    if(current===datasetKey(field)){paths.push(path);return;}
    for(const join of spec.joins||[]){
     const target=datasetKey(join.to_dataset_ref);
     if(datasetKey(join.from_dataset_ref)===current&&join.to_dataset_ref&&!seen.has(target))walk(target,[...path,join.join_id],new Set([...seen,target]));
    }
   }
   walk(datasetKey(spec.base_dataset_ref),[],new Set([datasetKey(spec.base_dataset_ref)]));
   if(paths.length!==1)throw new Error(paths.length?'字段有多条连接路径，请在连接设置中明确选择。':'所需字段没有从基础表可达的连接路径。');
   paths[0].forEach(id=>needed.add(id));
  }
  return [...needed];
 }
 spec.base_join_ids=[...new Set([...(spec.base_join_ids||[]),...joinsFor([...expressionReferences(spec.calculation).fields,spec.time_field_ref])])];
 for(const impl of spec.dimension_implementations||[]){
  const dimension=assetFor(impl.dimension_ref,context)?.asset_revision;
  if(!dimension)throw new Error('维度实现尚未选择可解析的维度。');
  const mapped=mapping.get(propertyKey({entity_ref:dimension.spec.entity_ref,property_id:dimension.spec.property_id}));
  if(!mapped)throw new Error('维度的业务属性尚未映射。');
  impl.expression=clone(mapped);
  const fields=expressionReferences(mapped).fields;
  impl.required_field_refs=unique([...(impl.required_field_refs||[]),...fields],physicalKey);
  impl.required_join_ids=[...new Set([...(impl.required_join_ids||[]),...joinsFor(fields).filter(id=>!spec.base_join_ids.includes(id))])];
 }
 // Explicit custom parameters remain intact; the supported time template is
 // offered only when none were configured. Compiler/planner remain authoritative.
 if(!spec.parameter_bindings?.length)spec.parameter_bindings=[{name:'time_start',data_type:'TIMESTAMP',source:'INTENT_TIME_START',required:true},{name:'time_end',data_type:'TIMESTAMP',source:'INTENT_TIME_END',required:true}];
 return next;
}

export function semanticSaveProblems(asset){
 const issues=[];if(!asset.name?.trim())issues.push('填写业务名称');if(!asset.definition?.trim())issues.push('填写业务定义与边界');
 if((asset.aliases||[]).some(alias=>alias.text.trim()&&(!alias.scope?.trim()||!alias.language?.trim())))issues.push('补齐现有别名的语言或范围');
 const spec=asset.spec||{};
 if(asset.asset_type==='metric'){
  if(!spec.entity_ref)issues.push('选择统计实体');
  if(!spec.grain?.description?.trim()||!spec.grain?.property_refs?.length||spec.grain.property_refs.some(ref=>!ref))issues.push('确认业务粒度说明与标识');
  function incomplete(node){if(!node||typeof node!=='object')return false;return ['property','field','metric'].includes(node.op)&&!node[node.op+'_ref']||Object.values(node).some(value=>Array.isArray(value)?value.some(incomplete):value&&typeof value==='object'&&incomplete(value));}
  if(!spec.expression||incomplete(spec.expression))issues.push('选择计算所需的业务属性');
 }
 if(asset.asset_type==='binding'){
  if(!spec.metric_ref)issues.push('选择业务指标');if(!spec.base_dataset_ref)issues.push('选择基础数据表');
  if(!spec.physical_grain?.description?.trim()||!spec.physical_grain?.key_field_refs?.length||spec.physical_grain.key_field_refs.some(ref=>!ref))issues.push('确认物理粒度说明与键');
  if((spec.property_mappings||[]).some(item=>!item.property_ref||!item.expression||item.expression.op==='field'&&!item.expression.field_ref))issues.push('补齐属性映射的字段');
 }
 return issues;
}
