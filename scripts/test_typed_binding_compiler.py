"""Authority regressions: maintained revisions must control SQL, never seed sidecars."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DATABASE_URL", "sqlite://")
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
from app.semantic_compiler import derive_contract
from app.agent_planner import compile_preview
from app.semantic import implementation_diagnostic


class TypedAuthority(unittest.TestCase):
    def setUp(self):
        self.entity = {"workspace_id": 3, "asset_id": "entity:Order", "asset_type": "entity", "revision": 2}
        self.metric_ref = {"workspace_id": 3, "asset_id": "metric:maintained_order_count", "asset_type": "metric", "revision": 2}
        self.dataset_ref = {"catalog_snapshot_id": "catalog:new", "datasource_id": "3", "dataset_id": "dwd.orders"}
        self.order = {**self.dataset_ref, "field_id": "order_id"}
        self.event = {**self.dataset_ref, "field_id": "purchased_at"}
        measure_prop = {"entity_ref": self.entity, "property_id": "order_id"}
        time_prop = {"entity_ref": self.entity, "property_id": "purchased_at"}
        self.metric = {**self.metric_ref, "name": "维护的订单量", "definition": "按订单编号去重", "spec": {
            "expression": {"op": "call", "function": "count_distinct", "args": [{"op": "property", "property_ref": measure_prop}]},
            "allowed_dimension_refs": [], "unit": {"kind": "COUNT", "code": "count", "scale": 1, "currency": None, "authority": "SYNTHETIC_ONLY"},
            "default_filter": None, "max_dimensions": 0, "forbidden_dimension_sets": [],
            "grain":{"description":"每个订单标识一条业务记录","property_refs":[measure_prop]},"distinct_property_refs":[measure_prop],
            "null_policy":"REJECT_ROW_AND_WARN","empty_result_policy":"NO_DATA",
            "time_policy": {"kind": "EVENT_RANGE", "property_ref": time_prop,
                "interval": "[start,end)", "history_policy": "CURRENT", "timezone": None}}}
        self.binding = {"asset_id": "binding:new_from_ui", "asset_type": "binding", "revision": 2, "workspace_id": 3, "spec": {
            "metric_ref": self.metric_ref, "base_dataset_ref": self.dataset_ref, "joins": [], "base_join_ids": [],
            "calculation": {"op": "call", "function": "count_distinct", "args": [{"op": "field", "field_ref": self.order}]},
            "property_mappings": [{"property_ref": measure_prop, "expression": {"op": "field", "field_ref": self.order}}, {"property_ref": time_prop, "expression": {"op": "field", "field_ref": self.event}}],
            "dimension_implementations": [], "time_field_ref": self.event, "base_filter": None,
            "applicability": {"scope_filter": None}, "parameter_bindings": []}}
        self.binding["spec"]["physical_grain"]={"description":"实际订单标识主键","key_field_refs":[self.order]}
        self.dataset = {"logical_locator": "dwd.orders", "namespace": "dwd", "physical_name": "dwd_orders", "connector_kind": "POSTGRESQL", "schema_fingerprint": "0" * 64}

    def derive(self):
        def asset(_db, _wid, target):
            if target == self.metric_ref:
                return self.metric
            return {"spec": {"properties": [{"property_id": "purchased_at", "name": "下单时间"}]}}
        def physical(_db, _wid, target):
            if "field_id" in target:
                return {"name": target["field_id"], "data_type": "timestamp" if target["field_id"] == "purchased_at" else "text", "business_definition": {"name_zh": "下单时间"}}
            return self.dataset
        with patch("app.semantic.resolve_asset", side_effect=asset), patch("app.semantic.resolve_physical", side_effect=physical):
            return derive_contract(None, 3, self.binding)

    def test_new_binding_has_no_sidecar_dependency(self):
        contract = self.derive()
        self.assertEqual(contract["authority"], "AssetRevision+CatalogSnapshot")
        self.assertEqual(contract["metric_ref"]["revision"], 2)
        self.assertEqual(contract["expression"], "COUNT(DISTINCT b.order_id)")
        self.assertEqual(contract["metric_null_policy"],"REJECT_ROW_AND_WARN")
        self.assertEqual(contract["empty_result_policy"],"NO_DATA")
        self.assertEqual(contract["measure_expression"],"b.order_id")
        self.assertEqual(contract["physical_grain_fields"],["order_id"])
        self.assertEqual(contract["physical_grain_expressions"],contract["metric_grain_expressions"])

    def test_maintained_catalog_controls_physical_sql(self):
        contract = self.derive()
        self.assertEqual(contract["physical_mapping"], {"dwd.orders": "dwd.dwd_orders"})
        before = contract["source_contract_hash"]
        self.dataset["physical_name"] = "revised_orders"
        revised = self.derive()
        self.assertEqual(revised["physical_mapping"]["dwd.orders"], "dwd.revised_orders")
        self.assertNotEqual(before, revised["source_contract_hash"])
        from test_agent_security import fixtures
        _, bundle, intent = fixtures()
        _, preview = compile_preview(revised, intent, bundle, "run", "req", {})
        self.assertIn('"dwd"."revised_orders"', preview["sql"])
        self.assertNotIn('dwd_fct_orders', preview["sql"])

    def test_calculation_cannot_disagree_with_logical_definition(self):
        self.binding["spec"]["calculation"]["function"] = "count"
        with self.assertRaises(HTTPException): self.derive()

    def test_time_mapping_cannot_be_inferred(self):
        self.binding["spec"]["property_mappings"].pop()
        with self.assertRaises(HTTPException): self.derive()

    def test_fixture_is_never_a_real_datasource(self):
        self.dataset["connector_kind"] = "LOCAL_FIXTURE"
        with self.assertRaises(HTTPException): self.derive()

    def test_unscoped_field_is_rejected(self):
        bad = {**self.order, "dataset_id": "dwd.unrelated"}
        self.binding["spec"]["property_mappings"][0]["expression"]["field_ref"] = bad
        self.binding["spec"]["calculation"]["args"][0]["field_ref"] = bad
        with self.assertRaises(HTTPException): self.derive()

    def test_arbitrary_identifier_cannot_enter_sql(self):
        self.dataset["physical_name"] = 'orders;DROP TABLE orders'
        with self.assertRaises(HTTPException): self.derive()

    def test_physical_grain_cannot_be_empty_duplicate_or_joined_dataset(self):
        for fields in ([],[self.order,self.order],[{**self.order,"dataset_id":"dwd.another"}]):
            with self.subTest(fields=fields):
                self.binding["spec"]["physical_grain"]["key_field_refs"]=fields
                with self.assertRaises(HTTPException):self.derive()

    def test_business_grain_mapping_cannot_be_missing(self):
        self.metric["spec"]["grain"]["property_refs"]=[{"entity_ref":self.entity,"property_id":"customer_id"}]
        with self.assertRaises(HTTPException):self.derive()

    def test_distinct_declaration_cannot_disagree_with_aggregate(self):
        self.metric["spec"]["distinct_property_refs"]=[]
        with self.assertRaises(HTTPException):self.derive()
        self.metric["spec"]["distinct_property_refs"]=[{"entity_ref":self.entity,"property_id":"order_id"}]
        self.metric["spec"]["expression"]["function"]="count"
        self.binding["spec"]["calculation"]["function"]="count"
        with self.assertRaises(HTTPException):self.derive()

    def test_freshness_sla_cannot_be_ignored_and_diagnostic_is_unknown(self):
        self.binding["spec"]["freshness_sla_minutes"]=30
        with self.assertRaises(HTTPException) as blocked:self.derive()
        with patch("app.semantic_compiler.derive_contract",side_effect=blocked.exception):
            diagnostic=implementation_diagnostic(None,3,self.binding)
        self.assertEqual(diagnostic["outcome"],"UNKNOWN")
        self.assertIsNone(diagnostic["compiled_contract"])
        self.assertIn("SLA",diagnostic["checks"][0]["reason"])


if __name__ == "__main__": unittest.main(verbosity=2)
