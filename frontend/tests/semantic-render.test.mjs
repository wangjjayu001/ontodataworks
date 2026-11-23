import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createSemanticDraft} from '../src/semanticUiModel.js';

const require=createRequire(import.meta.url);
const viteRequire=createRequire(require.resolve('vite'));
const esbuild=viteRequire('esbuild');
const compiled=await esbuild.build({entryPoints:[fileURLToPath(new URL('../src/SemanticWorkbench.jsx',import.meta.url))],bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react'],logLevel:'silent'});
const loaded={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(require,loaded,loaded.exports);
const {SemanticEditor,SemanticDefinition,CatalogBrowser}=loaded.exports;
const context={assets:[],currentAssets:[],snapshots:[],users:[],policies:[]};

test('all ten new asset types render dedicated maintenance controls with immutable identity',()=>{
 for(const type of ['metric','binding','entity','dimension','domain','term','relation','source','claim','evidence']){
  const asset=createSemanticDraft({workspace_id:3,asset_type:type},'render-test');
  const html=renderToStaticMarkup(React.createElement(SemanticEditor,{asset,onChange(){},context}));
  assert.ok(html.includes('基本业务定义'),type);
  assert.ok(html.includes('本次')||html.includes('业务')||html.includes('来源'),type);
  assert.ok(!/input[^>]+value="3"/.test(html),'workspace identity must stay read-only');
  assert.ok(!html.includes('value="binding:render-test"'),'asset identity must stay read-only');
  if(type==='metric'){assert.ok(!html.includes('时区'));assert.ok(!html.includes('timezone'));assert.ok(html.includes('统计实体'));assert.ok(html.includes('去重的业务标识'));assert.ok(!html.includes('运算节点'));}
  if(type==='binding'){assert.ok(html.includes('物理映射维护步骤'));assert.ok(html.includes('选择口径'));assert.ok(html.includes('验证与依据'));assert.ok(!html.includes('需要运行的核验项目'));}
 }
});

test('entity definition preserves false and zero-looking business values without treating them as missing',()=>{
 const asset=createSemanticDraft({workspace_id:3,asset_type:'entity'},'entity-render');
 Object.assign(asset,{name:'订单',definition:'一笔业务订单'});
 asset.spec={kind:'ENTITY',grain:'订单',business_key:['0'],properties:[{property_id:'0',name:'零编码',data_type:'INTEGER',semantic_role:'IDENTIFIER',nullable:false,unit:null}]};
 const html=renderToStaticMarkup(React.createElement(SemanticDefinition,{asset,context,onOpen(){}}));
 assert.ok(html.includes('零编码'));assert.ok(html.includes('false'));assert.ok(html.includes('<code>0</code>'));
});

test('catalog renders real Chinese field meaning and keeps absent snapshots explicit',()=>{
 const empty=renderToStaticMarkup(React.createElement(CatalogBrowser,{context,command(){}}));
 assert.ok(empty.includes('没有可用目录快照'));
 const withCatalog={...context,snapshots:[{catalog_snapshot_id:'catalog:exact',source_revision:'r1',datasets:[{dataset_id:'orders',datasource_id:'2',logical_locator:'dwd.orders',namespace:'dwd',physical_name:'orders',constraints_state:'UNKNOWN',fields:[{field_id:'customer_state',data_type:'text',nullable:false,semantic_role:'ATTRIBUTE',unit:null,business_definition:{name_zh:'买家所在地区（省/州）',definition:'地域代码，不是买家业务状态'}}]}]}]};
 const html=renderToStaticMarkup(React.createElement(CatalogBrowser,{context:withCatalog,command(){}}));
 assert.ok(html.includes('买家所在地区（省/州）'));assert.ok(html.includes('地域代码，不是买家业务状态'));assert.ok(html.includes('false'));
});
