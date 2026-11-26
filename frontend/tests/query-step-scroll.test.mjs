import test from 'node:test';
import assert from 'node:assert/strict';
import {queryStep,scrollQueryStep} from '../src/queryStepScroll.js';
test('confirmations locate the next interactive step and actual model phase',()=>{
 assert.equal(queryStep({state:'DRAFT',model_progress:{phase:'CONNECTING'}}).name,'progress');
 assert.notEqual(queryStep({state:'DRAFT',model_progress:{phase:'CONNECTING'}}).key,queryStep({state:'DRAFT',model_progress:{phase:'MODEL_RUNNING'}}).key);
 for(const [state,name] of [['TARGET_CONFIRMATION','intent'],['NEED_CONFIRMATION','metric'],['PREVIEW_READY','sql'],['AUTHORIZED','gate'],['DELIVERED','result']])assert.equal(queryStep({state}).name,name);
 assert.equal(queryStep({state:'NEED_CLARIFICATION',clarifications:[{id:'c',slot:'time_periods'}]}).name,'time');
 assert.equal(queryStep({state:'NEED_CLARIFICATION',clarifications:[{id:'c',slot:'roi_definition'}]}).name,'clarification');
});
test('scroll moves only the inner conversation by the target position and respects reduced motion',()=>{
 let result;const container={scrollTop:300,getBoundingClientRect:()=>({top:100}),scrollTo:value=>result=value};
 scrollQueryStep(container,{getBoundingClientRect:()=>({top:180})},true);
 assert.deepEqual(result,{top:362,behavior:'instant'});
 scrollQueryStep(container,{getBoundingClientRect:()=>({top:-500})});assert.equal(result.top,0);assert.equal(result.behavior,'smooth');
});
