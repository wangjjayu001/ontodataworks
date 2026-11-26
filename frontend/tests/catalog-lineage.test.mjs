import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {lineageLayout,connectedFields} from '../src/catalogLineageModel.js';
import {resolveNavigation} from '../src/navigation.js';
const data={focus:'table:2',nodes:[{id:'table:1',columns:[{name:'price'}]},{id:'table:2',columns:[{name:'amount'},{name:'unrelated'}]},{id:'table:3',columns:[{name:'total'}]}],records:[{id:'r',kind:'warehouse_transform'},{id:'q',kind:'query'}],table_edges:[{source:'table:1',target:'table:2',record_id:'r'},{source:'table:2',target:'table:3',record_id:'q'}],column_edges:[{source:'table:1',source_column:'price',target:'table:2',target_column:'amount',record_id:'r'},{source:'table:2',source_column:'amount',target:'table:3',target_column:'total',record_id:'q'}]};
test('field selection follows exact identities across upstream and downstream',()=>{
 const keys=connectedFields(data.column_edges,{node:'table:2',column:'amount'});
 assert.deepEqual([...keys].sort(),['table:1.price','table:2.amount','table:3.total']);assert.ok(!keys.has('table:2.unrelated'));
 const graph=lineageLayout(data,{level:'column',anchor:{node:'table:2',column:'amount'}});
 assert.equal(graph.columnEdges.length,2);assert.equal(graph.nodes[1].columns.length,1);
});
test('direction and relationship filters never draw arrows to hidden nodes',()=>{
 let graph=lineageLayout(data,{direction:'upstream'});assert.deepEqual(graph.nodes.map(n=>n.id),['table:1','table:2']);assert.equal(graph.tableEdges.length,1);
 graph=lineageLayout(data,{kind:'transform'});assert.equal(graph.tableEdges.length,1);assert.ok(!graph.nodes.some(n=>n.id==='table:3'));
 graph=lineageLayout(data,{level:'column',field:'absent'});assert.equal(graph.columnEdges.length,0);
});
test('a downstream join co-input is not an upstream ancestor and unrelated field cards are hidden',()=>{
 const branch={...data,nodes:[...data.nodes,{id:'table:4',columns:[{name:'other'}]}],
  table_edges:[...data.table_edges,{source:'table:4',target:'table:3',record_id:'q'}]};
 assert.deepEqual(lineageLayout(branch,{direction:'upstream'}).nodes.map(n=>n.id),['table:1','table:2']);
 const graph=lineageLayout(branch,{level:'column',anchor:{node:'table:2',column:'unrelated'}});
 assert.deepEqual(graph.nodes.map(n=>n.id),['table:2']);assert.equal(graph.columnEdges.length,0);
});
test('legacy lineage bookmarks open the integrated data map',()=>{
 assert.deepEqual(resolveNavigation('#lineage'),{page:'assets',view:'lineage'});
 assert.deepEqual(resolveNavigation('#assets/lineage'),{page:'assets',view:'lineage'});
});
const require=createRequire(import.meta.url),esbuild=createRequire(require.resolve('vite'))('esbuild');
const build=await esbuild.build({entryPoints:[fileURLToPath(new URL('../src/CatalogLineage.jsx',import.meta.url))],bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react'],logLevel:'silent'});
const loaded={exports:{}};new Function('require','module','exports',build.outputFiles[0].text)(require,loaded,loaded.exports);
test('evidence distinguishes row counts and declarations from resolved fields',()=>{
 const html=renderToStaticMarkup(React.createElement(loaded.exports.LineageEvidence,{record:{id:'r1',status:'declared_only',sql:'SELECT COUNT(*) AS records',evidence:{historical_execution_proven:false},outputs:[{name:'records',expression:'COUNT(*)',status:'ROW_COUNT'}]},nodes:[],onClose(){}}));
 for(const text of ['静态生成声明','历史执行已证明','否','按源记录计数','查看已记录的加工 SQL'])assert.ok(html.includes(text),text);
});
