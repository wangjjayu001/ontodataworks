"""Safe one-table metadata candidate compilation tests."""
import sys
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import duckdb
import re

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DATABASE_URL", "sqlite://")
from cryptography.fernet import Fernet
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

from app.agent_planner import (  # noqa: E402
    PlanningBlocked, apply_result_policies, canonical_hash, compile_metadata_preview, normalize_metadata_candidate,
)
from app.semantic import metadata_candidates_for_bundle, sha  # noqa: E402
from app.semantic_models import SemanticIndex, SemanticRelease, SemanticSnapshot  # noqa: E402
from app.db import Asset, CatalogMetadata, Datasource  # noqa: E402


def context():
    table = {
        "catalog_snapshot_id": "catalog-1", "datasource_id": "3", "dataset_id": "dwd.orders",
        "catalog_snapshot_hash": "a" * 64, "table_label": "订单事实表",
        "logical_locator": "dwd.orders", "namespace": "dwd", "physical_name": "fct_orders",
        "schema_fingerprint": "b" * 64, "constraints_state": "DECLARED",
        "key_constraints": [{"constraint_id": "pk-orders", "kind": "PRIMARY_KEY",
            "field_ids": ["order_id"]}],
        "fields": [
            {"field_id": "order_id", "name": "order_id", "name_zh": "订单编号",
                "definition": "订单唯一标识", "data_type": "text", "nullable": False,
                "semantic_role": "IDENTIFIER", "unit": None, "classification": "INTERNAL",
                "is_primary": True},
            {"field_id": "created_at", "name": "created_at", "name_zh": "订单创建时间",
                "definition": "订单创建事件时间", "data_type": "timestamp without time zone",
                "nullable": False, "semantic_role": "EVENT_TIME", "unit": None,
                "classification": "INTERNAL", "is_primary": False},
            {"field_id": "category", "name": "category", "name_zh": "商品类别",
                "definition": "商品业务类别", "data_type": "text", "nullable": True,
                "semantic_role": "ATTRIBUTE", "unit": None, "classification": "INTERNAL",
                "is_primary": False},
            {"field_id": "gross_amount", "name": "gross_amount", "name_zh": "订单金额",
                "definition": "订单含运费金额", "data_type": "numeric", "nullable": True,
                "semantic_role": "MEASURE", "unit": None, "classification": "INTERNAL",
                "is_primary": False},
            {"field_id": "status", "name": "status", "name_zh": "订单状态",
                "definition": "订单生命周期状态", "data_type": "text", "nullable": False,
                "semantic_role": "STATUS", "unit": None, "classification": "INTERNAL",
                "is_primary": False},
        ],
    }
    discovery = {
        "schema_version": "query-metadata-context/1.0.0",
        "release_id": "release-1", "release_hash": "c" * 64,
        "index_snapshot_id": "index-1", "index_snapshot_hash": "d" * 64,
        "tables": [table], "match_status": "RETRIEVAL_CANDIDATE",
        "notice": "仅固定目录候选",
    }
    discovery["content_hash"] = canonical_hash(discovery)
    bundle = {"bundle_id": "bundle-1", "release_id": "release-1",
        "release_hash": "c" * 64, "index_snapshot_id": "index-1",
        "index_snapshot_hash": "d" * 64, "selected_metric_ref": None}
    intent = {"question": "按订单创建时间统计订单数", "original_question": "按订单创建时间统计订单数",
        "metric_terms": ["订单数"], "dimension_terms": [], "dimension_ids": [],
        "start": "2025-01-01", "end_exclusive": "2026-01-01", "timezone": "SOURCE",
        "filters": [], "negative_terms": [], "analysis": {"kind": "NONE"}}
    plan = {"name": "订单数", "definition": "按订单编号去重计数",
        "catalog_snapshot_id": "catalog-1", "datasource_id": "3", "dataset_id": "dwd.orders",
        "operation": "COUNT_DISTINCT", "measure_field_id": "order_id", "time_field_id": "created_at",
        "dimension_field_ids": [], "filters": [], "unit_label": "个", "null_policy": "EXCLUDE_AND_WARN"}
    return discovery, bundle, intent, plan


