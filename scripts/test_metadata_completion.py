"""Metadata fallback state recovery and numerical comparison regressions."""
import copy
import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import duckdb
import test_agent_clarifications as flow
from test_metadata_candidate_flow import context
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from datetime import timedelta
from app.agent_models import AgentJob
from app.agent_worker import claim, metadata_model_context, normalize_intent
from app.agent_metadata import metadata_job
from app.agent_planner import (canonical_hash, normalize_metadata_candidate, compile_metadata_preview,
    observe_measure_rows, apply_result_policies, source_check, PlanningBlocked)
from app.db import now
from app.semantic import sha
from app.agent import refresh_run_model_selection, _cancel_model_jobs
from app.model_runtime import METADATA_CANDIDATE_SCHEMA
from codex_bridge import MAX_BODY
from app.agent_clarification import merge_clarified_intent


class ComparisonTests(unittest.TestCase):
    def test_roi_filter_is_not_an_implicit_group_and_formula_does_not_become_sales_metric(self):
        question='鞋类25年5月环比6月的roi如何'
        item={'question':question,'metric_terms':['ROI'],'dimension_terms':['商品类目'],
            'filters':[{'field':'商品类目','operator':'=','value':'鞋类'}]}
        intent,_,error=normalize_intent(item,question)
        self.assertIsNone(error);self.assertEqual(intent['dimension_ids'],[])
        self.assertEqual(intent['filters'],item['filters'])
        previous={'intent_frame':{**intent,'dimension_ids':['product_category']},'answers':[],'question':question}
        parsed={**item,'question':'鞋类五月六月ROI，口径为销售额÷投放费用'}
        refined,pending,error=normalize_intent(parsed,question)
        self.assertFalse(any(c['slot']=='metric' for c in pending))
        merged,_=merge_clarified_intent(previous,refined,pending)
        self.assertEqual(merged['metric_terms'],['ROI']);self.assertEqual(merged['dimension_ids'],[])

    def setup_ratio(self):
        discovery,bundle,intent,plan=context()
        cost=copy.deepcopy(discovery['tables'][0]['fields'][3])
        cost.update(field_id='ad_cost',name='ad_cost',name_zh='投放费用')
        discovery['tables'][0]['fields'].append(cost)
        discovery['content_hash']=canonical_hash({k:v for k,v in discovery.items() if k!='content_hash'})
        intent.pop('timezone')
        intent.update(metric_terms=['ROI'],time_periods=[
            {'label':'五月','start':'2025-05-01','end_exclusive':'2025-06-01'},
            {'label':'六月','start':'2025-06-01','end_exclusive':'2025-07-01'}],
            analysis={'kind':'PERIOD_COMPARISON','measures':['ABSOLUTE_CHANGE','GROWTH_RATE']})
        plan.update(name='ROI',definition='销售额合计除以投放费用合计',operation='RATIO_SUM',
            measure_field_id='gross_amount',denominator_field_id='ad_cost')
        candidate=normalize_metadata_candidate(plan,discovery,intent,bundle,[{'answer':'销售额÷投放费用，同一单位'}])
        frame,preview=compile_metadata_preview(candidate,intent,bundle,'run','req',{'id':'confirm'})
        return frame,preview

    def query(self, records):
        frame,preview=self.setup_ratio()
        db=duckdb.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE SCHEMA dwd; CREATE TABLE dwd.fct_orders (created_at TIMESTAMP,gross_amount DECIMAL(12,2),ad_cost DECIMAL(12,2))')
        if records:db.executemany('INSERT INTO dwd.fct_orders VALUES (?,?,?)',records)
        class Connection:
            def execute(self,sql,parameters):
                names=set(re.findall(r'%\(([a-z_0-9]+)\)s',sql))
                sql=re.sub(r'%\(([a-z_0-9]+)\)s',r'$\1',sql)
                return db.execute(sql,{key:value for key,value in parameters.items() if key in names})
        conn=Connection()
        counts=observe_measure_rows(conn,frame,preview['parameters'])
        rows=conn.execute(preview['sql'],preview['parameters']).fetchall()
        values,no_data,warnings=apply_result_policies(frame,counts,rows)
        return values,no_data,warnings,counts

    def test_ratio_uses_independent_periods_and_actual_fixed_base(self):
        values,no_data,warnings,counts=self.query([
            ('2025-05-01',100,20),('2025-05-31',200,30),('2025-06-01',600,60),
            ('2025-07-01',999,1)])
        self.assertEqual(values,[('五月',6.0,0.0,0.0),('六月',10.0,4.0,4/6)])
        self.assertFalse(no_data);self.assertFalse(warnings)
        self.assertEqual([p['source_rows'] for p in counts['periods']],[2,1])

    def test_missing_base_is_never_replaced_by_first_available_period(self):
        values,_,warnings,_=self.query([('2025-06-01',600,60)])
        self.assertEqual(values,[('六月',10.0,None,None)])
        self.assertIn('PERIOD_NO_DATA',[w['code'] for w in warnings])

    def test_zero_denominator_stays_null_and_warns(self):
        values,no_data,warnings,_=self.query([('2025-05-01',100,0),('2025-06-01',600,60)])
        self.assertIsNone(values[0][1]);self.assertIsNone(values[1][2])
        self.assertFalse(no_data)
        self.assertIn('ZERO_DENOMINATOR',[w['code'] for w in warnings])

    def test_null_pairs_and_empty_periods_do_not_become_zero(self):
        values,no_data,warnings,counts=self.query([('2025-05-01',100,None),('2025-06-01',None,60)])
        self.assertEqual(values,[]);self.assertTrue(no_data)
        self.assertEqual(counts['null_measure_rows'],2)

    def test_source_check_uses_field_id_inside_physical_reference(self):
        frame,preview=self.setup_ratio()
        content={'datasets':[]};snapshot=SimpleNamespace(workspace_id=31,content=content,content_hash=sha(content))
        candidate=frame['metadata_candidate'];candidate['catalog_snapshot_hash']=snapshot.content_hash
        candidate['content_hash']=canonical_hash({k:v for k,v in candidate.items() if k!='content_hash'})
        frame['metadata_candidate_hash']=candidate['content_hash']
        dataset={'schema_fingerprint':candidate['schema_fingerprint'],'logical_locator':candidate['logical_locator'],
            'fields':[{'field_id':ref['target_ref']['field_id']} for ref in frame['physical_refs'] if ref['kind']=='FIELD']}
        class DB:
            def get(self,model,key):return snapshot if key=='catalog-1' else None
        with patch('app.semantic.resolve_physical',return_value=dataset):
            with self.assertRaises(PlanningBlocked) as exc:source_check(DB(),31,frame,preview,False)
            self.assertEqual(exc.exception.code,'SOURCE_UNAVAILABLE')
            frame['physical_refs'][1]['target_ref']['field_id']='invented'
            with self.assertRaises(PlanningBlocked) as exc:source_check(DB(),31,frame,preview,False)
            self.assertEqual(exc.exception.code,'PINNED_CATALOG_STALE')

    def test_model_receives_all_directory_fields_when_within_budget(self):
        discovery,*_=context()
        discovery['tables']=[copy.deepcopy(discovery['tables'][0]) for _ in range(20)]
        result=metadata_model_context(discovery)
        self.assertEqual(len(result['tables']),20)
        self.assertEqual(result['model_field_count'],100)
        self.assertFalse(result['truncated'])

    def test_full_bounded_context_and_schema_fit_the_bridge_transport(self):
        discovery,*_=context()
        discovery['tables']=[copy.deepcopy(discovery['tables'][0]) for _ in range(42)]
        for table in discovery['tables']:
            for field in table['fields']:field['definition']='中文字段释义'*40
        result=metadata_model_context(discovery)
        body={'input':{'question':'五月六月比较','context':result},'output_schema':METADATA_CANDIDATE_SCHEMA}
        self.assertLess(len(json.dumps(body,ensure_ascii=False).encode()),MAX_BODY)

    def test_directory_refresh_keeps_chosen_model_and_reasoning_effort(self):
        selection={'model':'model-a','reasoningEffort':'high','serviceTier':None,'catalog_version':'before-restart'}
        payload={'model_selection':selection}
        with patch('app.model_runtime.list_models',return_value={'catalog_version':'after-restart','models':[
                {'id':'model-a','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]}):
            refresh_run_model_selection(payload)
        self.assertEqual(payload['model_selection'],{**selection,'catalog_version':'after-restart'})

    def test_multiple_dimensions_cannot_silently_collapse_into_one_field(self):
        discovery,bundle,intent,plan=context()
        intent['dimension_terms']=['商品类目','订单状态'];plan['dimension_field_ids']=['category']
        with self.assertRaises(PlanningBlocked):normalize_metadata_candidate(plan,discovery,intent,bundle)

    def test_unclassified_time_field_requires_explicit_run_local_field_confirmation(self):
        discovery,bundle,intent,plan=context()
        discovery['tables'][0]['fields'][1]['semantic_role']='ATTRIBUTE'
        discovery['content_hash']=canonical_hash({k:v for k,v in discovery.items() if k!='content_hash'})
        with self.assertRaises(PlanningBlocked) as exc:normalize_metadata_candidate(plan,discovery,intent,bundle)
        self.assertIn('created_at',exc.exception.message)
        candidate=normalize_metadata_candidate(plan,discovery,intent,bundle,[{'answer':'本次按 created_at 表示订单创建时间'}])
        self.assertEqual(candidate['time_role_source'],'USER_CLARIFICATION')
        self.assertEqual(candidate['time_field']['semantic_role'],'ATTRIBUTE')
        self.assertFalse(candidate['business_approved'])


class MetadataRecoveryTests(unittest.TestCase):
    setUp=flow.ClarificationFlowTests.setUp
    tearDown=flow.ClarificationFlowTests.tearDown

    def seed(self):
        self.discovery,self.bundle,intent,_=context()
        self.bundle.update(next_action='NEED_CLARIFICATION',coverage=[])
        payload=copy.deepcopy(self.row.payload);req=payload['requirements'][0]
        req.update(state='BLOCKED_EVIDENCE',intent_frame=intent,answers=[],clarifications=[],
            error={'code':'COMPARISON_PLANNING_UNSUPPORTED'},intent_frame_hash=canonical_hash(intent))
        payload.update(pinned_deployment={},trace_id='test-trace');self.row.payload=payload;self.db.commit()

    def retry(self):
        req=self.row.payload['requirements'][0]
        with patch('app.agent.check_run_access'),patch('app.semantic.retrieve',return_value=self.bundle), \
             patch('app.semantic.metadata_candidates_for_bundle',return_value=self.discovery):
            response=self.client.post('/api/workspaces/31/agent/runs/run-test/requirements/requirement-test/retry-retrieval',
                json={'expected_run_version':self.row.run_version,'expected_requirement_version':req['requirement_version']})
        self.assertEqual(response.status_code,200,response.text)
        self.db.refresh(self.row)
        return self.db.scalar(select(AgentJob).where(AgentJob.kind=='METADATA'))

    def test_blocked_run_can_retry_and_get_actionable_data_gap(self):
        self.seed();job=self.retry()
        self.assertEqual(self.row.payload['requirements'][0]['state'],'DRAFT')
        with patch('app.agent_metadata.SessionLocal',sessionmaker(self.engine,expire_on_commit=False)), \
             patch('app.agent_worker.SessionLocal',sessionmaker(self.engine,expire_on_commit=False)), \
             patch('app.agent.check_run_access'),patch('app.model_runtime.derive_metadata_candidate',
                return_value={'output':{'candidate_plan':None,'data_gaps':['缺少ROI分母投放费用字段']}}):
            metadata_job(job.id)
        self.db.expire_all();self.db.refresh(self.row);req=self.row.payload['requirements'][0]
        self.assertEqual(req['state'],'NEED_CLARIFICATION')
        self.assertEqual(req['data_gaps'],['缺少ROI分母投放费用字段'])
        self.assertNotIn('preview',req)
        self.assertEqual(req['data_availability']['model_field_count'],5)

    def test_metadata_expired_lease_is_retryable_and_cancel_is_not_resurrected(self):
        self.seed();job=self.retry();job.status='RUNNING';job.attempts=1
        job.lease_until=now()-timedelta(minutes=1);self.db.commit()
        with patch('app.agent_worker.SessionLocal',sessionmaker(self.engine,expire_on_commit=False)):
            self.assertEqual(claim(),job.id)
        self.db.refresh(job);job.lease_until=now()-timedelta(minutes=1)
        payload=copy.deepcopy(self.row.payload);payload['requirements'][0]['state']='CANCELLED'
        self.row.payload=payload;self.db.commit()
        with patch('app.agent_worker.SessionLocal',sessionmaker(self.engine,expire_on_commit=False)):
            self.assertIsNone(claim())
        self.db.refresh(job);self.assertEqual(job.status,'CANCELLED')

    def test_cancel_sends_the_metadata_request_id_and_cancels_queued_job(self):
        self.seed();job=self.retry();job.status='RUNNING';self.db.commit()
        with patch('app.model_runtime.cancel') as cancel:
            _cancel_model_jobs(self.db,self.row.id,'requirement-test')
            cancel.assert_called_once_with(job.id+':metadata')
        self.assertEqual(job.status,'CANCELLED')


if __name__=='__main__':unittest.main()
