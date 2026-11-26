"""Isolated HTTP/worker tests; no real model, business DB or warehouse calls."""
import copy
import os
from pathlib import Path
import sys
import unittest
from datetime import timedelta
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DATABASE_URL', 'sqlite://')
from cryptography.fernet import Fernet
os.environ.setdefault('ENCRYPTION_KEY', Fernet.generate_key().decode())
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base, User, Workspace, Membership, now
from app.security import identity, db_session
from app.agent import router
from app.agent_models import AgentRun, AgentJob
from app.agent_worker import clarification_job, record_failure, claim
from app.agent_planner import canonical_hash, compile_preview, PlanningBlocked
from app.agent_clarification import merge_clarified_intent


def requirement():
    intent = {'question': '服饰鞋包类目2025年比2024年销售额增长多少',
        'original_question': '服饰鞋包类目2025年比2024年销售额增长多少',
        'metric_terms': ['gross_order_value'], 'metric_id': 'gross_order_value',
        'dimension_terms': ['商品类目'], 'dimension_ids': ['product_category'],
        'start': '2024-01-01', 'end_exclusive': '2026-01-01', 'timezone': 'SOURCE',
        'filters': [{'field': '商品类目', 'operator': '=', 'value': '服饰鞋包'}],
        'negative_terms': ['排除测试用户'], 'depends_on_requirement_ids': []}
    return {'id': 'requirement-test', 'requirement_id': 'requirement-test', 'requirement_version': 3,
        'question': intent['question'], 'intent_frame': intent, 'intent_frame_hash': canonical_hash(intent),
        'state': 'NEED_CLARIFICATION', 'error': None,
        'answers': [{'clarification_id': 'old-timezone', 'answer': 'SOURCE'},
                    {'clarification_id': 'old-metric', 'answer': 'gross_order_value'}],
        'clarifications': [{'id': 'direction', 'slot': 'comparison_direction', 'question': '比较方向？', 'options': ['2024年为基期，比较2025年']},
                           {'id': 'growth', 'slot': 'growth_measure', 'question': '增长率还是增长金额？', 'options': ['两者都要']}]}


class ClarificationFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.user = User(id=1, username='tester', display_name='test', role='admin', password_hash='not-a-login', enabled=1)
        self.db.add_all([self.user, Workspace(id=31, name='isolated-clarification', description='test'), Membership(user_id=1, workspace_id=31)])
        self.row = AgentRun(id='run-test', workspace_id=31, actor_id=1, conversation_id='conversation-test',
            state='NEED_CLARIFICATION', run_version=4,
            payload={'message': {'text': requirement()['question']}, 'requirements': [requirement()],
                'model_selection': {}, 'pinned_deployment': {}, 'request_id': 'test-request', 'event_seq': 0})
        self.db.add(self.row); self.db.commit()
        self.app = FastAPI(); self.app.include_router(router)
        self.app.dependency_overrides[identity] = lambda: self.user
        self.app.dependency_overrides[db_session] = lambda: self.db
        self.client = TestClient(self.app)
        # SQLite has no pg_advisory_xact_lock. Live HTTP separately tests keys.
        self.start = patch('app.agent.command_start', return_value=(None, {})); self.start.start()
        def commit(db, meta, response):
            db.commit(); return response
        self.done = patch('app.agent.command_done', side_effect=commit); self.done.start()

    def tearDown(self):
        self.start.stop(); self.done.stop(); self.db.close(); self.engine.dispose()

    def command(self, cid, answer, expected=200, versions=None):
        self.db.refresh(self.row)
        req = self.row.payload['requirements'][0]
        body = {'clarification_id': cid, 'answer': answer,
            'expected_run_version': self.row.run_version,
            'expected_requirement_version': req['requirement_version'], **(versions or {})}
        result = self.client.post('/api/workspaces/31/agent/runs/run-test/requirements/requirement-test/clarifications', json=body)
        self.assertEqual(result.status_code, expected, result.text)
        self.db.refresh(self.row)
        return result.json().get('data', result.json())

    def queue(self):
        payload = copy.deepcopy(self.row.payload)
        payload['requirements'][0]['clarifications'][1]['slot'] = 'semantic_definition'
        self.row.payload = payload; self.db.commit()
        self.command('direction', '2024年为基期，比较2025年')
        self.command('growth', '两者都要')
        return self.db.scalar(select(AgentJob)).id

    def parsed(self):
        intent = copy.deepcopy(requirement()['intent_frame'])
        intent.update(metric_terms=['商品金额'], timezone=None, negative_terms=[],
            clarifications=[{'slot': 'timezone', 'question': '重复时区', 'options': []},
                            {'slot': 'metric', 'question': '重复指标', 'options': []}])
        return {'output': {'intent': intent}, 'tool_events': []}

    def worker(self, job, output=None, side_effect=None):
        with patch('app.agent_worker.SessionLocal', sessionmaker(self.engine, expire_on_commit=False)), \
             patch('app.agent_worker.pinned_model_vocabulary', return_value={}), \
             patch('app.model_runtime.refine_intent', return_value=output or self.parsed(), side_effect=side_effect) as model:
            clarification_job(job)
        self.db.expire_all()
        return model

    def test_model_slots_accept_option_and_free_text_without_creating_new_run(self):
        data = self.command('direction', '以2024年为基期，比较2025年全年')
        req = data['run']['requirements'][0]
        self.assertEqual(req['state'], 'NEED_CLARIFICATION')
        self.assertEqual(req['clarifications'][0]['id'], 'growth')
        self.assertEqual(req['intent_frame']['clarification_answers'][0]['slot'], 'comparison_direction')
        self.assertEqual(req['answers'][-1]['actor_id'], 1)
        self.assertEqual(len(self.db.scalars(select(AgentRun)).all()), 1)
        self.assertFalse(self.db.scalars(select(AgentJob)).all())

    def test_comparison_answers_stay_one_requirement_without_reparsing_both_as_new_metrics(self):
        self.command('direction', '2024年为基期，比较2025年')
        data = self.command('growth', '两者都要')
        req = data['run']['requirements'][0]
        self.assertEqual(req['state'], 'TARGET_CONFIRMATION')
        self.assertEqual(len(req['intent_frame']['clarification_answers']), 2)
        self.assertFalse(self.db.scalars(select(AgentJob)).all())

    def test_final_answer_queues_only_reparse_and_keeps_cas(self):
        self.queue()
        req = self.row.payload['requirements'][0]
        jobs = self.db.scalars(select(AgentJob)).all()
        self.assertEqual(req['state'], 'DRAFT')
        self.assertEqual([job.kind for job in jobs], ['CLARIFY'])
        self.assertEqual(jobs[0].requirement_id, req['id'])
        self.assertEqual(req['clarification_parse_version'], req['requirement_version'])
        self.command('growth', '两者都要', expected=409, versions={'expected_run_version': 4})

    def test_reparse_preserves_chosen_metric_timezone_filters_exclusions_and_identity(self):
        job = self.queue(); model = self.worker(job)
        req = self.db.get(AgentRun, 'run-test').payload['requirements'][0]
        self.assertEqual(req['id'], 'requirement-test')
        self.assertEqual(req['state'], 'TARGET_CONFIRMATION')
        self.assertEqual(req['intent_frame']['timezone'], 'SOURCE')
        self.assertEqual(req['intent_frame']['metric_id'], 'gross_order_value')
        self.assertEqual(req['intent_frame']['metric_terms'], ['gross_order_value'])
        self.assertEqual(req['intent_frame']['negative_terms'], ['排除测试用户'])
        self.assertEqual(len(req['intent_frame']['clarification_answers']), 2)
        self.assertIn('比较2025年', model.call_args.args[0])
        self.assertIn('两者都要', model.call_args.args[0])
        self.assertEqual(self.db.get(AgentJob, job).status, 'DONE')
        self.assertFalse(req.get('preview'))

    def test_parse_cannot_drop_original_filter(self):
        job = self.queue(); output = self.parsed(); output['output']['intent']['filters'] = []
        self.worker(job, output)
        req = self.db.get(AgentRun, 'run-test').payload['requirements'][0]
        self.assertEqual(req['state'], 'BLOCKED_EVIDENCE')
        self.assertEqual(req['error']['code'], 'CLARIFICATION_CONSTRAINT_LOST')
        self.assertEqual(req['intent_frame']['filters'], requirement()['intent_frame']['filters'])

    def test_cancel_during_model_call_cannot_resurrect_requirement(self):
        job = self.queue()
        def cancelled(*args, **kwargs):
            with Session(self.engine) as db:
                row = db.get(AgentRun, 'run-test'); payload = copy.deepcopy(row.payload)
                payload['requirements'][0].update(state='CANCELLED', requirement_version=99)
                row.payload = payload; row.state = 'CANCELLED'; db.commit()
            return self.parsed()
        self.worker(job, side_effect=cancelled)
        self.assertEqual(self.db.get(AgentRun, 'run-test').payload['requirements'][0]['state'], 'CANCELLED')
        self.assertEqual(self.db.get(AgentJob, job).status, 'CANCELLED')

    def test_completed_job_is_not_reparsed(self):
        job = self.queue(); self.worker(job)
        model = self.worker(job)
        model.assert_not_called()

    def test_expired_clarification_lease_cannot_resurrect_cancelled_requirement(self):
        jobid = self.queue()
        job = self.db.get(AgentJob, jobid)
        job.status = 'RUNNING'; job.attempts = 2; job.lease_until = now() - timedelta(minutes=1)
        payload = copy.deepcopy(self.row.payload); payload['requirements'][0]['state'] = 'CANCELLED'
        self.row.payload = payload; self.row.state = 'CANCELLED'; self.db.commit()
        with patch('app.agent_worker.SessionLocal', sessionmaker(self.engine, expire_on_commit=False)):
            self.assertIsNone(claim())
        self.db.expire_all()
        self.assertEqual(self.db.get(AgentRun, 'run-test').payload['requirements'][0]['state'], 'CANCELLED')
        self.assertEqual(self.db.get(AgentJob, jobid).status, 'CANCELLED')

    def test_malformed_answer_leaves_run_and_answer_history_unchanged(self):
        before = copy.deepcopy(self.row.payload)
        self.command('direction', {'unexpected': 'bad'}, expected=422)
        self.assertEqual(self.row.payload, before)

    def test_comparison_never_compiles_as_combined_two_year_total(self):
        with self.assertRaises(PlanningBlocked) as exc:
            compile_preview({'scope': 'local_validation', 'metric_id': 'order_count'},
                requirement()['intent_frame'], {}, 'run-test', 'requirement-test', {})
        self.assertEqual(exc.exception.code, 'COMPARISON_PLANNING_UNSUPPORTED')

    def test_clarification_model_failure_is_not_an_execution_failure(self):
        job = self.queue()
        with patch('app.agent_worker.SessionLocal', sessionmaker(self.engine, expire_on_commit=False)):
            record_failure(job, PlanningBlocked('MODEL_UNAVAILABLE', 'test model unavailable'))
        self.db.expire_all()
        req = self.db.get(AgentRun, 'run-test').payload['requirements'][0]
        self.assertEqual(req['state'], 'MODEL_FAILED')
        self.assertEqual(req['error']['code'], 'MODEL_UNAVAILABLE')

    def test_metadata_only_recall_after_clarification_reaches_confirmed_sql_preview(self):
        intent={'question':'2025年按订单创建时间统计订单数，使用源库时间不转换',
            'original_question':'2025年按订单创建时间统计订单数，使用源库时间不转换',
            'metric_terms':['订单数'],'dimension_terms':[],'dimension_ids':[],
            'start':'2025-01-01','end_exclusive':'2026-01-01','timezone':'SOURCE',
            'time_event':'订单创建时间','filters':[],'negative_terms':[],
            'analysis':{'kind':'NONE'},'depends_on_requirement_ids':[]}
        rid='metadata-requirement'
        dataset_ref={'catalog_snapshot_id':'catalog-1','datasource_id':'3','dataset_id':'dwd.orders'}
        discovery={'schema_version':'query-metadata-context/1.0.0','release_id':'release-1',
            'release_hash':'release-hash','index_snapshot_id':'index-1','index_snapshot_hash':'index-hash',
            'tables':[{'catalog_snapshot_id':'catalog-1','catalog_snapshot_hash':'catalog-hash',
                'datasource_id':'3','dataset_id':'dwd.orders','dataset_ref':dataset_ref,
                'table_label':'订单事实表','table_definition':'订单事实表','logical_locator':'dwd.orders',
                'namespace':'dwd','physical_name':'fct_orders','schema_fingerprint':'schema-fingerprint',
                'constraints_state':'DECLARED','key_constraints':[],
                'fields':[{'field_id':'order_id','name':'order_id','name_zh':'订单编号','definition':'订单唯一标识',
                    'data_type':'text','nullable':False,'semantic_role':'IDENTIFIER','unit':None,
                    'classification':'INTERNAL','is_primary':True},
                    {'field_id':'created_at','name':'created_at','name_zh':'订单创建时间','definition':'订单创建事件',
                    'data_type':'timestamp without time zone','nullable':False,'semantic_role':'EVENT_TIME',
                    'unit':None,'classification':'INTERNAL','is_primary':False}]}],
            'match_status':'RETRIEVAL_CANDIDATE','notice':'固定发布目录候选'}
        discovery['content_hash']=canonical_hash(discovery)
        bundle={'bundle_id':'bundle-1','next_action':'NEED_CLARIFICATION','selected_metric_ref':None,
            'selected_binding_ref':None,'release_id':'release-1','release_hash':'release-hash',
            'index_snapshot_id':'index-1','index_snapshot_hash':'index-hash','candidates':[],
            'closure_refs':[],'requested_dimension_refs':[],'coverage':[]}
        req={'id':rid,'requirement_id':rid,'requirement_version':1,'clarification_parse_version':1,'question':intent['question'],
            'intent_frame':intent,'intent_frame_hash':canonical_hash(intent),'state':'DRAFT',
            'error':None,'answers':[{'clarification_id':'semantic','slot':'semantic_definition',
                'question':'请说明统计方式','answer':'按订单编号去重计数'}],
            'clarifications':[],'bundle':bundle,'bundle_content_hash':canonical_hash(bundle),
            'metadata_discovery':discovery}
        payload=copy.deepcopy(self.row.payload);payload.update(requirements=[req],state='DRAFT',trace_id='trace-metadata')
        payload['pinned_deployment']={'release_id':'release-1','release_hash':'release-hash',
            'index_snapshot_id':'index-1','index_snapshot_hash':'index-hash'}
        payload['model_selection']={'model':'model-test'};self.row.payload=payload
        self.row.state='DRAFT';self.db.commit()
        job=AgentJob(id='job-metadata',workspace_id=31,run_id='run-test',requirement_id=rid,kind='CLARIFY',status='QUEUED')
        self.db.add(job);self.db.commit()
        parse_result={'output':{'intent':copy.deepcopy(intent)},'model':'model-test','tool_events':[]}
        plan={'name':'订单数','definition':'按订单编号去重计数',
            'catalog_snapshot_id':'catalog-1','datasource_id':'3','dataset_id':'dwd.orders',
            'operation':'COUNT_DISTINCT','measure_field_id':'order_id','time_field_id':'created_at',
            'dimension_field_ids':[],'filters':[],'unit_label':'个','null_policy':'EXCLUDE_AND_WARN'}
        candidate_result={'output':{'candidate_plan':plan},'model':'model-test','tool_events':[]}
        with patch('app.agent_worker.SessionLocal',sessionmaker(self.engine,expire_on_commit=False)), \
             patch('app.agent_worker.pinned_model_vocabulary',return_value={}), \
             patch('app.agent_worker.check_run_access'), \
             patch('app.model_runtime.refine_intent',return_value=parse_result), \
             patch('app.model_runtime.derive_metadata_candidate',return_value=candidate_result):
            clarification_job('job-metadata')
        self.db.expire_all();self.db.refresh(self.row)
        req=self.row.payload['requirements'][0]
        self.assertEqual(req['state'],'TARGET_CONFIRMATION')
        self.assertNotIn('metadata_candidate', req)

        with patch('app.agent.check_run_access'), \
             patch('app.semantic.retrieve',return_value=copy.deepcopy(bundle)), \
             patch('app.semantic.metadata_candidates_for_bundle',return_value=discovery):
            self.db.refresh(self.row);req=self.row.payload['requirements'][0]
            body={'expected_run_version':self.row.run_version,'expected_requirement_version':req['requirement_version'],
                'intent_frame_hash':req['intent_frame_hash']}
            response=self.client.post('/api/workspaces/31/agent/runs/run-test/requirements/'+rid+'/confirm-intent',
                json=body,headers={'Idempotency-Key':'confirm-target-metadata'})
        self.assertEqual(response.status_code,200,response.text)
        self.db.refresh(self.row);req=self.row.payload['requirements'][0]
        self.assertEqual(req['state'],'DRAFT')
        metadata_id=self.db.scalar(select(AgentJob).where(AgentJob.kind=='METADATA')).id
        from app.agent_metadata import metadata_job
        with patch('app.agent_metadata.SessionLocal',sessionmaker(self.engine,expire_on_commit=False)), \
             patch('app.agent_worker.SessionLocal',sessionmaker(self.engine,expire_on_commit=False)), \
             patch('app.agent.check_run_access'), \
             patch('app.model_runtime.derive_metadata_candidate',return_value=candidate_result), \
             patch('app.agent_metadata.source_check',return_value={'read_only':True,'no_data':False,'measure_row_counts':{'source_rows':3,'valid_measure_rows':3}}), \
             patch('app.semantic.retrieve',return_value=copy.deepcopy(bundle)), \
             patch('app.semantic.metadata_candidates_for_bundle',return_value=discovery):
            metadata_job(metadata_id)
        self.db.expire_all();self.db.refresh(self.row);req=self.row.payload['requirements'][0]
        self.assertEqual(req['state'],'NEED_CONFIRMATION')
        self.assertTrue(req['metric_card']['candidate_only'])
        self.assertIn('未命中已发布业务指标',req['metric_card']['notice'])

        with patch('app.agent.check_run_access'):
            self.db.refresh(self.row);req=self.row.payload['requirements'][0]
            body={'expected_run_version':self.row.run_version,'expected_requirement_version':req['requirement_version'],
                'bundle_content_hash':req['bundle_content_hash'],'metric_card_hash':req['metric_card_hash']}
            response=self.client.post('/api/workspaces/31/agent/runs/run-test/requirements/'+rid+'/confirm-metric',
                json=body,headers={'Idempotency-Key':'confirm-metadata-caliber'})
        self.assertEqual(response.status_code,200,response.text)
        self.db.refresh(self.row);req=self.row.payload['requirements'][0]
        self.assertEqual(req['state'],'PREVIEW_READY')
        self.assertIn('COUNT(DISTINCT "b"."order_id")',req['preview']['sql'])
        self.assertIn('"dwd"."fct_orders"',req['preview']['sql'])
        self.assertFalse(req['query_frame']['business_approved'])


if __name__ == '__main__':
    unittest.main()
