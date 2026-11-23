import test from 'node:test';
import assert from 'node:assert/strict';
import {completePropertyMappings,deriveBindingDraft,prepareSemanticSave,semanticSaveProblems} from '../src/semanticMaintenance.js';
const er={workspace_id:1,asset_type:'entity',asset_id:'entity:order',revision:1};
const mr={workspace_id:1,asset_type:'metric',asset_id:'metric:orders',revision:1};
const pr=id=>({entity_ref:er,property_id:id});
const ds={catalog_snapshot_id:'cat',datasource_id:'1',dataset_id:'orders'};
const field=id=>({...ds,field_id:id});
const metric={...mr,name:'订单数',definition:'订单去重',spec:{expression:{op:'call',function:'count_distinct',args:[{op:'property',property_ref:pr('id')}]},time_policy:{kind:'EVENT_RANGE',property_ref:pr('time')},grain:{description:'一单',property_refs:[pr('id')]},allowed_dimension_refs:[]}};
const context={assets:[{asset_ref:mr,asset_revision:metric}]};
const binding=()=>({name:'订单实现',definition:'实现',asset_type:'binding',spec:{metric_ref:mr,base_dataset_ref:ds,physical_grain:{description:'物理一单',key_field_refs:[field('id')]},property_mappings:[{property_ref:pr('id'),expression:{op:'field',field_ref:field('id')}},{property_ref:pr('time'),expression:{op:'field',field_ref:field('time')}}],joins:[],base_join_ids:[],dimension_implementations:[],parameter_bindings:[],base_filter:{preserved:true},freshness_sla_minutes:30}});

test('calculation and event mapping derive from exact logical properties while preserving business constraints',()=>{
 const original=binding(),next=deriveBindingDraft(original,context);
 assert.equal(next.spec.calculation.function,'count_distinct');assert.deepEqual(next.spec.time_field_ref,field('time'));
 assert.deepEqual(next.spec.base_filter,{preserved:true});assert.equal(next.spec.freshness_sla_minutes,30);
 assert.deepEqual(next.spec.physical_grain,original.spec.physical_grain);assert.deepEqual(next.spec.base_join_ids,[]);
 assert.equal(original.spec.calculation,undefined);assert.equal(next.spec.parameter_bindings.length,2);
});
test('derive refuses missing mapping instead of inventing a similarly named field',()=>{
 const source=binding();source.spec.property_mappings.pop();
 assert.throws(()=>deriveBindingDraft(source,context),/时间属性/);
 const pending=completePropertyMappings(source,metric,context);assert.equal(pending.spec.property_mappings.length,2);assert.equal(pending.spec.property_mappings[1].expression.field_ref,null);
});
test('cross-table derivation requires one path and never silently chooses between two joins',()=>{
 const source=binding(),other={...ds,dataset_id:'events'};source.spec.property_mappings[1].expression.field_ref={...other,field_id:'time'};
 assert.throws(()=>deriveBindingDraft(source,context),/没有.*连接路径/);
 source.spec.joins=[{join_id:'event',from_dataset_ref:ds,to_dataset_ref:other}];
 assert.deepEqual(deriveBindingDraft(source,context).spec.base_join_ids,['event']);
 source.spec.joins.push({join_id:'other_event',from_dataset_ref:ds,to_dataset_ref:other});
 assert.throws(()=>deriveBindingDraft(source,context),/多条连接路径/);
});
test('save generates dependency declarations and preserves extra explicit dimension fields',()=>{
 const edited={...metric,aliases:[{text:''},{text:'订单量',language:'zh-CN',kind:'EXACT_ALIAS',scope:'当前资产'}],spec:{...metric.spec,metric_kind:'DERIVED',depends_on_metric_refs:[{...mr,revision:999}],distinct_property_refs:[]}};
 const saved=prepareSemanticSave(edited);assert.equal(saved.aliases.length,1);assert.equal(saved.spec.metric_kind,'ATOMIC');assert.deepEqual(saved.spec.depends_on_metric_refs,[]);assert.deepEqual(saved.spec.distinct_property_refs,[pr('id')]);
 const source=binding();source.aliases=[];source.spec.dimension_implementations=[{expression:{op:'field',field_ref:field('region')},required_field_refs:[field('policy_guard')]}];
 const result=prepareSemanticSave(source);assert.deepEqual(result.spec.dimension_implementations[0].required_field_refs,[field('policy_guard'),field('region')]);
 assert.equal(semanticSaveProblems(source).length,0);
});
