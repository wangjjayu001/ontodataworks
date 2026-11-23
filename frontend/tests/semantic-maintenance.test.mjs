import test from 'node:test';
import assert from 'node:assert/strict';
import {assetFor,createSemanticDraft,expressionLabel,fieldLabel,maintenanceIssues,planCatalogMigration} from '../src/semanticUiModel.js';

const entityRef={workspace_id:3,asset_type:'entity',asset_id:'entity:order',revision:1};
const metricRef={workspace_id:3,asset_type:'metric',asset_id:'metric:orders',revision:1};
const dsRef={catalog_snapshot_id:'catalog:old',datasource_id:'2',dataset_id:'orders'};
const oldField={...dsRef,field_id:'order_id'};
const context={assets:[{asset_ref:entityRef,asset_revision:{name:'订单',asset_type:'entity',spec:{properties:[{property_id:'order_id',name:'订单编号'}]}}},{asset_ref:{...entityRef,revision:2},asset_revision:{name:'新订单实体',asset_type:'entity',spec:{properties:[{property_id:'order_id',name:'新订单编码'}]}}},{asset_ref:metricRef,asset_revision:{name:'订单量',asset_type:'metric',spec:{}}}],snapshots:[{catalog_snapshot_id:'catalog:old',datasets:[{datasource_id:'2',dataset_id:'orders',logical_locator:'dwd.orders',connector_kind:'POSTGRESQL',namespace:'dwd',physical_name:'orders',schema_fingerprint:'same-fingerprint',fields:[{field_id:'order_id',name:'order_id',data_type:'text',nullable:false,business_definition:{name_zh:'订单编号',definition:'旧目录中的业务唯一单号'}}]}]},{catalog_snapshot_id:'catalog:new',datasets:[{datasource_id:'2',dataset_id:'orders',logical_locator:'dwd.orders',connector_kind:'POSTGRESQL',namespace:'dwd',physical_name:'orders',schema_fingerprint:'same-fingerprint',fields:[{field_id:'order_id',name:'order_id',data_type:'text',nullable:false,business_definition:{name_zh:'订单业务编码',definition:'新修订'}}]}]}]};

test('historical business and physical references retain their exact names after newer revisions exist',()=>{
 assert.equal(assetFor(entityRef,context).asset_revision.name,'订单');
 assert.equal(fieldLabel(oldField,context),'订单编号（dwd.orders.order_id）');
 assert.equal(fieldLabel({...oldField,catalog_snapshot_id:'catalog:new'},context),'订单业务编码（dwd.orders.order_id）');
 assert.equal(expressionLabel({op:'call',function:'count_distinct',args:[{op:'property',property_ref:{entity_ref:entityRef,property_id:'order_id'}}]},context),'去重计数（订单 · 订单编号）');
});

test('new draft never inherits the template existing business meaning, mapped fields, provenance or approval assumptions',()=>{
 const template={workspace_id:3,asset_id:'binding:old',asset_type:'binding',revision:9,name:'旧口径',definition:'旧范围',owner_ids:['1'],provenance_refs:[metricRef],policy_ref:{resolution:'RESOLVED'},spec:{metric_ref:metricRef,base_dataset_ref:dsRef,calculation:{op:'call',function:'avg',args:[{op:'field',field_ref:oldField}]}}};
 const draft=createSemanticDraft(template,'fixed-new-id');
 assert.equal(draft.asset_id,'binding:fixed-new-id');assert.equal(draft.name,'');assert.equal(draft.definition,'');assert.equal(draft.revision,1);assert.equal(draft.spec.metric_ref,null);assert.equal(draft.spec.base_dataset_ref,null);assert.deepEqual(draft.provenance_refs,[]);assert.deepEqual(draft.owner_ids,[]);assert.equal(draft.policy_ref.resolution,'UNKNOWN');assert.equal(template.spec.base_dataset_ref.dataset_id,'orders');
});

test('incomplete physical mapping exposes precise maintenance gaps instead of treating filled template as executable',()=>{
 const draft=createSemanticDraft({workspace_id:3,asset_type:'binding'},'empty');
 const issues=maintenanceIssues(draft,context);
 assert.ok(issues.includes('业务指标引用不可解析'));assert.ok(issues.includes('数据表目录引用不可解析'));assert.ok(issues.includes('未映射业务时间字段'));assert.ok(issues.includes('业务属性与字段尚未映射'));assert.ok(issues.includes('未选择物理粒度键'));
});

test('explicit catalog migration moves exact physical references only after physical identities match',()=>{
 const binding={asset_type:'binding',spec:{metric_ref:metricRef,base_dataset_ref:dsRef,calculation:{op:'call',function:'count_distinct',args:[{op:'field',field_ref:oldField}]},property_mappings:[{property_ref:{entity_ref:entityRef,property_id:'order_id'},expression:{op:'field',field_ref:oldField}}]}};
 const plan=planCatalogMigration(binding,'catalog:new',context);
 assert.equal(plan.count,3);assert.deepEqual(plan.oldSnapshotIds,['catalog:old']);assert.equal(plan.asset.spec.calculation.args[0].field_ref.catalog_snapshot_id,'catalog:new');assert.deepEqual(plan.asset.spec.metric_ref,metricRef);assert.deepEqual(plan.asset.spec.property_mappings[0].property_ref.entity_ref,entityRef);assert.equal(binding.spec.base_dataset_ref.catalog_snapshot_id,'catalog:old');
 const changed=structuredClone(context);changed.snapshots[1].datasets[0].schema_fingerprint='different';
 assert.throws(()=>planCatalogMigration(binding,'catalog:new',changed),/schema_fingerprint/);
 const deleted=structuredClone(context);deleted.snapshots[1].datasets[0].fields=[];
 assert.throws(()=>planCatalogMigration(binding,'catalog:new',deleted),/缺失/);
});
