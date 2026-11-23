"""Isolated HTTP regressions for atomic attachments and catalog alias projection."""
import copy
import unittest
from sqlalchemy import select, func
from app.semantic_models import SemanticRevision, SemanticSourceBlob, SemanticSnapshot
from app.semantic import sha, verify_archived_evidence, verify_archived_source
from app.semantic_seed import make_asset
import test_semantic_workflow as workflow


class MaintenanceTests(workflow.WorkflowTests):
    def domain(self):
        return make_asset(11,"domain","attached","附件业务域","按原文确认的业务边界",{"parent_ref":None,"scope_notes":"隔离测试"})

    def test_attachment_and_parent_save_atomically_with_exact_text(self):
        raw='业务定义：订单数量。\r\n  保留空格和换行。'
        key='same-attachment-command'
        result=self.call('/assets',{'asset_revision':self.domain(),'attachments':[{'title':'公开口径','text':raw,'source_kind':'BUSINESS_SPEC','classification':'PUBLIC'}]},key=key)
        self.assertEqual(len(result['attachment_refs']),2)
        evidence=self.db.get(SemanticRevision,(11,result['asset_revision']['provenance_refs'][0]['asset_id'],1)).content
        source=self.db.get(SemanticRevision,(11,evidence['spec']['source_ref']['asset_id'],1)).content
        self.assertTrue(verify_archived_source(self.db,11,source));self.assertTrue(verify_archived_evidence(self.db,11,evidence))
        blob=self.db.get(SemanticSourceBlob,(11,source['asset_id'],source['spec']['content_sha256']))
        self.assertEqual(blob.content['json']['text'],raw);self.assertEqual(source['spec']['authority'],'UNREVIEWED')
        self.assertEqual(result['governance_record']['workflow_state'],'DRAFT')
        repeated=self.call('/assets',{'asset_revision':self.domain(),'attachments':[{'title':'公开口径','text':raw,'source_kind':'BUSINESS_SPEC','classification':'PUBLIC'}]},key=key)
        self.assertEqual(repeated['attachment_refs'],result['attachment_refs'])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SemanticRevision)),3)

    def test_invalid_parent_and_stale_revision_leave_no_orphan_attachments(self):
        invalid=self.domain();invalid['definition']=''
        self.call('/assets',{'asset_revision':invalid,'attachments':[{'title':'材料','text':'原文'}]},status=422)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SemanticRevision)),0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SemanticSourceBlob)),0)
        item=self.call('/assets',{'asset_revision':self.domain()})
        self.call('/assets/domain:attached/revisions',{'asset_revision':self.domain(),'base_revision':99,'expected_record_version':99,'attachments':[{'title':'材料','text':'原文'}]},version=99,status=409)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SemanticRevision)),1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SemanticSourceBlob)),0)

    def test_structured_catalog_alias_builds_and_physical_field_name_remains_searchable(self):
        snapshot=self.db.get(SemanticSnapshot,'catalog:workflow');content=copy.deepcopy(snapshot.content)
        field=content['datasets'][0]['fields'][0];field['business_definition']['aliases']=[{'text':'业务编号','language':'zh-CN','kind':'EXACT_ALIAS','scope':'本字段'}]
        snapshot.content=content;snapshot.content_hash=sha(content);self.db.commit()
        self.call('/assets',{'asset_revision':self.domain()})
        release=self.call('/releases',{'scope':'local_validation'})
        index=self.call('/index-builds',{'release_ref':release['release_ref']})
        from app.semantic_models import SemanticIndex
        row=self.db.get(SemanticIndex,index['index_snapshot']['id'])
        document=next(d for d in row.documents if d['role']=='field')
        self.assertIn('业务编号',document['search_fields']['aliases']);self.assertIn('id',document['search_fields']['aliases'])

if __name__=='__main__':unittest.main()