class MetadataCandidateTests(unittest.TestCase):
    def test_no_ranked_semantic_hit_falls_back_to_permission_scoped_catalog_discovery(self):
        content={"workspace_id":31,"catalog_snapshot_id":"catalog-1","source_revision":"catalog-r1",
            "datasets":[{"dataset_id":"dwd.orders","datasource_id":"3","connector_kind":"POSTGRESQL",
                "namespace":"dwd","physical_name":"fct_orders","logical_locator":"dwd.orders",
                "schema_fingerprint":"b"*64,"fields":[{"field_id":"order_id","name":"order_id","data_type":"text",
                    "nullable":False,"semantic_role":"IDENTIFIER","unit":None,"classification":"INTERNAL",
                    "business_definition":{"name_zh":"订单编号","definition":"订单唯一标识","aliases":[],
                        "policy_ref":{"policy_id":None,"revision":None,"resolution":"UNKNOWN"}}}],
                "key_constraints":[],"policy_ref":{"policy_id":None,"revision":None,"resolution":"UNKNOWN"},
                "constraints_state":"UNKNOWN"}]}
        catalog_hash=sha(content)
        snapshot=SimpleNamespace(id="catalog-1",workspace_id=31,content=content,content_hash=catalog_hash)
        release=SimpleNamespace(id="release-1",workspace_id=31,scope="local_validation",
            content={"catalog_snapshots":[{"id":"catalog-1","content_hash":catalog_hash}]},content_hash="r"*64)
        index=SimpleNamespace(id="index-1",workspace_id=31,release_id="release-1",release_hash="r"*64,
            documents=[{"role":"dataset","target_ref":{"catalog_snapshot_id":"catalog-1","datasource_id":"3","dataset_id":"dwd.orders"}}],
            content_hash="i"*64)
        asset=SimpleNamespace(id=7,workspace_id=31,datasource_id=3,namespace="dwd",table_name="fct_orders",
            state="active",name="订单事实表",description="订单事实记录",fingerprint="b"*64)
        metadata=SimpleNamespace(spec={"name_zh":"订单事实表","definition":"按订单保存的事实记录"})
        datasource=SimpleNamespace(name="电商验证库")
        class FakeDB:
            calls=[]
            def get(self, model, key):
                if model is SemanticRelease:return release
                if model is SemanticIndex:return index
                if model is SemanticSnapshot:return snapshot
                if model is Datasource:return datasource
                return None
            def scalar(self, statement):
                self.calls.append(statement)
                return asset if len(self.calls)==1 else metadata
        bundle={"release_id":"release-1","release_hash":"r"*64,"index_snapshot_id":"index-1",
            "index_snapshot_hash":"i"*64,"selected_metric_ref":None,"candidates":[]}
        with patch("app.semantic.can_read_physical",return_value=True):
            result=metadata_candidates_for_bundle(FakeDB(),31,SimpleNamespace(role="admin",enabled=True),bundle)
        self.assertEqual(result["match_status"],"CATALOG_DISCOVERY_ONLY")
        self.assertEqual(result["tables"][0]["table_label"],"订单事实表")
        self.assertEqual(result["tables"][0]["fields"][0]["name_zh"],"订单编号")

    def test_compiler_uses_exact_snapshot_fields_and_generates_only_parameterized_select(self):
        discovery, bundle, intent, plan = context()
        candidate = normalize_metadata_candidate(plan, discovery, intent, bundle)
        frame, preview = compile_metadata_preview(candidate, intent, bundle, "run-1", "req-1", {"id": "confirm-1"})
        self.assertEqual(frame["execution_authority"], "MetadataCandidate")
        self.assertEqual(frame["result_shape"], ["metric_value"])
        self.assertIn('COUNT(DISTINCT "b"."order_id")', preview["sql"])
        self.assertIn('"dwd"."fct_orders"', preview["sql"])
        self.assertIn("%(start_date)s", preview["sql"])
        self.assertNotIn("SELECT *", preview["sql"].upper())
        self.assertEqual(preview["parameters"], {"start_date": "2025-01-01", "end_date": "2026-01-01"})
        self.assertFalse(candidate["business_approved"])

    def test_every_candidate_operation_distinguishes_no_rows_from_an_aggregate_zero(self):
        discovery, bundle, intent, base_plan = context()

        class DuckConnection:
            def __init__(self):
                self.db = duckdb.connect(":memory:")
                self.db.execute("CREATE SCHEMA dwd; CREATE TABLE dwd.fct_orders (order_id VARCHAR, created_at TIMESTAMP)")

            def execute(self, sql, parameters=None):
                text = re.sub(r"%\(([a-z_][a-z0-9_]*)\)s", r"$\1", sql)
                return self.db.execute(text, parameters or {})

        conn = DuckConnection()
        self.addCleanup(conn.db.close)
        for operation in ("COUNT_ROWS", "COUNT_DISTINCT"):
            with self.subTest(operation=operation):
                plan = dict(base_plan)
                if operation == "COUNT_ROWS":
                    plan.update(operation=operation, measure_field_id=None, definition="记录数")
                else:
                    plan.update(operation=operation, definition="按订单编号去重计数")
                candidate = normalize_metadata_candidate(plan, discovery, intent, bundle)
                frame, preview = compile_metadata_preview(candidate, intent, bundle, "run-1", "req-1", {"id": "confirm-1"})
                self.assertIsNotNone(frame["observation_sql"])
                observation = conn.execute(frame["observation_sql"], preview["parameters"]).fetchone()
                rows = conn.execute(preview["sql"], preview["parameters"]).fetchall()
                values, no_data, _ = apply_result_policies(
                    frame, {"source_rows": observation[0], "valid_measure_rows": observation[1],
                        "null_measure_rows": observation[0] - observation[1]}, rows)
                self.assertTrue(no_data)
                self.assertEqual(values, [])

                if operation == "COUNT_ROWS":
                    conn.execute("INSERT INTO dwd.fct_orders VALUES ('one','2025-03-01')")
                else:
                    conn.execute("INSERT INTO dwd.fct_orders VALUES ('one','2025-03-01')")
                observation = conn.execute(frame["observation_sql"], preview["parameters"]).fetchone()
                rows = conn.execute(preview["sql"], preview["parameters"]).fetchall()
                values, no_data, _ = apply_result_policies(
                    frame, {"source_rows": observation[0], "valid_measure_rows": observation[1],
                        "null_measure_rows": observation[0] - observation[1]}, rows)
                self.assertFalse(no_data)
                self.assertEqual(values[0][0], 1)
                conn.execute("DELETE FROM dwd.fct_orders")

    def test_field_from_another_dataset_is_rejected(self):
        discovery, bundle, intent, plan = context()
        plan["dataset_id"] = "other.orders"
        with self.assertRaises(PlanningBlocked) as exc:
            normalize_metadata_candidate(plan, discovery, intent, bundle)
        self.assertEqual(exc.exception.code, "METADATA_CANDIDATE_INVALID")

    def test_sum_without_authoritative_or_user_supplied_unit_is_rejected(self):
        discovery, bundle, intent, plan = context()
        plan.update(operation="SUM", measure_field_id="gross_amount", unit_label="CNY")
        with self.assertRaises(PlanningBlocked) as exc:
            normalize_metadata_candidate(plan, discovery, intent, bundle)
        self.assertIn("单位", exc.exception.message)

    def test_sum_with_explicit_user_unit_and_filter_is_parameterized(self):
        discovery, bundle, intent, plan = context()
        intent["filters"] = [{"field": "订单状态", "operator": "=", "value": "PAID"}]
        plan.update(operation="SUM", measure_field_id="gross_amount", unit_label="CNY",
            filters=[{"field_id": "status", "operator": "=", "value": "PAID"}])
        candidate = normalize_metadata_candidate(plan, discovery, intent, bundle,
            answers=[{"answer": "本次按CNY统计"}])
        _, preview = compile_metadata_preview(candidate, intent, bundle, "run-1", "req-1", {"id": "confirm-1"})
        self.assertIn('"b"."status" = %(filter_0)s', preview["sql"])
        self.assertIn('NOT "b"."gross_amount" IS NULL', preview["sql"])
        self.assertEqual(preview["parameters"]["filter_0"], "PAID")
        self.assertEqual(candidate["unit_source"], "USER_CLARIFICATION")


if __name__ == "__main__":
    unittest.main()
