export const assetGroups={business:['metric','dimension'],modeling:['entity','relation','term','domain'],physical:['binding'],sources:['source','evidence','claim']};
export const typeTitles={metric:'业务指标',dimension:'分析维度',entity:'业务实体',term:'业务术语',relation:'业务关系',domain:'业务域',binding:'物理映射',source:'来源材料',claim:'业务主张',evidence:'证据片段'};
export const valueTitles={UNKNOWN:'尚未确认',DECLARED:'已声明（待核验）',VERIFIED:'已核验',ENTITY:'业务实体',EVENT:'业务事件',POSTGRESQL:'PostgreSQL 数据源',LOCAL_FIXTURE:'本地结构夹具',COUNT:'数量',CURRENCY:'金额',NUMBER:'数值',RATIO:'比例',DURATION:'时长',CONFIRMED:'已确认',SYNTHETIC_ONLY:'仅合成数据',EVENT_RANGE:'业务事件区间',SNAPSHOT_DAY:'快照日',BUSINESS_DATE:'业务日期',PROCESSING_TIME:'处理时间',CURRENT:'当前值',AS_OF:'当时值',SCD2:'历史拉链',IMMUTABLE:'不可变事实',IDENTIFIER:'业务标识',ATTRIBUTE:'普通属性',MEASURE:'度量',STATUS:'业务状态',EVENT_TIME:'事件发生时间',SNAPSHOT_DATE:'快照日期',SOURCE:'源库时间（不转换）',ATOMIC:'原子指标',DERIVED:'派生指标',NO_DATA:'返回无数据',ZERO_IF_COMPLETE:'覆盖完整时返回零',REJECT_ROW_AND_WARN:'拒绝空值行并提示',IGNORE:'忽略空值',PROPAGATE:'传播空值',UNKNOWN_BUCKET:'归入未知分组',EXCLUDE:'排除',ERROR:'阻止计算',ADDITIVE:'可加',NON_ADDITIVE:'不可加',SEMI_ADDITIVE:'半可加',PUBLIC:'公开',INTERNAL:'内部',SENSITIVE:'敏感',BUSINESS_SPEC:'业务制度/说明',CATALOG:'数据目录',SQL:'SQL 材料',TECHNICAL_QA:'技术检测材料',FEEDBACK:'使用反馈',BUSINESS_APPROVED:'业务已批准',TECHNICAL_ONLY:'仅技术材料',UNREVIEWED:'尚未审核',BUSINESS_TEXT:'业务原文',SQL_LINEAGE:'SQL 血缘',CATALOG_OBSERVATION:'目录观测',BUSINESS_DEFINITION:'业务定义主张',MAPPING_ASSERTION:'映射主张',TECHNICAL_OBSERVATION:'技术观察',SUPPORT:'支持',REFUTE:'反驳',EXACT_ALIAS:'同义词',ABBREVIATION:'简称',EQUIVALENT:'等价',BROADER:'上位概念',NARROWER:'下位概念',RELATED:'相关',AMBIGUOUS:'存在歧义',INTENT_TIME_START:'已确认时间起点',INTENT_TIME_END:'已确认时间终点',INTENT_SNAPSHOT_DATE:'已确认快照日',INTENT_FILTER:'已确认筛选条件'};
export const showValue=value=>value===null||value===undefined?'未确认':valueTitles[value]??String(value);
export const exactRefKey=ref=>ref?`${ref.workspace_id??''}|${ref.asset_type??''}|${ref.asset_id??''}|${ref.revision??''}`:'';
export const physicalKey=ref=>ref?`${ref.catalog_snapshot_id??''}|${ref.datasource_id??''}|${ref.dataset_id??''}|${ref.field_id??''}`:'';
export const assetFor=(ref,context)=>context.assets?.find(item=>exactRefKey(item.asset_ref)===exactRefKey(ref));
export const assetLabel=(ref,context)=>{const item=assetFor(ref,context);return item?`${item.asset_revision.name} · r${ref.revision}`:ref?`引用不可用：${ref.asset_id} · r${ref.revision}`:'未选择';};
export function catalogDatasets(context){return (context.snapshots||[]).flatMap(snapshot=>(snapshot.datasets||[]).map(dataset=>({snapshot,dataset,ref:{catalog_snapshot_id:snapshot.catalog_snapshot_id,datasource_id:dataset.datasource_id,dataset_id:dataset.dataset_id}})));}
export function catalogFields(context){return catalogDatasets(context).flatMap(entry=>(entry.dataset.fields||[]).map(field=>({...entry,field,ref:{...entry.ref,field_id:field.field_id},label:field.business_definition?.name_zh||field.name||field.field_id})));}
export const datasetFor=(ref,context)=>catalogDatasets(context).find(entry=>physicalKey(entry.ref)===physicalKey(ref&&{...ref,field_id:undefined}));
export const fieldFor=(ref,context)=>catalogFields(context).find(entry=>physicalKey(entry.ref)===physicalKey(ref));
export const datasetLabel=(ref,context)=>{const entry=datasetFor(ref,context);return entry?entry.dataset.business_definition?.name_zh||entry.dataset.logical_locator:ref?`目录引用不可用：${ref.dataset_id}`:'未选择数据表';};
export const fieldLabel=(ref,context)=>{const entry=fieldFor(ref,context);return entry?`${entry.label}（${entry.dataset.logical_locator}.${entry.field.field_id}）`:ref?`字段引用不可用：${ref.field_id}`:'未选择字段';};
export const propertyLabel=(ref,context)=>{const item=assetFor(ref?.entity_ref,context),property=item?.asset_revision.spec.properties?.find(p=>p.property_id===ref?.property_id);return property?`${item.asset_revision.name} · ${property.name}`:ref?`属性引用不可用：${ref.property_id}`:'未选择业务属性';};
export function expressionLabel(expression,context){if(!expression)return '未定义';if(expression.op==='field')return fieldLabel(expression.field_ref,context);if(expression.op==='property')return propertyLabel(expression.property_ref,context);if(expression.op==='metric')return assetLabel(expression.metric_ref,context);if(expression.op==='literal')return String(expression.value);if(expression.op==='parameter')return `需求参数：${expression.name}`;if(expression.op==='call')return `${({count:'计数',count_distinct:'去重计数',avg:'平均值',sum:'求和',min:'最小值',max:'最大值',coalesce:'空值替换',date_trunc:'日期截断'})[expression.function]||expression.function}（${(expression.args||[]).map(arg=>expressionLabel(arg,context)).join('，')}）`;if(expression.op==='binary')return `${expressionLabel(expression.left,context)} ${expression.operator} ${expressionLabel(expression.right,context)}`;return '暂不支持编辑的计算结构';}
export function maintenanceIssues(asset,context){const issues=[];if(!asset.name?.trim())issues.push('缺少业务名称');if(!asset.definition?.trim())issues.push('缺少业务定义');if(!asset.owner_ids?.length)issues.push('未指派业务负责人');if(asset.policy_ref?.resolution!=='RESOLVED')issues.push('访问政策尚未确认');if(!asset.provenance_refs?.length&&asset.asset_type!=='source')issues.push('未关联来源或证据');const spec=asset.spec||{};if(asset.asset_type==='metric'){if(!assetFor(spec.entity_ref,context))issues.push('统计实体不可解析');if(!spec.time_policy?.property_ref)issues.push('业务时间属性未确认');if(!spec.time_policy?.timezone)issues.push('业务时区未确认');if(spec.unit?.kind==='UNKNOWN'||spec.unit?.authority==='UNKNOWN')issues.push('单位或权威性未确认');if(spec.unit?.kind==='CURRENCY'&&!spec.unit.currency)issues.push('币种未确认');}if(asset.asset_type==='binding'){if(!assetFor(spec.metric_ref,context))issues.push('业务指标引用不可解析');if(!datasetFor(spec.base_dataset_ref,context))issues.push('数据表目录引用不可解析');if(!spec.time_field_ref)issues.push('未映射业务时间字段');if(!spec.property_mappings?.length)issues.push('业务属性与字段尚未映射');if(!spec.physical_grain?.key_field_refs?.length)issues.push('未选择物理粒度键');for(const map of spec.property_mappings||[])if(map.expression?.op==='field'&&!fieldFor(map.expression.field_ref,context))issues.push(`字段不可解析：${map.expression.field_ref?.field_id}`);if((spec.joins||[]).some(join=>join.expected_cardinality==='UNKNOWN'||join.null_key_policy==='UNKNOWN'||!join.equivalence_evidence_refs?.length))issues.push('关联基数、空键规则或等价证据待核验');}return [...new Set(issues)];}
export function createSemanticDraft(template,randomId=globalThis.crypto.randomUUID()){
 const asset=JSON.parse(JSON.stringify(template));
 Object.assign(asset,{asset_id:asset.asset_type+':'+randomId,revision:1,name:'',definition:'',domain_ref:null,aliases:[],owner_ids:[],steward_ids:[],provenance_refs:[],validity:{from:null,to:null},policy_ref:{policy_id:null,revision:null,resolution:'UNKNOWN'}});
 const property={op:'property',property_ref:null},field={op:'field',field_ref:null};
 const specs={
  metric:{metric_kind:'ATOMIC',entity_ref:null,grain:{description:'',property_refs:[]},expression:{op:'call',function:'count_distinct',args:[property]},depends_on_metric_refs:[],distinct_property_refs:[],default_filter:null,allowed_dimension_refs:[],forbidden_dimension_sets:[],max_dimensions:0,time_policy:{kind:'UNKNOWN',property_ref:null,timezone:null,interval:'UNKNOWN',history_policy:'UNKNOWN'},unit:{kind:'UNKNOWN',code:null,currency:null,scale:null,authority:'UNKNOWN'},additivity:{across_time:'UNKNOWN',across_dimensions:'UNKNOWN'},null_policy:'UNKNOWN',empty_result_policy:'UNKNOWN'},
  binding:{metric_ref:null,base_dataset_ref:null,calculation:{op:'call',function:'count_distinct',args:[field]},property_mappings:[],physical_grain:{description:'',key_field_refs:[]},base_join_ids:[],joins:[],dimension_implementations:[],base_filter:null,time_field_ref:null,partition_field_refs:[],coverage:{start:null,end_exclusive:null,data_as_of:null,evidence_refs:[]},freshness_sla_minutes:null,verification_rule_ids:[],applicability:{scope_filter:null,description:''},parameter_bindings:[]},
  entity:{kind:'ENTITY',properties:[],business_key:[],grain:''},dimension:{entity_ref:null,property_id:'',value_type:'STRING',null_member:'UNKNOWN',parent_dimension_refs:[],value_dictionary_ref:null,history_policy:'UNKNOWN'},
  domain:{parent_ref:null,scope_notes:''},term:{target_refs:[],target_relation:'AMBIGUOUS',excluded_meanings:[]},relation:{from_entity_ref:null,to_entity_ref:null,predicate:'',from_role:'',to_role:'',cardinality:'UNKNOWN',logical_key_pairs:[]},
  source:{source_kind:'BUSINESS_SPEC',uri:'',source_revision:'',content_sha256:'',classification:'INTERNAL',authority:'UNREVIEWED'},claim:{subject_ref:null,claim_kind:'BUSINESS_DEFINITION',statement:'',condition:null,evidence_links:[]},evidence:{source_ref:null,locator:{kind:'JSON_POINTER',pointer:''},excerpt:'',fragment_sha256:'',evidence_kind:'BUSINESS_TEXT',observed_at:null,assertion:''}
 };
 asset.spec=specs[asset.asset_type];return asset;
}

