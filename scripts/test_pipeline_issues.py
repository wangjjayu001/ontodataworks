"""Verify that successful unrelated work cannot close the original problem."""
import os, sys, unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch
os.environ.setdefault('DATABASE_URL', 'sqlite://')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'backend'))
from app.pipeline_contracts import IssueContent
from app.pipeline_issues import validate_issue, issue_category
from fastapi import HTTPException


class IssueClosureTests(unittest.TestCase):
    def setUp(self):
        self.when = datetime.now(timezone.utc)
        self.origin = NS(definition_id=7, snapshot={'kind':'sql','config':{'target_asset_id':4}})
        self.retest = NS(definition_id=7, status='SUCCEEDED', snapshot={'kind':'quality','config':{'target_asset_id':4}}, created_at=self.when+timedelta(seconds=2), completed_at=self.when+timedelta(seconds=3))
        self.user = NS(id=3)
        self.old = NS(content={'category':'quality','run_id':'origin','asset_id':4}, status='FIXED', updated_at=self.when, actor_id=1, history=[{'actor_id':2}])
    def close(self, category='quality', **extra):
        content = IssueContent(description='问题', category=category, run_id='origin', asset_id=4, retest_run_id='retest', resolution='修复完成', verification_note='独立复核通过', **extra).model_dump(mode='json')
        def owned(_db, cls, _wid, key):
            if cls.__name__=='Asset':return NS(id=4)
            return {'origin':self.origin,'retest':self.retest}[key]
        with patch('app.pipeline_issues.owned',side_effect=owned):
            return validate_issue(NS(),9,NS(status='CLOSED'),content,self.user,self.old)
    def test_quality_requires_same_asset(self):
        self.retest.snapshot['config']['target_asset_id']=5
        with self.assertRaises(HTTPException):self.close()
    def test_quality_cannot_use_old_success(self):
        self.retest.created_at=self.when-timedelta(seconds=1)
        with self.assertRaises(HTTPException):self.close()
    def test_quality_cannot_reclassify_to_feedback(self):
        with self.assertRaises(HTTPException):self.close('feedback')
    def test_quality_success_stamps_actual_reviewer(self):
        result=self.close(verification_actor_id=999,verified_at='forged')
        self.assertEqual(result['verification_actor_id'],3)
        self.assertNotEqual(result['verified_at'],'forged')
    def test_cannot_substitute_other_issue_asset(self):
        self.old.content['asset_id']=5
        with self.assertRaises(HTTPException):self.close()
    def test_task_requires_same_definition(self):
        self.old.content['category']='task';self.retest.definition_id=8
        with self.assertRaises(HTTPException):self.close('task')
    def test_task_success_can_use_real_retry_completion(self):
        self.old.content['category']='task';self.retest.created_at=self.when-timedelta(days=1)
        self.assertEqual(self.close('task')['verification_actor_id'],3)
    def test_feedback_requires_independent_reviewer(self):
        self.old.content['category']='feedback';self.user.id=2
        with self.assertRaises(HTTPException):self.close('feedback')
    def test_feedback_can_be_human_verified_without_quality_task(self):
        self.old.content['category']='feedback'
        self.assertEqual(self.close('feedback')['verification_actor_id'],3)
    def test_close_requires_separate_fixed_record(self):
        self.old.status='OPEN'
        with self.assertRaises(HTTPException):self.close()
    def test_legacy_feedback_and_drift_keep_their_original_categories(self):
        self.assertEqual(issue_category(None,9,{'event_key':'agent-feedback:old'}),'feedback')
        self.assertEqual(issue_category(None,9,{'event_key':'catalog:12:4'}),'schema')
        self.assertEqual(issue_category(None,9,{'event_key':None}),'quality')

if __name__=='__main__':unittest.main()
