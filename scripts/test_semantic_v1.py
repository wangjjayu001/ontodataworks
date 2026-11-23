"""Meaningful semantic regressions on isolated SQLite metadata fixtures.

Live API and source-result parity are verified separately in deployment tests.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"backend"))
os.environ.setdefault("DATABASE_URL","sqlite://")
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.db import Base, User, Workspace, Asset
from app.semantic_models import *
from app.semantic import *
from app.semantic_seed import seed_local


class SemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=create_engine("sqlite://")
        Base.metadata.create_all(cls.engine)
        cls.db=Session(cls.engine,expire_on_commit=False)
        db=cls.db
        cls.user=User(id=1,username="test-admin",display_name="test",password_hash="not-a-real-password",role="admin",enabled=1)
        db.add_all([cls.user,Workspace(id=3,name="semantic-test",description="isolated")]);db.commit()
        payload=json.loads((ROOT/"backend/app/contracts/local-seed.json").read_text("utf-8"))
        catalog=json.loads(payload["metadata_catalog_text"])
        for i,t in enumerate(catalog["tables"],1):
            logical=t["object_id"];physical=payload["physical_mapping"][logical];ns,name=physical.split(".")
            cols=[]
            for c in catalog["columns"]:
                if c["schema_name"]+"."+c["table_name"]==logical:
                    cols.append({"name":c["column_name"],"type":c.get("data_type","text"),"nullable":None,"comment":c.get("column_name_zh",c["column_name"]),"primary_key":c.get("is_primary_key",False),"primary_key_position":1 if c.get("is_primary_key") else None,"foreign_keys":[]})
            db.add(Asset(id=i,workspace_id=3,datasource_id=1,namespace=ns,table_name=name,name=t.get("table_name_zh",name),description=t.get("description",""),columns=cols,fingerprint=sha(cols),state="active",layer=ns.upper()))
        db.commit()
        cls.payload=payload
        cls.report=seed_local(db,3,cls.user,payload)

    def intent(self,question,metric=None,dims=None,start="2025-01-01",end="2026-01-01"):
        value={"question":question,"start":start,"end_exclusive":end,"dimension_ids":dims or [],"metric_terms":[metric] if metric else []}
        return value

    def bundle(self,intent):
        return retrieve(self.db,3,self.user,new_id("test_run"),"r1",intent)

    def test_01_inventory_and_typed_contract(self):
        self.assertEqual(self.report["staged"],429)
        self.assertEqual(self.report["catalog_datasets"],42)
        self.assertEqual(self.report["catalog_fields"],282)
        roles={r.asset_type for r in current_assets(self.db,3)}
        self.assertEqual(roles,set(TYPES))
        for row in current_assets(self.db,3):validate(row.content,"asset_revision")

    def test_02_seed_idempotent_no_approval(self):
        count=len(current_assets(self.db,3))
        again=seed_local(self.db,3,self.user,self.payload)
        self.assertEqual(count,len(current_assets(self.db,3)))
        self.assertEqual(again["deployment"]["pointer_version"],1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SemanticApproval)),0)
        self.assertEqual(again["production_ready_paths"],0)

    def test_03_order_recall_and_scoped_closure(self):
        b=self.bundle(self.intent("2025年订单量按买家所在地区分组","order_count",["customer_state"]))
        self.assertEqual(b["selected_metric_ref"]["asset_id"],"metric:order_count")
        self.assertEqual(b["selected_binding_ref"]["asset_id"],"binding:order_count.local_v1")
        self.assertEqual(b["next_action"],"NEED_CONFIRMATION")
        fields={d["target_ref"]["field_id"] for d in b["closure_refs"] if d["kind"]=="FIELD"}
        self.assertTrue({"order_id","purchased_at","customer_id","state"}.issubset(fields))
        self.assertTrue(any(d["dimension_scope_ref"] for d in b["closure_refs"] if d["kind"]=="FIELD" and d["target_ref"]["field_id"]=="state"))
        validate(b,"retrieval_bundle",RETRIEVAL)

    def test_04_buyer_status_never_geography(self):
        b=self.bundle(self.intent("2025年订单数按买家状态","order_count",["customer_state"]))
        self.assertIsNone(b["selected_metric_ref"])
        self.assertIsNone(b["selected_binding_ref"])
        self.assertEqual(b["next_action"],"GOVERNANCE_REQUIRED")
        self.assertEqual(b["closure_refs"],[])

    def test_05_amount_currency_unknown_blocked(self):
        b=self.bundle(self.intent("2025年支付金额","payment_total"))
        self.assertEqual(b["next_action"],"BLOCKED_EVIDENCE")
        self.assertTrue(any(c["slot"]=="unit_currency" and c["status"]=="UNKNOWN" for c in b["coverage"]))

    def test_06_unknown_coverage_requires_live_source_gate(self):
        b=self.bundle(self.intent("2027年订单数","order_count",start="2027-01-01",end="2028-01-01"))
        self.assertEqual(b["next_action"],"NEED_CONFIRMATION")
        self.assertTrue(any(c["slot"]=="time" and c["status"]=="UNKNOWN" for c in b["coverage"]))

    def test_07_unimplemented_dimension_blocked(self):
        b=self.bundle(self.intent("订单数按商品类目","order_count",["product_category"]))
        self.assertEqual(b["next_action"],"GOVERNANCE_REQUIRED")

    def test_08_ambiguous_sales_clarifies(self):
        b=self.bundle(self.intent("2025年销售额"))
        self.assertEqual(b["next_action"],"NEED_CLARIFICATION")

    def test_09_cross_workspace_ref_rejected(self):
        target={"workspace_id":4,"asset_id":"metric:order_count","asset_type":"metric","revision":1}
        with self.assertRaises(HTTPException):resolve_asset(self.db,3,target)

    def test_10_type_confusion_and_physical_ownership(self):
        with self.assertRaises(HTTPException):resolve_binding(self.db,3,{"workspace_id":3,"asset_id":"metric:order_count","asset_type":"metric","revision":1})
        snap=self.db.scalar(select(SemanticSnapshot))
        with self.assertRaises(HTTPException):resolve_physical(self.db,3,{"catalog_snapshot_id":snap.id,"datasource_id":"1","dataset_id":"dim.customers","field_id":"order_id"})

    def test_11_production_and_unknown_policy_fail_closed(self):
        with self.assertRaises(HTTPException):create_release(self.db,3,self.user,{"scope":"production"})
        nonadmin=User(id=55,username="reader",display_name="reader",role="viewer",password_hash="unused",enabled=1)
        with self.assertRaises(HTTPException):get_deployment(self.db,3,nonadmin)

    def test_12_revision_cas_and_execution_derives_new_typed_revision(self):
        gov=self.db.get(SemanticGovernance,(3,"binding:session_count.local_v1"))
        old=self.db.get(SemanticRevision,(3,gov.asset_id,gov.revision))
        a=copy.deepcopy(old.content);a["name"]+="新修订"
        with self.assertRaises(HTTPException):save_asset(self.db,3,self.user,a,base_revision=old.revision,expected_record_version=999)
        self.db.rollback()
        gov=self.db.get(SemanticGovernance,(3,a["asset_id"]))
        result=save_asset(self.db,3,self.user,a,base_revision=old.revision,expected_record_version=gov.record_version)
        self.assertEqual(result["asset_ref"]["revision"],old.revision+1)
        self.assertEqual(self.db.get(SemanticRevision,(3,a["asset_id"],old.revision)).content_hash,old.content_hash)
        contract=get_execution_contract(self.db,3,result["asset_ref"])
        self.assertEqual(contract["authority"],"AssetRevision+CatalogSnapshot")
        self.assertEqual(contract["binding_content_hash"],result["content_hash"])
        self.assertIsNone(self.db.get(SemanticContract,(3,a["asset_id"],result["asset_ref"]["revision"])))
        self.db.rollback()

    def test_13_pinned_index_mismatch_rejected(self):
        dep=get_deployment(self.db,3,self.user);dep["index_snapshot_hash"]="0"*64
        with self.assertRaises(HTTPException):retrieve(self.db,3,self.user,"bad","r",self.intent("订单数","order_count"),dep)

    def test_14_evidence_exact_source_validation(self):
        e=self.db.get(SemanticRevision,(3,"evidence:order_key_unique",1)).content
        self.assertTrue(verify_archived_evidence(self.db,3,e))
        bad=copy.deepcopy(e);bad["spec"]["excerpt"]="forged";bad["spec"]["fragment_sha256"]=hashlib.sha256(b"forged").hexdigest()
        self.assertFalse(verify_archived_evidence(self.db,3,bad))

    def test_15_bundle_rechecks_current_withdrawal(self):
        bundle=self.bundle(self.intent("2025年订单数","order_count"))
        check_bundle_access(self.db,3,self.user,bundle)
        gov=self.db.get(SemanticGovernance,(3,"binding:order_count.local_v1"))
        gov.workflow_state="WITHDRAWN";self.db.flush()
        with self.assertRaises(HTTPException):check_bundle_access(self.db,3,self.user,bundle)
        with self.assertRaises(HTTPException):get_execution_contract(self.db,3,bundle["selected_binding_ref"])
        self.db.rollback()

    def test_16_specific_sales_name_overrides_generic_ambiguity(self):
        bundle=self.bundle(self.intent("2025年商品销售额是多少"))
        self.assertEqual(bundle["selected_metric_ref"]["asset_id"],"metric:item_revenue")
        self.assertEqual(bundle["next_action"],"BLOCKED_EVIDENCE")
        generic=self.bundle(self.intent("2025年销售额是多少"))
        self.assertIsNone(generic["selected_metric_ref"])
        self.assertEqual(generic["next_action"],"NEED_CLARIFICATION")

    def test_17_repaired_12_bindings_compile_without_sidecars_or_mixed_revisions(self):
        bindings=[r for r in current_assets(self.db,3) if r.asset_type=="binding"]
        self.assertEqual(len(bindings),12)
        for row in bindings:
            contract=get_execution_contract(self.db,3,ref(row.content))
            self.assertEqual(contract["authority"],"AssetRevision+CatalogSnapshot",row.asset_id)
            self.assertEqual(row.revision,3 if row.content["spec"]["joins"] else 2)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SemanticContract)),0)
        release=self.db.get(SemanticRelease,self.report["deployment"]["release_id"])
        ids=[i["asset_ref"]["asset_id"] for i in release.content["items"]]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(SemanticApproval)),0)
        self.assertEqual(len(self.report["relation_mapping_repair"]["created_revisions"]),5)

    def test_18_longest_exact_business_name_selects_identity_without_reordering_rrf(self):
        bundle=self.bundle(self.intent("2025年按下单时间统计促销订单数，按订单编号去重，返回总计，使用源库时间。"))
        self.assertEqual(bundle["selected_metric_ref"]["asset_id"],"metric:promotion_orders")
        metric_candidates=[candidate for candidate in bundle["candidates"] if candidate["role"]=="metric"]
        self.assertTrue(metric_candidates)
        self.assertEqual(metric_candidates,sorted(metric_candidates,key=lambda item:item["fused_rank"]))

    def test_19_unmatched_metric_retrieves_readable_catalog_for_clarification(self):
        intent=self.intent("2025年统计紫色飞船的笑声数量", "紫色飞船的笑声数量")
        bundle=self.bundle(intent)
        self.assertIsNone(bundle["selected_metric_ref"])
        self.assertEqual(bundle["next_action"], "NEED_CLARIFICATION")

        discovery=metadata_candidates_for_bundle(self.db, 3, self.user, bundle)
        self.assertIsNotNone(discovery)
        self.assertEqual(discovery["match_status"], "CATALOG_DISCOVERY_ONLY")
        self.assertEqual(discovery["release_id"], bundle["release_id"])
        self.assertEqual(discovery["index_snapshot_id"], bundle["index_snapshot_id"])
        self.assertTrue(discovery["tables"])
        self.assertTrue(any(field["field_id"] for table in discovery["tables"] for field in table["fields"]))


if __name__=="__main__":unittest.main(verbosity=2)
