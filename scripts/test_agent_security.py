"""Deterministic semantic-integrity regressions for the real Agent compiler.

No model calls or database writes. These tests enforce fail-closed behavior where
an unknown dimension/event must never silently become a different query.
"""
from __future__ import annotations
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = "sqlite://"
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
from app.agent_worker import normalize_intent
from app.agent_planner import compile_preview, PlanningBlocked, canonical_hash
from app.agent import _actions, aggregate
from app.warehouse import read_query, table_refs


def fixtures():
    contract = {"scope": "local_validation", "metric_id": "order_count", "temporal_mode": "event_range",
        "expression": "count(DISTINCT order_id)", "source_object": "dwd.fct_orders", "time_expression": "b.purchased_at",
        "definition": "按下单时间统计去重订单", "physical_mapping": {"dwd.fct_orders": "dwd.dwd_fct_orders", "dim.customers": "dim.dim_customers"},
        "dimensions": {"customer_state": {"expression": "c.state", "joins": ["JOIN dim.customers c ON b.customer_id=c.customer_id"]}},
        "base_joins": [], "datasource_id": 1, "unit": "count"}
    bundle = {"bundle_id": "bundle_test", "selected_metric_ref": {"workspace_id": 3, "asset_id": "metric:order_count", "asset_type": "metric", "revision": 1},
        "selected_binding_ref": {"workspace_id": 3, "asset_id": "binding:order_count", "asset_type": "binding", "revision": 1},
        "requested_dimension_refs": [], "release_id": "release_test", "release_hash": "0" * 64,
        "index_snapshot_id": "index_test", "index_snapshot_hash": "1" * 64, "closure_refs": []}
    intent = {"question": "2025年按源库时间统计订单量", "metric_terms": ["订单量"], "dimension_terms": [],
        "start": "2025-01-01", "end_exclusive": "2026-01-01", "timezone": "SOURCE", "time_event": "下单时间",
        "dimension_ids": [], "filters": [], "negative_terms": []}
    return contract, bundle, intent


