"""A technical source or missing archive must never approve a business standard."""
import os,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('DATABASE_URL','sqlite://')
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'backend'))
if 'ENCRYPTION_KEY' not in os.environ:
    from cryptography.fernet import Fernet
    os.environ['ENCRYPTION_KEY']=Fernet.generate_key().decode()
from app.pipeline import validate_item
from app.pipeline_contracts import GovernanceBody,StandardContent
from fastapi import HTTPException

class StandardEvidenceTests(unittest.TestCase):
    def run_case(self,kind='BUSINESS_SPEC',authority='BUSINESS_APPROVED',archived=True,approvals=True):
        source={'workspace_id':9,'asset_id':'source:test','asset_type':'source','revision':1,'validity':{'from':None,'to':None},'spec':{'source_kind':kind,'authority':authority}}
        content=StandardContent(category='null',definition='业务必填规范',value='not_null',owner_id=2,source_ref={k:source[k] for k in ['workspace_id','asset_id','asset_type','revision']}).model_dump(mode='json')
        body=GovernanceBody(kind='standard',name='必填规则',status='APPROVED',revision=1,content=content)
        user=SimpleNamespace(id=2,role='developer',enabled=True);db=SimpleNamespace(get=lambda *_:user)
        old=SimpleNamespace(status='REVIEW',content=content,actor_id=1)
        with patch('app.pipeline.require_actor'),patch('app.semantic.resolve_asset',return_value=source),patch('app.semantic.can_read_asset',return_value=True),patch('app.semantic.validity_active',return_value=True),patch('app.semantic.verify_archived_source',return_value=archived),patch('app.semantic.valid_approvals',return_value=[{'reviewer_role':'BUSINESS_OWNER'}] if approvals else []):
            return validate_item(db,9,body,user,old)
    def test_technical_qa_cannot_become_business_approved_standard(self):
        with self.assertRaises(HTTPException):self.run_case(kind='TECHNICAL_QA',authority='TECHNICAL_ONLY')
    def test_missing_archive_or_real_approval_is_rejected(self):
        for options in [{'archived':False},{'approvals':False},{'authority':'UNREVIEWED'}]:
            with self.subTest(options=options),self.assertRaises(HTTPException):self.run_case(**options)
    def test_fixed_review_with_archived_business_evidence_can_be_approved(self):
        self.assertEqual(self.run_case()['value'],'not_null')

if __name__=='__main__':unittest.main()
