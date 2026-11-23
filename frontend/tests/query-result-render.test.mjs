import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const require=createRequire(import.meta.url);
const esbuild=createRequire(require.resolve('vite'))('esbuild');
const compiled=await esbuild.build({entryPoints:[fileURLToPath(new URL('../src/SmartQuery.jsx',import.meta.url))],bundle:true,platform:'node',format:'cjs',write:false,loader:{'.css':'empty'},external:['react','lucide-react'],logLevel:'silent'});
const loaded={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(require,loaded,loaded.exports);
const {ResultTable,IntentSummary,SemanticPath,MetadataDiscovery,TimePeriodEditor,QueryProgress}=loaded.exports;
const render=result=>renderToStaticMarkup(React.createElement(ResultTable,{result}));

test('comparison editor shows independent inclusive dates with a reversible base period',()=>{
 const periods=[{label:'2025年5月',start:'2025-05-01',end_exclusive:'2025-06-01'},{label:'2025年6月',start:'2025-06-01',end_exclusive:'2025-07-01'}];
 const html=renderToStaticMarkup(React.createElement(TimePeriodEditor,{periods,comparison:true}));
 for(const text of ['确认对比时间段','2025-05-01','2025-05-31','2025-06-01','2025-06-30','基期','对比期 1','设为基期','添加时间段','确认这些时间范围'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('2025-07-01'));
 const summary=renderToStaticMarkup(React.createElement(IntentSummary,{intent:{metric_terms:['ROI'],time_periods:periods}}));
 assert.ok(summary.includes('基期：2025-05-01'));
 assert.ok(summary.includes('对比期 1：2025-06-01'));
 assert.ok(!summary.includes('需要补充'));
});

test('model progress exposes actual phase, elapsed seconds and a cancellation action without percentages',()=>{
 const html=renderToStaticMarkup(React.createElement(QueryProgress,{progress:{phase:'MODEL_RUNNING',label:'正在识别问题、指标和时间',started_at:new Date(Date.now()-26000).toISOString()},onCancel:()=>{}}));
 assert.ok(html.includes('问题识别进度'));
 assert.ok(html.includes('正在识别问题、指标和时间'));
 assert.ok(html.includes('已等待 26 秒'));
 assert.ok(html.includes('取消本次识别'));
 assert.ok(!html.includes('%'));
});

test('intent summary exposes accepted clarification answers beside the business constraints',()=>{
 const html=renderToStaticMarkup(React.createElement(IntentSummary,{intent:{metric_terms:['销售额'],clarification_answers:[{slot:'comparison_direction',question:'比较方向',answer:'2024年为基期，比较2025年'},{slot:'growth_measure',question:'增长方式',answer:'两者都要'}]}}));
 assert.ok(html.includes('已补充的口径'));
 assert.ok(html.includes('2024年为基期，比较2025年'));
 assert.ok(html.includes('两者都要'));
 assert.ok(!html.includes('comparison_direction'));
});

test('metadata discovery shows fixed-snapshot Chinese fields without implying a confirmed metric',()=>{
 const html=renderToStaticMarkup(React.createElement(MetadataDiscovery,{discovery:{match_status:'RETRIEVAL_CANDIDATE',tables:[{catalog_snapshot_id:'catalog-1',datasource_id:'3',dataset_id:'orders',table_label:'订单明细',logical_locator:'dwd.orders',schema_fingerprint:'abc',fields:[{field_id:'order_id',name:'order_id',name_zh:'订单编号',definition:'订单唯一标识',data_type:'text',semantic_role:'IDENTIFIER',is_primary:true}]}]}}));
 assert.ok(html.includes('检索候选'));
 assert.ok(html.includes('订单明细'));
 assert.ok(html.includes('订单编号'));
 assert.ok(html.includes('主键'));
});

test('result validation and data lineage render Chinese check details and source evidence',()=>{
 const html=render({columns:['metric_value'],rows:[[3]],row_count:1,data_as_of:'2025-12-31',
  validation:{status:'PASS',status_label:'技术校验通过',business_approved:false,check_items:[
   {code:'RESULT_SHAPE',label:'结果列结构',status:'PASS',detail:'结果列与方案一致'},
   {code:'TEMPORARY_CALIBER',label:'临时口径状态',status:'NOT_APPROVED',detail:'仅本次本地验证'}
  ]},
  provenance:{tables:[{asset_id:7,table_label:'订单明细',physical_object:'dwd.orders',datasource_name:'电商验证库',
   schema_fingerprint:'fingerprint',verified:true,catalog_snapshot:{catalog_snapshot_id:'catalog-1'},data_batch:{batch:'batch-3'},
   fields:[{field_id:'order_id',name_zh:'订单编号',physical_name:'order_id',definition:'订单唯一标识',data_type:'text',semantic_role:'IDENTIFIER',classification:'INTERNAL'}]}],
   temporary_candidate:{name:'按订单编号计数'},evidence:[{name:'已支付口径',source_name:'业务规则文档',assertion:'仅计算已支付订单',excerpt:'已支付订单定义',source_revision:'r2',locator:{pointer:'/rules/1'}}],
   verification_receipts:[{verification_id:'verify-1',rule_id:'BUSINESS_PROVENANCE',outcome:'PASS',reason:'来源可回查'}]}
 });
 assert.ok(html.includes('结果校验'));
 assert.ok(html.includes('业务口径尚未审批'));
 assert.ok(html.includes('订单明细'));
 assert.ok(html.includes('本次使用字段'));
 assert.ok(html.includes('订单唯一标识'));
 assert.ok(html.includes('已支付口径'));
 assert.ok(html.includes('已支付订单定义'));
 assert.ok(html.includes('来源原文与业务主张'));
 assert.ok(html.includes('机器核验回执'));
});

test('result warnings show actual message and excluded-row count including zero',()=>{
 const html=render({columns:['metric_value'],rows:[[12]],row_count:1,warnings:[{code:'NULL_EXCLUDED',message:'空值记录已按口径排除',row_count:4},{code:'EMPTY_EXCLUDED',message:'本次没有额外排除记录',row_count:0}]});
 assert.ok(html.includes('aria-label="结果警告"'));assert.ok(html.includes('空值记录已按口径排除'));assert.ok(html.includes('排除行数：4'));assert.ok(html.includes('排除行数：0'));
});

test('NO_DATA remains an empty result and is never turned into numeric zero',()=>{
 const html=render({columns:['metric_value'],rows:[],row_count:0,no_data:true,warnings:[]});
 assert.ok(html.includes('当前口径下没有有效数据'));assert.ok(html.includes('<tbody></tbody>'));assert.ok(!html.includes('<td>0</td>'));
});

test('actual null, zero and false cells remain distinct with business column labels',()=>{
 const html=render({columns:['metric_value','nullable_value','flag'],column_metadata:[{name:'metric_value',label:'订单量'}],rows:[[0,null,false]],row_count:1,no_data:false});
 assert.ok(html.includes('>订单量</th>'));assert.ok(html.includes('<td>0</td>'));assert.ok(html.includes('<td>NULL</td>'));assert.ok(html.includes('<td>false</td>'));assert.ok(!html.includes('没有有效数据'));
});

test('primary intent is a business summary rather than protocol field names',()=>{
 const html=renderToStaticMarkup(React.createElement(IntentSummary,{intent:{metric_terms:['访问账户数'],start:'2025-01-01',end_exclusive:'2026-01-01',timezone:'SOURCE',time_event:'会话开始时间',dimension_terms:[],filters:[],negative_terms:[],depends_on_requirement_ids:[]}}));
 assert.ok(html.includes('关注指标'));assert.ok(html.includes('源库原始时间'));assert.ok(html.includes('访问账户数'));assert.ok(!html.includes('metric_terms'));assert.ok(!html.includes('depends_on_requirement_ids'));
});

test('semantic path renders exact revision and real property-to-field mapping',()=>{
 const ref=(asset_type,asset_id,revision)=>({workspace_id:10,asset_type,asset_id,revision}),er=ref('entity','entity:session',1),mr=ref('metric','metric:identity',2),br=ref('binding','binding:identity',3);
 const field=field_id=>({catalog_snapshot_id:'catalog-fixed',datasource_id:'7',dataset_id:'dwd.sessions',field_id}),property=property_id=>({entity_ref:er,property_id});
 const entity={...er,name:'会话',spec:{properties:[{property_id:'customer_id',name:'客户账户标识'},{property_id:'started_at',name:'会话开始时间'}]}},metric={...mr,name:'访问账户数',definition:'按账户去重',spec:{expression:{op:'call',function:'count_distinct',args:[{op:'property',property_ref:property('customer_id')}]},time_policy:{property_ref:property('started_at')},grain:{description:'访问账户'},unit:{kind:'COUNT',code:'account',authority:'SYNTHETIC_ONLY'},null_policy:'REJECT_ROW_AND_WARN',empty_result_policy:'NO_DATA'}},binding={...br,name:'访问账户映射',spec:{property_mappings:[{property_ref:property('customer_id'),expression:{op:'field',field_ref:field('customer_id')}}],time_field_ref:field('started_at'),calculation:{op:'call',function:'count_distinct',args:[{op:'field',field_ref:field('customer_id')}]}}};
 const context={assets:[entity,metric,binding].map(asset_revision=>({asset_ref:asset_revision,asset_revision})),snapshots:[{catalog_snapshot_id:'catalog-fixed',datasets:[{datasource_id:'7',dataset_id:'dwd.sessions',namespace:'dwd',physical_name:'dwd_sessions',logical_locator:'dwd.sessions',fields:[{field_id:'customer_id',name:'customer_key',business_definition:{name_zh:'账户标识'}},{field_id:'started_at',name:'start_time',business_definition:{name_zh:'会话开始时间'}}]}]}],status:'ready'},requirement={bundle:{selected_metric_ref:mr,selected_binding_ref:br,requested_dimension_refs:[],closure_refs:[{kind:'FIELD',target_ref:field('customer_id')}]}};
 const html=renderToStaticMarkup(React.createElement(SemanticPath,{requirement,context}));
 assert.ok(html.includes('访问账户数 · r2'));assert.ok(html.includes('访问账户映射 · r3'));assert.ok(html.includes('catalog-fixed'));assert.ok(html.includes('客户账户标识'));assert.ok(html.includes('dwd.dwd_sessions.customer_key'));assert.ok(html.includes('dwd.dwd_sessions.start_time'));assert.ok(!html.includes('暂不支持编辑的计算结构'));
});
