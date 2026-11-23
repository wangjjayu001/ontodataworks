"""Model vocabulary must not disclose unpinned drafts or hidden dependencies."""
import copy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"backend"))
os.environ.setdefault("DATABASE_URL","sqlite://")
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"]=Fernet.generate_key().decode()
from app.agent_worker import pinned_model_vocabulary
from app.agent_planner import PlanningBlocked
from app.semantic import sha
from app.semantic_models import SemanticRelease,SemanticIndex,SemanticGovernance


class VocabularyTests(unittest.TestCase):
    def setUp(self):
        self.metric_ref={"workspace_id":71,"asset_id":"metric:new-account-visits","asset_type":"metric","revision":2}
        self.entity_ref={"workspace_id":71,"asset_id":"entity:session","asset_type":"entity","revision":1}
        self.entity={**self.entity_ref,"name":"会话实体","aliases":[],"validity":{"from":None,"to":None},"spec":{}}
        self.metric={**self.metric_ref,"name":"访问账户数","aliases":[{"text":"访问客户账户量","language":"zh-CN","kind":"EXACT_ALIAS","scope":"local_validation"}],"validity":{"from":None,"to":None},"spec":{"entity_ref":self.entity_ref}}
        self.assets={("metric:new-account-visits",2):self.metric,("entity:session",1):self.entity}
        self.hidden=set();self.physical_allowed=True;self.resolved=[]
        content={"items":[{"asset_ref":self.metric_ref,"content_hash":sha(self.metric)},{"asset_ref":self.entity_ref,"content_hash":sha(self.entity)}],"catalog_snapshots":[{"id":"snapshot_fixed","content_hash":"0"*64}]}
        self.release=SimpleNamespace(id="release_fixed",workspace_id=71,scope="local_validation",content=content,content_hash=sha(content))
        index_content={"release_id":"release_fixed"}
        self.index=SimpleNamespace(workspace_id=71,release_id=self.release.id,release_hash=self.release.content_hash,content=index_content,content_hash=sha(index_content))
        self.deployment={"release_id":self.release.id,"release_hash":self.release.content_hash,"index_snapshot_id":"index_fixed","index_snapshot_hash":self.index.content_hash}
        def get(model,key):
            if model is SemanticRelease:return self.release
            if model is SemanticIndex:return self.index
            if model is SemanticGovernance:return None
            raise AssertionError(model)
        self.db=SimpleNamespace(get=get)
        def resolve(_db,_wid,ref):
            identity=(ref["asset_id"],ref["revision"]);self.resolved.append(identity)
            return self.assets[identity]
        self.patches=[patch("app.semantic.resolve_asset",side_effect=resolve),patch("app.semantic.can_read_asset",side_effect=lambda _d,_w,_u,a:a["asset_id"] not in self.hidden),patch("app.semantic.can_read_physical",side_effect=lambda *_a:self.physical_allowed),patch("app.semantic.semantic_access")]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def vocabulary(self):return pinned_model_vocabulary(self.db,71,SimpleNamespace(role="admin",enabled=True),self.deployment)

    def test_disabled_actor_cannot_send_deployed_vocabulary(self):
        with self.assertRaises(PlanningBlocked):pinned_model_vocabulary(self.db,71,SimpleNamespace(role="admin",enabled=False),self.deployment)

    def test_local_scope_role_downgrade_cannot_send_vocabulary(self):
        with self.assertRaises(PlanningBlocked):pinned_model_vocabulary(self.db,71,SimpleNamespace(role="viewer",enabled=True),self.deployment)

    def refresh_hash(self):
        for item in self.release.content["items"]:item["content_hash"]=sha(self.assets[(item["asset_ref"]["asset_id"],item["asset_ref"]["revision"])])
        self.release.content_hash=sha(self.release.content);self.deployment["release_hash"]=self.release.content_hash;self.index.release_hash=self.release.content_hash

    def test_new_deployed_id_names_available_without_id_hint(self):
        result=self.vocabulary()
        self.assertEqual(result["metric_vocabulary"],["访问账户数","访问客户账户量"])
        self.assertNotIn(self.metric_ref["asset_id"],json.dumps(result))

    def test_newer_undeployed_draft_never_resolved_or_in_context(self):
        self.assets[("metric:new-account-visits",3)]={**self.metric,"revision":3,"name":"尚未发布敏感草稿","aliases":["未部署同义词"]}
        result=self.vocabulary()
        self.assertNotIn("尚未发布",json.dumps(result,ensure_ascii=False))
        self.assertNotIn(("metric:new-account-visits",3),self.resolved)

    def test_readable_metric_with_hidden_entity_omitted(self):
        self.hidden.add("entity:session")
        self.assertEqual(self.vocabulary()["metric_vocabulary"],[])

    def test_readable_metric_with_hidden_physical_dependency_omitted(self):
        self.metric["spec"]["field_ref"]={"catalog_snapshot_id":"snapshot_fixed","datasource_id":"3","dataset_id":"sessions","field_id":"customer_id"}
        self.refresh_hash();self.physical_allowed=False
        self.assertEqual(self.vocabulary()["metric_vocabulary"],[])

    def test_unpinned_dependency_omitted(self):
        self.metric["spec"]["entity_ref"]={**self.entity_ref,"revision":2}
        self.refresh_hash()
        self.assertEqual(self.vocabulary()["metric_vocabulary"],[])
        self.assertNotIn(("entity:session",2),self.resolved)


if __name__=="__main__":
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(VocabularyTests))
    report={"completed_at":datetime.now(timezone.utc).isoformat(),"scope":"Pure pinned-release/current-policy vocabulary regressions; no inference or DB writes","passed":result.wasSuccessful(),"test_count":result.testsRun,"failures":[{"name":str(t),"detail":d} for t,d in result.failures],"errors":[{"name":str(t),"detail":d} for t,d in result.errors]}
    out=ROOT/"evidence/v1.0/model-vocabulary-integrity.json";out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
