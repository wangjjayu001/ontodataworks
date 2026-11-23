import test from 'node:test';
import assert from 'node:assert/strict';
import {readSemanticDeployment,semanticActivationCommand} from '../src/semanticDeployment.js';
import {timeRangeAnswer,clarificationChoice} from '../src/queryInput.js';

const release=scope=>({scope,release_ref:{id:'release-'+scope,content_hash:'release-hash-'+scope}});
const index=scope=>({index_snapshot_ref:{id:'index-'+scope,content_hash:'index-hash-'+scope},index_snapshot:{id:'index-'+scope,release_id:'release-'+scope,release_hash:'release-hash-'+scope}});

test('production activation reads production pointer even when local pointer differs',async()=>{
 const reads=[];
 const request=async path=>{reads.push(path);const scope=path.split('/').at(-1);return {data:{scope,deployment:{scope,pointer_version:scope==='production'?17:3}}};};
 const local=await readSemanticDeployment(request,'local_validation');
 const production=await readSemanticDeployment(request,'production');
 assert.equal(semanticActivationCommand('production',production,release('production'),index('production'),'发布').body.expected_pointer_version,17);
 assert.equal(semanticActivationCommand('local_validation',local,release('local_validation'),index('local_validation'),'回滚').version,3);
 assert.deepEqual(reads,['/semantic/deployments/local_validation','/semantic/deployments/production']);
 assert.throws(()=>semanticActivationCommand('production',local,release('production'),index('production'),'发布'),/环境不一致/);
});

test('only an explicit empty same-scope deployment permits first activation with version zero',async()=>{
 const current=await readSemanticDeployment(async()=>({data:{scope:'production',deployment:null}}),'production');
 assert.equal(semanticActivationCommand('production',current,release('production'),index('production'),'首次发布').version,0);
 await assert.rejects(readSemanticDeployment(async()=>({data:{scope:'production'}}),'production'),/响应不匹配/);
 await assert.rejects(readSemanticDeployment(async()=>({data:{scope:'production',deployment:{scope:'production'}}}),'production'),/指针缺失/);
});

test('failed reads, mismatched scope and mismatched index cannot become an activation request',async()=>{
 await assert.rejects(readSemanticDeployment(async()=>{throw new Error('403 denied');},'production'),/403 denied/);
 await assert.rejects(readSemanticDeployment(async()=>({data:{scope:'local_validation',deployment:null}}),'production'),/响应不匹配/);
 const current={scope:'production',deployment:{scope:'production',pointer_version:9},pointerVersion:9};
 assert.throws(()=>semanticActivationCommand('production',current,release('local_validation'),index('local_validation'),'发布'),/环境不一致/);
 assert.throws(()=>semanticActivationCommand('production',current,release('production'),index('local_validation'),'发布'),/完整快照/);
});

test('calendar input keeps exact half-open date strings without browser timezone conversion',()=>{
 assert.deepEqual(timeRangeAnswer('2025-01-01','2026-01-01'),{start:'2025-01-01',end_exclusive:'2026-01-01'});
 assert.deepEqual(timeRangeAnswer('2024-02-29','2024-03-01'),{start:'2024-02-29',end_exclusive:'2024-03-01'});
 for(const dates of [['2025-02-29','2025-03-02'],['2025-01-01','2025-01-01'],['2026-01-01','2025-01-01'],['2025-01-01T00:00:00Z','2026-01-01']])assert.equal(timeRangeAnswer(...dates),null);
});

test('clarification values zero and false are preserved independently of labels',()=>{
 assert.deepEqual(clarificationChoice({value:0,label:'零'}),{value:0,label:'零',description:null});
 assert.deepEqual(clarificationChoice({value:false}),{value:false,label:false,description:null});
 assert.deepEqual(clarificationChoice(false),{value:false,label:false,description:null});
});
