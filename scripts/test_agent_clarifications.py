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

    def test_comparison_answers_use_one_requirement_contract_and_preserve_periods(self):
        self.command('direction', '2024年为基期，比较2025年')
        data = self.command('growth', '两者都要')
        req = data['run']['requirements'][0]
        self.assertEqual(req['state'], 'DRAFT')
        self.assertEqual(len(req['intent_frame']['clarification_answers']), 2)
        jobs = self.db.scalars(select(AgentJob)).all()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].kind, 'CLARIFY')
        self.worker(jobs[0].id)
        requirements = self.db.get(AgentRun, 'run-test').payload['requirements']
        self.assertEqual(len(requirements), 1)
        self.assertEqual(requirements[0]['state'], 'TARGET_CONFIRMATION')
        self.assertEqual(requirements[0]['id'], 'requirement-test')
        self.assertEqual(requirements[0]['intent_frame']['analysis'], {
            'kind': 'PERIOD_COMPARISON',
            'base_period': {'start': '2024-01-01', 'end_exclusive': '2025-01-01'},
            'comparison_period': {'start': '2025-01-01', 'end_exclusive': '2026-01-01'},
            'measures': ['GROWTH_RATE', 'ABSOLUTE_CHANGE']})

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


if __name__ == '__main__':
    unittest.main()
