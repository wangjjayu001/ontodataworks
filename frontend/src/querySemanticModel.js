import {assetFor,assetLabel,exactRefKey,expressionLabel,fieldFor,propertyLabel} from './semanticUiModel.js';

const bundleOf=requirement=>requirement?.bundle||requirement?.retrieval_bundle;
const unwrap=value=>value?.data??value;
const isAssetRef=value=>value&&typeof value==='object'&&value.workspace_id!==undefined&&typeof value.asset_id==='string'&&typeof value.asset_type==='string'&&Number.isInteger(value.revision);
function uniqueRefs(refs){return [...new Map(refs.filter(isAssetRef).map(ref=>[exactRefKey(ref),ref])).values()];}

export function semanticReferenceList(run){
 return uniqueRefs((run?.requirements||[]).flatMap(requirement=>{
  const bundle=bundleOf(requirement);if(!bundle)return [];
  return [bundle.selected_metric_ref,bundle.selected_binding_ref,...bundle.requested_dimension_refs||[],...(bundle.closure_refs||[]).filter(item=>item.kind==='ASSET').map(item=>item.target_ref)];
 }));
}

export function snapshotIds(run){
 return [...new Set((run?.requirements||[]).flatMap(requirement=>(bundleOf(requirement)?.closure_refs||[]).filter(item=>item.kind==='FIELD'||item.kind==='DATASET').map(item=>item.target_ref?.catalog_snapshot_id)).filter(Boolean))];
}

export function buildPinnedContext(run,assetResponses=[],snapshots=[]){
 const refs=semanticReferenceList(run),wanted=new Set(refs.map(exactRefKey)),neededSnapshots=new Set(snapshotIds(run));
 const assets=[...new Map(assetResponses.map(unwrap).filter(item=>item?.asset_revision&&wanted.has(exactRefKey(item.asset_ref))&&exactRefKey(item.asset_revision)===exactRefKey(item.asset_ref)).map(item=>[exactRefKey(item.asset_ref),item])).values()];
 const catalog=Array.isArray(snapshots)?snapshots:unwrap(snapshots)?.items||[];
 return {assets,snapshots:catalog.filter(snapshot=>neededSnapshots.has(snapshot.catalog_snapshot_id)),requiredRefs:refs,missingRefs:refs.filter(ref=>!assets.some(item=>exactRefKey(item.asset_ref)===exactRefKey(ref)))};
}

function fieldRefs(expression){
 if(!expression||typeof expression!=='object')return [];
 if(expression.op==='field'&&expression.field_ref)return [expression.field_ref];
 return Object.values(expression).flatMap(value=>Array.isArray(value)?value.flatMap(fieldRefs):typeof value==='object'?fieldRefs(value):[]);
}
function physicalLabel(expression,context){
 if(expression?.op==='field'){
  const entry=fieldFor(expression.field_ref,context);
  return entry?`${entry.label}（${entry.dataset.namespace}.${entry.dataset.physical_name}.${entry.field.name}）`:`字段引用不可用：${expression.field_ref?.dataset_id||''}.${expression.field_ref?.field_id||''}`;
 }
 if(expression?.op==='call')return `${({count:'计数',count_distinct:'去重计数',avg:'平均值',sum:'求和',min:'最小值',max:'最大值'})[expression.function]||expression.function}（${(expression.args||[]).map(arg=>physicalLabel(arg,context)).join('，')}）`;
 if(expression?.op==='binary')return `${physicalLabel(expression.left,context)} ${expression.operator} ${physicalLabel(expression.right,context)}`;
 return expressionLabel(expression,context);
}

export function requirementSemanticView(requirement,context={assets:[],snapshots:[]}){
 const bundle=bundleOf(requirement)||{};
 const resolve=ref=>{const item=ref?assetFor(ref,context):undefined;return {ref:ref||null,item,asset:item?.asset_revision,label:assetLabel(ref,context),resolved:!!item};};
 const metric=resolve(bundle.selected_metric_ref),binding=resolve(bundle.selected_binding_ref),spec=binding.asset?.spec,metricSpec=metric.asset?.spec;
 const dimensions=(bundle.requested_dimension_refs||[]).map(resolve);
 const mappingRows=(spec?.property_mappings||[]).map(mapping=>({propertyRef:mapping.property_ref,logical:propertyLabel(mapping.property_ref,context),expression:mapping.expression,physical:physicalLabel(mapping.expression,context),fieldRefs:fieldRefs(mapping.expression)}));
 const ids=new Set((bundle.closure_refs||[]).map(item=>item.target_ref?.catalog_snapshot_id).filter(Boolean));
 if(spec?.base_dataset_ref?.catalog_snapshot_id)ids.add(spec.base_dataset_ref.catalog_snapshot_id);
 for(const row of mappingRows)for(const ref of row.fieldRefs)ids.add(ref.catalog_snapshot_id);
 if(spec?.time_field_ref?.catalog_snapshot_id)ids.add(spec.time_field_ref.catalog_snapshot_id);
 return {metric,binding,dimensions,snapshotIds:[...ids],snapshots:(context.snapshots||[]).filter(snapshot=>ids.has(snapshot.catalog_snapshot_id)),mappingRows,
  timeMapping:{propertyRef:metricSpec?.time_policy?.property_ref||null,fieldRef:spec?.time_field_ref||null,logical:metricSpec?.time_policy?.property_ref?propertyLabel(metricSpec.time_policy.property_ref,context):'业务时间属性未确认',physical:spec?.time_field_ref?physicalLabel({op:'field',field_ref:spec.time_field_ref},context):'物理时间字段未确认'},
  logicalExpression:metricSpec?.expression?expressionLabel(metricSpec.expression,context):'指标逻辑口径尚未回源',physicalExpression:spec?.calculation?physicalLabel(spec.calculation,context):'物理计算尚未回源',grain:metricSpec?.grain||null,unit:metricSpec?.unit||null,timePolicy:metricSpec?.time_policy||null};
}
