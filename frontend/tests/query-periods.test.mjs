import test from 'node:test';
import assert from 'node:assert/strict';
import {periodDrafts,periodAnswer,progressSeconds} from '../src/queryInput.js';

test('editable inclusive dates round trip independent May and June windows without timezone shifts',()=>{
 const periods=[{label:'五月',start:'2025-05-01',end_exclusive:'2025-06-01'},{label:'六月',start:'2025-06-01',end_exclusive:'2025-07-01'}];
 const drafts=periodDrafts(periods);
 assert.equal(drafts[0].end,'2025-05-31');assert.equal(drafts[1].end,'2025-06-30');
 assert.deepEqual(periodAnswer(drafts,true),periods);
 drafts.reverse();drafts[0].start='2025-06-04';drafts[0].end='2025-06-17';
 const changed=periodAnswer(drafts,true);
 assert.equal(changed[0].start,'2025-06-04');assert.equal(changed[0].end_exclusive,'2025-06-18');
 assert.equal(changed[1].start,'2025-05-01');
});
test('period input supports arbitrary windows and leap days while rejecting an incomplete comparison',()=>{
 const drafts=[{label:'闰日',start:'2024-02-29',end:'2024-02-29'},{label:'窗口',start:'2025-01-03',end:'2025-01-12'},{label:'第三期',start:'2026-01-01',end:'2026-02-04'}];
 assert.equal(periodAnswer(drafts,true).length,3);
 assert.equal(periodAnswer(drafts,true)[0].end_exclusive,'2024-03-01');
 assert.equal(periodAnswer(drafts.slice(0,1),true),null);
 assert.equal(periodAnswer([{start:'2025-02-30',end:'2025-03-04'}]),null);
});
test('elapsed time handles a future clock and missing timestamps without displaying negative seconds',()=>{
 assert.equal(progressSeconds('2025-01-01T00:00:00Z',Date.parse('2025-01-01T00:00:26Z')),26);
 assert.equal(progressSeconds('2025-01-01T00:00:02Z',Date.parse('2025-01-01T00:00:00Z')),0);
 assert.equal(progressSeconds(undefined),0);
});
