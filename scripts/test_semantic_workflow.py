"""Real HTTP route tests for governance with isolated identities and DB state."""
import hashlib
import json
import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"backend"))
os.environ.setdefault("DATABASE_URL","sqlite://")
from fastapi import FastAPI,Request,HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import Base,User,Workspace,Membership
from app.security import identity,db_session
from app.semantic import router,sha,canonical,retrieve,check_bundle_access
from app.semantic_models import SemanticSnapshot
from app.semantic_seed import make_asset,asset_ref


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db=Session(self.engine,expire_on_commit=False)
        for uid,name,role in [(1,"admin","admin"),(2,"steward","developer"),(3,"business-owner","developer"),(4,"data-owner","developer"),(5,"publisher","developer"),(6,"viewer","viewer")]:
            self.db.add(User(id=uid,username=name,display_name=name,role=role,password_hash="test-not-login",enabled=1))
            self.db.add(Membership(user_id=uid,workspace_id=11))
        self.db.add(Workspace(id=11,name="isolated-workflow-test",description="not default local data"))
        unknown={"policy_id":None,"revision":None,"resolution":"UNKNOWN"}
        field={"field_id":"id","name":"id","data_type":"INTEGER","nullable":False,"semantic_role":"IDENTIFIER","unit":None,"classification":"INTERNAL","business_definition":{"name_zh":"标识","definition":"隔离测试标识","aliases":[],"grain":None,"legacy_unit_text":None,"owner_ids":[],"steward_ids":[],"quality_rule_refs":[],"provenance_refs":[],"policy_ref":unknown}}
        dataset={"dataset_id":"test.objects","datasource_id":"1","connector_kind":"LOCAL_FIXTURE","namespace":"test","physical_name":"objects","logical_locator":"test.objects","schema_fingerprint":"0"*64,"fingerprint_algorithm":"test-fixture","fields":[field],"key_constraints":[],"policy_ref":unknown,"constraints_state":"UNKNOWN"}
        snapshot={"workspace_id":11,"catalog_snapshot_id":"catalog:workflow","source_revision":"test","datasets":[dataset]}
        self.db.add(SemanticSnapshot(id="catalog:workflow",workspace_id=11,content=snapshot,content_hash=sha(snapshot)))
        self.db.commit()
        self.app=FastAPI();self.app.include_router(router)
        def auth(request:Request):return self.db.get(User,int(request.headers.get("X-Test-User","1")))
        self.app.dependency_overrides[identity]=auth
        self.app.dependency_overrides[db_session]=lambda:self.db
        self.client=TestClient(self.app)
        self.base="/api/workspaces/11/semantic"

    def tearDown(self):
        self.db.close();self.engine.dispose()

    def call(self,path,body=None,user=1,status=200,version=None,key=None):
        headers={"X-Test-User":str(user),"Idempotency-Key":key or uuid.uuid4().hex}
        if version is not None:headers["If-Match"]=str(version)
        r=self.client.post(self.base+path,json=body,headers=headers) if body is not None else self.client.get(self.base+path,headers=headers)
        self.assertEqual(r.status_code,status,r.text)
        return r.json().get("data",r.json())

    def configure(self):
        for uid,role in [(2,"STEWARD"),(3,"BUSINESS_OWNER"),(4,"DATA_OWNER"),(5,"PUBLISHER")]:self.call("/assignments",{"user_id":uid,"role":role,"asset_id":"*","active":True})
        return self.call("/policies",{"policy_id":"workflow-read","allowed_user_ids":[1,2,3,4,5,6],"allowed_roles":[],"allow_execution":False,"description":"isolated workflow objects"})["policy_ref"]

    def create_source(self,policy):
        raw=canonical({"business_definition":"订单是在本测试规范中定义的购买交易。"})
        source=make_asset(11,"source","workflow","业务规范","独立测试业务规范",{"source_kind":"BUSINESS_SPEC","uri":"archive://workflow","source_revision":"test/1","content_sha256":hashlib.sha256(raw.encode()).hexdigest(),"classification":"INTERNAL","authority":"UNREVIEWED"})
        source["owner_ids"]=["3"];source["steward_ids"]=["2"];source["policy_ref"]=policy
        item=self.call("/assets",{"asset_revision":source},user=2)
        self.call("/sources/source:workflow/archive",{"revision":1,"raw_text":raw},user=2)
        return item

    def verify_submit(self,item):
        aid=item["asset_ref"]["asset_id"];gov=item["governance_record"]
        checked=self.call("/governance/"+aid+"/verify",{"expected_record_version":gov["record_version"],"content_hash":item["content_hash"]},user=2,version=gov["record_version"])
        self.assertEqual(checked["governance_record"]["validation_state"],"PASS")
        gov=checked["governance_record"]
        submitted=self.call("/governance/"+aid+"/submit",{"expected_record_version":gov["record_version"],"content_hash":item["content_hash"],"dependency_hash":gov["dependency_hash"]},user=2,version=gov["record_version"])
        self.assertEqual(submitted["governance_record"]["workflow_state"],"IN_REVIEW")
        return submitted["governance_record"]

    def approve(self,aid,gov):
        approved=self.call("/governance/"+aid+"/decisions",{"expected_record_version":gov["record_version"],"decision":"APPROVE","reviewer_role":"BUSINESS_OWNER","reason":"独立测试身份核对业务定义及来源"},user=3,version=gov["record_version"])
        self.assertEqual(approved["governance_record"]["workflow_state"],"APPROVED")
        return approved

    def test_full_positive_workflow_publish_index_activate_and_revoke(self):
        policy=self.configure();source=self.create_source(policy)
        self.approve("source:workflow",self.verify_submit(source))
        domain=make_asset(11,"domain","workflow","订单域","测试业务规范定义的订单域",{"parent_ref":None,"scope_notes":"仅隔离测试"},[source["asset_ref"]])
        domain.update(owner_ids=["3"],steward_ids=["2"],policy_ref=policy)
        item=self.call("/assets",{"asset_revision":domain},user=2)
        self.approve("domain:workflow",self.verify_submit(item))
        rel=self.call("/releases",{"scope":"production","asset_refs":[item["asset_ref"]],"reason":"isolated workflow acceptance"},user=5)
        self.assertEqual(rel["scope"],"production")
        self.assertEqual(len(rel["release"]["items"]),2)
        idx=self.call("/index-builds",{"release_ref":rel["release_ref"]},user=5)
        dep=self.call("/deployments/production/activate",{"release_ref":rel["release_ref"],"index_snapshot_ref":idx["index_snapshot_ref"],"expected_pointer_version":0},user=5,version=0)
        self.assertEqual(dep["deployment"]["pointer_version"],1)
        publisher=self.db.get(User,5)
        bundle=retrieve(self.db,11,publisher,"isolated_run","r1",{"question":"订单域","start":"2025-01-01","end_exclusive":"2026-01-01"},dep["deployment"])
        check_bundle_access(self.db,11,publisher,bundle)
        self.call("/policies/workflow-read/revoke",{"revision":1})
        with self.assertRaises(HTTPException) as revoked:check_bundle_access(self.db,11,publisher,bundle)
        self.assertEqual(revoked.exception.status_code,403)
        self.call("/assets/domain:workflow",user=3,status=404)
        self.call("/deployments/production/activate",{"release_ref":rel["release_ref"],"index_snapshot_ref":idx["index_snapshot_ref"],"expected_pointer_version":1},user=5,version=1,status=409)

    def test_cas_idempotency_duties_and_policy_no_execution(self):
        policy=self.configure();source=self.create_source(policy)
        gov=self.verify_submit(source)
        self.call("/governance/source:workflow/decisions",{"expected_record_version":gov["record_version"],"decision":"APPROVE","reviewer_role":"BUSINESS_OWNER","reason":"cannot self elevate"},user=2,version=gov["record_version"],status=403)
        self.call("/governance/source:workflow/decisions",{"expected_record_version":1,"decision":"APPROVE","reviewer_role":"BUSINESS_OWNER","reason":"stale"},user=3,version=1,status=409)
        self.approve("source:workflow",gov)
        self.call("/policies",{"policy_id":"illegal-execute","allowed_user_ids":[1],"allow_execution":True},status=422)
        key=uuid.uuid4().hex
        b={"user_id":6,"role":"STEWARD","asset_id":"source:another","active":True}
        first=self.call("/assignments",b,key=key);second=self.call("/assignments",b,key=key)
        self.assertEqual(first,second)
        self.call("/assignments",{**b,"active":False},key=key,status=409)


if __name__=="__main__":unittest.main(verbosity=2)
