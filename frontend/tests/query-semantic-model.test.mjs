import test from 'node:test';
import assert from 'node:assert/strict';
import {buildPinnedContext,requirementSemanticView,semanticReferenceList,snapshotIds} from '../src/querySemanticModel.js';

const ref=(type,id,revision=1)=>({workspace_id:10,asset_type:type,asset_id:`${type}:${id}`,revision});
const er=ref('entity','session'),mr=ref('metric','identity',2),br=ref('binding','identity',3),dr=ref('dimension','region',2);
const fref=(field_id,snapshot='catalog-fixed')=>({catalog_snapshot_id:snapshot,datasource_id:'7',dataset_id:'dwd.sessions',field_id});
const property=property_id=>({entity_ref:er,property_id});
const wrap=asset_revision=>({asset_revision,asset_ref:ref(asset_revision.asset_type,asset_revision.asset_id.split(':')[1],asset_revision.revision),content_hash:'fixed'});
const entity=wrap({...er,name:'浏览会话',spec:{properties:[{property_id:'customer_id',name:'客户账户标识'},{property_id:'started_at',name:'会话开始时间'}]}});
const metric=wrap({...mr,name:'访问账户数',spec:{expression:{op:'call',function:'count_distinct',args:[{op:'property',property_ref:property('customer_id')}]},time_policy:{property_ref:property('started_at')},grain:{description:'账户身份'},unit:{kind:'COUNT'}}});
const binding=wrap({...br,name:'访问账户映射',spec:{base_dataset_ref:{...fref('customer_id'),field_id:undefined},calculation:{op:'call',function:'count_distinct',args:[{op:'field',field_ref:fref('customer_id')}]},property_mappings:[{property_ref:property('customer_id'),expression:{op:'field',field_ref:fref('customer_id')}}],time_field_ref:fref('started_at')}});
const dimension=wrap({...dr,name:'买家所在地区新释义',spec:{}});
const req={bundle:{selected_metric_ref:mr,selected_binding_ref:br,requested_dimension_refs:[dr],closure_refs:[...[[er,'ASSET'],[mr,'ASSET'],[br,'ASSET'],[dr,'ASSET'],[fref('customer_id'),'FIELD'],[fref('started_at'),'FIELD']].map(([target_ref,kind])=>({kind,target_ref}))]}};
const run={requirements:[req]};
const snapshot={catalog_snapshot_id:'catalog-fixed',datasets:[{datasource_id:'7',dataset_id:'dwd.sessions',namespace:'dwd',physical_name:'dwd_sessions',logical_locator:'dwd.sessions',fields:[{field_id:'customer_id',name:'customer_key',business_definition:{name_zh:'访问客户账户'}},{field_id:'started_at',name:'start_time',business_definition:{name_zh:'会话开始时间'}}]}]};

test('run reference inventory deduplicates exact refs and keeps physical snapshot identity',()=>{
 assert.equal(semanticReferenceList(run).length,4);
 assert.deepEqual(snapshotIds(run),['catalog-fixed']);
});

test('pinned context excludes newer and older revisions with the same stable ID',()=>{
 const oldMetric=wrap({...metric.asset_revision,revision:1,name:'旧访问会话数'}),newDimension=wrap({...dimension.asset_revision,revision:3,name:'尚未发布维度名'});
 const context=buildPinnedContext(run,[oldMetric,metric,binding,entity,dimension,newDimension],[snapshot,{...snapshot,catalog_snapshot_id:'catalog-new'}]);
 assert.equal(context.assets.length,4);
 const view=requirementSemanticView(req,context);
 assert.equal(view.metric.label,'访问账户数 · r2');
 assert.equal(view.dimensions[0].label,'买家所在地区新释义 · r2');
 assert.deepEqual(context.snapshots.map(s=>s.catalog_snapshot_id),['catalog-fixed']);
});

test('missing exact revision stays unresolved even when another revision is available',()=>{
 const context=buildPinnedContext(run,[wrap({...metric.asset_revision,revision:1}),binding,entity],[snapshot]);
 const view=requirementSemanticView(req,context);
 assert.equal(view.metric.resolved,false);
 assert.match(view.metric.label,/引用不可用.*r2/);
 assert.equal(view.dimensions[0].resolved,false);
 assert.equal(context.missingRefs.length,2);
});

test('mapping and time chain use fixed Binding and actual physical column names',()=>{
 const context=buildPinnedContext(run,[metric,binding,entity,dimension],[snapshot]);
 const view=requirementSemanticView(req,context);
 assert.match(view.logicalExpression,/去重计数.*客户账户标识/);
 assert.match(view.mappingRows[0].logical,/客户账户标识/);
 assert.match(view.mappingRows[0].physical,/dwd\.dwd_sessions\.customer_key/);
 assert.match(view.timeMapping.logical,/会话开始时间/);
 assert.match(view.timeMapping.physical,/dwd\.dwd_sessions\.start_time/);
 assert.deepEqual(view.snapshotIds,['catalog-fixed']);
});

test('server response asset revision must match requested response ref exactly',()=>{
 const mismatched={...metric,asset_revision:{...metric.asset_revision,revision:99,name:'错误修订'}};
 const context=buildPinnedContext(run,[{data:mismatched},binding,entity],[snapshot]);
 assert.equal(requirementSemanticView(req,context).metric.resolved,false);
});