class SemanticIntegrity(unittest.TestCase):
    def setUp(self):
        self.contract, self.bundle, self.intent = fixtures()

    def compile(self, **changes):
        return compile_preview(self.contract, {**self.intent, **changes}, self.bundle, "run_test", "req_test", {"id": "confirmed_test"})

    def blocked(self, **changes):
        with self.assertRaises(PlanningBlocked):
            self.compile(**changes)

    def test_sqlglot_placeholders_and_physical_refs(self):
        frame, preview = self.compile()
        self.assertIn("%(start_date)s", preview["sql"])
        self.assertIn("%(end_date)s", preview["sql"])
        self.assertNotIn("2025-01-01", preview["sql"])
        self.assertEqual(table_refs(read_query(preview["sql_ast_template"])), [("dwd", "dwd_fct_orders")])
        import re
        self.assertEqual(re.sub(r":([a-z_][a-z0-9_]*)", r"%(\1)s", preview["sql_ast_template"]), preview["sql"])
        self.assertEqual(preview["parameters"], {"start_date": "2025-01-01", "end_date": "2026-01-01"})
        self.assertEqual(frame["content_hash"], canonical_hash({k: v for k, v in frame.items() if k != "content_hash"}))

    def test_time_event_cannot_silently_change(self):
        self.blocked(time_event="支付时间")

    def test_specific_product_sales_not_forced_into_generic_sales_clarification(self):
        question="2025年商品销售额是多少，使用源库时间"
        item={**self.intent,"question":question,"metric_terms":["商品销售额"],"time_event":None}
        _,clarifications,error=normalize_intent(item,question)
        self.assertIsNone(error)
        self.assertFalse(any(c["slot"]=="metric" for c in clarifications),"a specific official metric name was replaced by generic sales ambiguity")

    def test_new_binding_event_overrides_legacy_metric_vocabulary(self):
        self.contract.update(authority="AssetRevision+CatalogSnapshot", time_event_aliases=["paid_at", "支付时间"], time_expression="b.paid_at")
        self.blocked(time_event="下单时间")

    def test_unknown_dimension_is_not_dropped(self):
        item = {**self.intent, "question": "2025年按订单状态统计订单量，按源库时间", "dimension_terms": ["订单状态"]}
        intent, clarifications, error = normalize_intent(item, item["question"])
        self.assertTrue(error or clarifications, "unsupported requested dimension was silently dropped")

    def test_negative_geography_does_not_add_grouping(self):
        item = {**self.intent, "question": "2025年统计订单量，不要按地区分组，按源库时间", "dimension_terms": []}
        intent, clarifications, error = normalize_intent(item, item["question"])
        self.assertNotIn("customer_state", intent["dimension_ids"])

    def test_buyer_status_never_geography(self):
        item = {**self.intent, "question": "2025年按买家状态统计订单量，按源库时间", "dimension_terms": ["买家状态"]}
        intent, clarifications, error = normalize_intent(item, item["question"])
        self.assertTrue(error or clarifications)
        self.assertNotIn("customer_state", intent["dimension_ids"])

    def test_confirmed_source_timezone_is_not_business_negation(self):
        item = {**self.intent, "question": "2025年统计订单量，不做时区转换", "negative_terms": ["不做时区转换"]}
        intent, clarifications, error = normalize_intent(item, item["question"])
        self.assertFalse(error)
        self.assertEqual(intent["timezone"], "SOURCE")
        compile_preview(self.contract, intent, self.bundle, "run_test", "req_test", {})

    def test_source_time_needs_no_timezone_setting(self):
        _, preview = self.compile(timezone=None)
        self.assertNotIn("AT TIME ZONE", preview["sql"])

    def test_unknown_business_negation_blocks(self):
        self.blocked(negative_terms=["不包括退款订单"])

    def test_model_cannot_drop_status_filter(self):
        question = "2025年只统计已支付订单的订单量，使用源库时间"
        _, _, error = normalize_intent({**self.intent, "question": question, "filters": []}, question)
        self.assertIsNotNone(error)

    def test_explicit_payment_event_survives_model_omission(self):
        question = "2025年按支付时间统计订单量，使用源库时间"
        intent, _, _ = normalize_intent({**self.intent, "question": question, "time_event": None}, question)
        with self.assertRaises(PlanningBlocked):
            compile_preview(self.contract, intent, self.bundle, "run_test", "req_test", {})

    def test_reverse_or_missing_date_blocks(self):
        self.blocked(start="2026-01-01", end_exclusive="2025-01-01")
        self.blocked(start=None)

    def test_precise_timestamp_not_silently_truncated(self):
        self.blocked(start="2025-01-01T12:30:00", end_exclusive="2025-01-02T12:30:00")

    def test_unapproved_money_and_production_block(self):
        self.contract["metric_id"] = "gross_order_value"
        self.blocked()
        self.contract["metric_id"] = "order_count"
        self.contract["scope"] = "production"
        self.blocked()

    def test_unknown_or_multiple_dimension_blocks(self):
        self.blocked(dimension_ids=["buyer_status"])
        self.blocked(dimension_ids=["customer_state", "product_category"])

    def test_filter_value_cannot_become_sql(self):
        self.blocked(dimension_ids=["customer_state"], filters=[{"field": "region", "operator": "=", "value": "SP' OR true--"}])
        frame, preview = self.compile(dimension_ids=["customer_state"], filters=[{"field": "region", "operator": "=", "value": "SP"}])
        self.assertNotIn("'SP'", preview["sql"])
        self.assertEqual(preview["parameters"]["filter_0"], "SP")
        self.assertIn("%(filter_0)s", preview["sql"])

    def test_unknown_execution_cannot_be_reauthorized(self):
        class User:
            role = "admin"
        self.assertEqual(_actions({"state": "EXECUTION_UNKNOWN"}, User()), [])

    def test_partial_result_is_not_whole_run_success(self):
        state = aggregate({"requirements": [{"state": "DELIVERED"}, {"state": "BLOCKED_EVIDENCE"}]})
        self.assertEqual(state, "BLOCKED_EVIDENCE")


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({"name": test.id(), "passed": True})
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({"name": test.id(), "passed": False, "detail": self._exc_info_to_string(err, test)})
    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({"name": test.id(), "passed": False, "detail": self._exc_info_to_string(err, test)})


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SemanticIntegrity)
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    out = ROOT / "evidence/v1.0/agent-semantic-integrity-after.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"completed_at": datetime.now(timezone.utc).isoformat(), "scope": "Pure semantic normalization/compiler/state regressions; no database or inference", "passed": result.wasSuccessful(), "tests": result.records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
