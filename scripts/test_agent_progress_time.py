"""Regression for month comparison confirmation and durable model progress."""
import copy
import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import patch, MagicMock
from concurrent.futures import TimeoutError as FutureTimeout

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'scripts')]
import test_agent_clarifications as fixtures
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.agent import view
from app.agent_time import infer_periods, set_periods
from app.agent_worker import normalize_intent, model_progress
from app.agent_models import AgentJob, AgentEvent
from app.agent_clarification import merge_clarified_intent
from app.model_runtime import invoke, MODEL_CONTRACT_VERSION, EXPLAIN_SCHEMA, canonical_hash
from codex_bridge import Bridge

QUESTION = '鞋类25年5月环比6月的roi如何'
PERIODS = [{'label': '2025年5月', 'start': '2025-05-01', 'end_exclusive': '2025-06-01'},
           {'label': '2025年6月', 'start': '2025-06-01', 'end_exclusive': '2025-07-01'}]


class ProgressTimeTests(unittest.TestCase):
    setUp = fixtures.ClarificationFlowTests.setUp
    tearDown = fixtures.ClarificationFlowTests.tearDown

    def seed_question(self):
        payload = copy.deepcopy(self.row.payload)
        req = payload['requirements'][0]
        req['intent_frame'].update(question=QUESTION, original_question=QUESTION, start=None, end_exclusive=None,
            analysis={'kind': 'PERIOD_COMPARISON', 'base_period': {'start': None, 'end_exclusive': None},
                'comparison_period': {'start': None, 'end_exclusive': None}, 'measures': ['GROWTH_RATE']})
        req['clarifications'] = [{'id': 'old-time', 'slot': 'time', 'question': '起止时间', 'options': []},
            {'id': 'old-direction', 'slot': 'comparison_direction', 'question': '比较方向', 'options': []},
            {'id': 'timezone', 'slot': 'timezone', 'question': '源库时间', 'options': []}]
        req['intent_frame_hash'] = canonical_hash(req['intent_frame'])
        self.row.payload = payload
        self.db.commit()
        return req

    def time_command(self, periods, expected=200):
        self.db.refresh(self.row)
        req = self.row.payload['requirements'][0]
        with patch('app.agent.check_run_access'):
            result = self.client.post('/api/workspaces/31/agent/runs/run-test/requirements/requirement-test/time-periods',
                json={'expected_run_version': self.row.run_version, 'expected_requirement_version': req['requirement_version'], 'periods': periods})
        self.assertEqual(result.status_code, expected, result.text)
        self.db.refresh(self.row)
        return result.json().get('data', result.json())

    def test_explicit_may_and_june_are_two_editable_periods_even_when_model_dates_are_empty(self):
        item = {'question': QUESTION, 'metric_terms': ['ROI'], 'dimension_terms': ['商品类目'],
            'filters': [{'field': '商品类目', 'operator': '=', 'value': '鞋类'}],
            'analysis': {'kind': 'PERIOD_COMPARISON'}, 'clarifications': [{'slot': 'comparison_direction', 'question': '比较方向', 'options': []}]}
        intent, clarifications, error = normalize_intent(item, QUESTION)
        self.assertIsNone(error)
        self.assertEqual(intent['time_periods'], PERIODS)
        self.assertIsNone(intent['start'])
        self.assertIsNone(intent['end_exclusive'])
        self.assertEqual(clarifications[0]['slot'], 'time_periods')
        self.assertFalse(any(c['slot'] in {'time', 'comparison_direction'} for c in clarifications))

    def test_calendar_periods_support_cross_year_and_custom_multiple_dates(self):
        intent = {'question': '25年12月同比26年1月', 'analysis': {'kind': 'PERIOD_COMPARISON'}}
        periods = infer_periods(intent)
        self.assertEqual(periods[0]['end_exclusive'], '2026-01-01')
        self.assertEqual(periods[1]['start'], '2026-01-01')
        custom = PERIODS + [{'label': '自定义窗口', 'start': '2025-07-04', 'end_exclusive': '2025-07-18'}]
        set_periods(intent, custom, confirmed=True)
        self.assertEqual(len(intent['time_periods']), 3)
        self.assertIsNone(intent['start'])

    def test_legacy_run_get_projects_period_cards_without_rewriting_history_or_hash(self):
        self.seed_question()
        before = copy.deepcopy(self.row.payload)
        result = view(self.row, self.user)['requirements'][0]
        self.assertEqual(result['suggested_time_periods'], PERIODS)
        self.assertEqual(result['clarifications'][0]['slot'], 'time_periods')
        self.assertEqual(result['intent_frame_hash'], before['requirements'][0]['intent_frame_hash'])
        self.assertEqual(self.row.payload, before)

    def test_confirm_or_reverse_periods_preserves_each_range_and_does_not_execute(self):
        self.seed_question()
        result = self.time_command(list(reversed(PERIODS)))['run']['requirements'][0]
        self.assertTrue(result['intent_frame']['time_periods_confirmed'])
        self.assertEqual(result['intent_frame']['analysis']['base_period']['start'], '2025-06-01')
        self.assertEqual(result['intent_frame']['analysis']['comparison_period']['start'], '2025-05-01')
        self.assertIsNone(result['intent_frame']['start'])
        self.assertEqual(result['clarifications'], [])
        self.assertEqual(result['state'], 'TARGET_CONFIRMATION')
        self.assertEqual(result['answers'][-1]['actor_id'], 1)
        self.assertNotIn('preview', result)
        self.assertFalse(self.db.scalars(select(AgentJob)).all())

    def test_single_period_cannot_silently_replace_comparison_and_invalid_dates_are_rejected(self):
        self.seed_question()
        before = copy.deepcopy(self.row.payload)
        self.time_command(PERIODS[:1], expected=422)
        self.time_command([*PERIODS[:1], {'start': '2025-02-30', 'end_exclusive': '2025-03-02'}], expected=422)
        self.assertEqual(self.row.payload, before)

    def test_reparse_keeps_user_confirmed_periods_and_direction(self):
        self.seed_question()
        self.time_command(list(reversed(PERIODS)))
        previous = self.row.payload['requirements'][0]
        parsed = copy.deepcopy(previous['intent_frame'])
        parsed.update(time_periods=PERIODS, time_periods_confirmed=False)
        parsed['analysis'] = {'kind': 'NONE'}
        merged, pending = merge_clarified_intent(previous, parsed,
            [{'id': 'repeated', 'slot': 'time_periods', 'question': '重复时间'}])
        self.assertEqual(merged['time_periods'], list(reversed(PERIODS)))
        self.assertEqual(merged['analysis']['base_period']['start'], '2025-06-01')
        self.assertFalse(pending)

    def test_progress_persists_real_phases_and_cancellation_blocks_late_updates(self):
        payload = copy.deepcopy(self.row.payload)
        payload.update(requirements=[], state='DRAFT')
        self.row.payload = payload
        self.row.state = 'DRAFT'
        self.db.add(AgentJob(id='progress-job', workspace_id=31, run_id=self.row.id, kind='PARSE', status='RUNNING'))
        self.db.commit()
        with patch('app.agent_worker.SessionLocal', sessionmaker(self.engine, expire_on_commit=False)):
            model_progress('progress-job', 'CONNECTING')
            model_progress('progress-job', 'MODEL_RUNNING')
            self.db.refresh(self.row)
            self.assertEqual(self.row.payload['model_progress']['phase'], 'MODEL_RUNNING')
            self.assertEqual(len(self.db.scalars(select(AgentEvent)).all()), 2)
            payload = copy.deepcopy(self.row.payload)
            payload['cancel_requested'] = True
            self.row.payload = payload
            self.row.state = 'CANCEL_REQUESTED'
            self.db.commit()
            model_progress('progress-job', 'VALIDATING')
        self.db.refresh(self.row)
        self.assertEqual(self.row.payload['model_progress']['phase'], 'MODEL_RUNNING')
        self.assertEqual(self.row.state, 'CANCEL_REQUESTED')

    def test_progress_polling_never_resends_model_invocation(self):
        response = {'model_contract_version': MODEL_CONTRACT_VERSION, 'output_schema_hash': canonical_hash(EXPLAIN_SCHEMA),
            'output': {'summary': '完成', 'limitations': [], 'cited_ids': []}}
        phases = []
        future = MagicMock()
        future.result.side_effect = [FutureTimeout(), response]
        executor = MagicMock()
        executor.__enter__.return_value = executor
        def submit(fn, *args):
            fn(*args)
            return future
        executor.submit.side_effect = submit
        def bridge(path, *args, **kwargs):
            return response if path == '/invoke' else {'phase': 'MODEL_RUNNING'}
        with patch('app.model_runtime.ThreadPoolExecutor', return_value=executor), patch('app.model_runtime._bridge_call', side_effect=bridge) as call:
            invoke('explain', {}, request_id='progress-test', progress_callback=phases.append)
        self.assertEqual(phases, ['CONNECTING', 'MODEL_RUNNING', 'VALIDATING'])
        self.assertEqual(sum(c.args[0] == '/invoke' for c in call.call_args_list), 1)

    def test_bridge_status_contains_phase_metadata_only_and_is_bounded(self):
        bridge = object.__new__(Bridge)
        bridge.progress = {}
        bridge.progress_lock = threading.Lock()
        for index in range(270):
            bridge.report_progress(str(index), 'MODEL_RUNNING')
        self.assertEqual(len(bridge.progress), 256)
        self.assertEqual(set(bridge.request_progress('269')), {'phase', 'started_at', 'updated_at'})


if __name__ == '__main__':
    unittest.main()