export function planCatalogMigration(binding,targetSnapshotId,context){
 if(binding.asset_type!=='binding')throw new Error('仅物理映射可迁移目录引用。');
 const snapshot=(context.snapshots||[]).find(item=>item.catalog_snapshot_id===targetSnapshotId);
 if(!snapshot)throw new Error('目标目录快照不可用。');
 const replacements=[],oldSnapshotIds=new Set();
 function visit(value){
  if(Array.isArray(value))return value.map(visit);
  if(!value||typeof value!=='object')return value;
  if(value.catalog_snapshot_id&&value.datasource_id&&value.dataset_id){
   if(value.catalog_snapshot_id===targetSnapshotId)return {...value};
   const oldDataset=datasetFor(value,context)?.dataset;
   const targetDataset=snapshot.datasets?.find(dataset=>dataset.datasource_id===value.datasource_id&&dataset.dataset_id===value.dataset_id);
   if(!oldDataset||!targetDataset)throw new Error(`数据表 ${value.dataset_id} 在原目录或目标目录不可解析，不能迁移。`);
   for(const key of ['connector_kind','namespace','physical_name','schema_fingerprint'])if(typeof oldDataset[key]!=='string'||!oldDataset[key]||oldDataset[key]!==targetDataset[key])throw new Error(`数据表 ${value.dataset_id} 的 ${key} 未知或已变化，必须重新核验物理映射。`);
   if(value.field_id){
    const oldField=oldDataset.fields?.find(field=>field.field_id===value.field_id),targetField=targetDataset.fields?.find(field=>field.field_id===value.field_id);
    if(!oldField||!targetField||!oldField.name||!oldField.data_type||oldField.name!==targetField.name||oldField.data_type!==targetField.data_type||oldField.nullable!==targetField.nullable)throw new Error(`字段 ${value.dataset_id}.${value.field_id} 缺失或类型/可空性发生变化，不能迁移。`);
   }
   oldSnapshotIds.add(value.catalog_snapshot_id);
   replacements.push({dataset_id:value.dataset_id,field_id:value.field_id||null,from_snapshot_id:value.catalog_snapshot_id,to_snapshot_id:targetSnapshotId});
   return {...value,catalog_snapshot_id:targetSnapshotId};
  }
  return Object.fromEntries(Object.entries(value).map(([key,child])=>[key,visit(child)]));
 }
 const migrated={...binding,spec:visit(binding.spec)};
 return {asset:migrated,targetSnapshotId,oldSnapshotIds:[...oldSnapshotIds],replacements,count:replacements.length};
}
