"""Isolated semantic compiler regressions: no model, service restart or DB writes."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"backend"))
os.environ.setdefault("DATABASE_URL","sqlite://")
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"]=Fernet.generate_key().decode()
from fastapi import HTTPException
from app.semantic_compiler import derive_contract
from app.agent_planner import compile_preview,PlanningBlocked


def fixture():
    def asset(aid,typ):return {"workspace_id":3,"asset_id":aid,"asset_type":typ,"revision":2}
    def ds(did):return {"catalog_snapshot_id":"snapshot_test","datasource_id":"1","dataset_id":did}
    def col(did,name):return {**ds(did),"field_id":name}
    def prop(name):return {"entity_ref":asset("entity:order","entity"),"property_id":name}
    p_id,p_time=prop("order_id"),prop("ordered_at")
    p_order_customer=prop("customer_id")
    p_customer={"entity_ref":asset("entity:customer","entity"),"property_id":"customer_id"}
    p_region={"entity_ref":asset("entity:customer","entity"),"property_id":"state"}
    dim=asset("dimension:region","dimension")
    metric={**asset("metric:orders_v2","metric"),"name":"按下单时间统计订单数","definition":"测试明确类型映射",
        "spec":{"expression":{"op":"call","function":"count_distinct","args":[{"op":"property","property_ref":p_id}]},
            "allowed_dimension_refs":[dim],"time_policy":{"kind":"EVENT_RANGE","property_ref":p_time,"timezone":None,"interval":"[start,end)","history_policy":"CURRENT"},
            "unit":{"kind":"COUNT","code":"order","scale":1,"authority":"SYNTHETIC_ONLY"},"default_filter":None,
            "max_dimensions":1,"forbidden_dimension_sets":[],"grain":{"description":"订单标识业务粒度","property_refs":[p_id]},
            "distinct_property_refs":[p_id],"null_policy":"REJECT_ROW_AND_WARN","empty_result_policy":"NO_DATA"}}
    binding={**asset("binding:orders_v2","binding"),"spec":{
        "metric_ref":asset("metric:orders_v2","metric"),"base_dataset_ref":ds("orders"),
        "calculation":{"op":"call","function":"count_distinct","args":[{"op":"field","field_ref":col("orders","order_id")}]},
        "property_mappings":[{"property_ref":p_id,"expression":{"op":"field","field_ref":col("orders","order_id")}},
            {"property_ref":p_time,"expression":{"op":"field","field_ref":col("orders","purchased_at")}},
            {"property_ref":p_order_customer,"expression":{"op":"field","field_ref":col("orders","customer_id")}},
            {"property_ref":p_customer,"expression":{"op":"field","field_ref":col("customers","customer_id")}},
            {"property_ref":p_region,"expression":{"op":"field","field_ref":col("customers","state")}}],
        "physical_grain":{"description":"基础订单记录主键粒度","key_field_refs":[col("orders","order_id")]},
        "joins":[{"join_id":"customers","from_dataset_ref":ds("orders"),"to_dataset_ref":ds("customers"),
            "logical_relation_ref":asset("relation:order_customer","relation"),
            "expected_cardinality":"N:1","fanout_strategy":"REJECT","temporal_condition":None,
            "key_pairs":[{"from":col("orders","customer_id"),"to":col("customers","customer_id")}],"join_type":"LEFT","null_key_policy":"KEEP"}],
        "base_join_ids":[],"dimension_implementations":[{"dimension_ref":dim,"expression":{"op":"field","field_ref":col("customers","state")},"required_join_ids":["customers"]}],
        "time_field_ref":col("orders","purchased_at"),"base_filter":None,"applicability":{"scope_filter":None},"parameter_bindings":[]}}
    dimension={"asset_id":dim["asset_id"],"name":"地区","spec":{"entity_ref":p_region["entity_ref"],"property_id":"state","history_policy":"CURRENT","null_member":"ERROR"}}
    relation={"asset_id":"relation:order_customer","spec":{"from_entity_ref":copy.deepcopy(p_order_customer["entity_ref"]),"to_entity_ref":copy.deepcopy(p_customer["entity_ref"]),"logical_key_pairs":[{"from":copy.deepcopy(p_order_customer),"to":copy.deepcopy(p_customer)}],"cardinality":"N:1"}}
    def resolve_asset(_db,_wid,ref):
        if ref["asset_type"]=="metric":return metric
        if ref["asset_type"]=="entity":return {"spec":{"properties":[{"property_id":"ordered_at","name":"下单时间"}]}}
        if ref["asset_type"]=="dimension":return dimension
        if ref["asset_type"]=="relation":return relation
        raise AssertionError(ref)
    def resolve_physical(_db,_wid,ref):
        if "field_id" in ref:return {"name":ref["field_id"],"data_type":"timestamp without time zone" if ref["field_id"]=="purchased_at" else "text","business_definition":{"name_zh":"下单时间" if ref["field_id"]=="purchased_at" else ref["field_id"]}}
        return {"connector_kind":"POSTGRESQL","schema_fingerprint":"0"*64,"logical_locator":"dwd.orders" if ref["dataset_id"]=="orders" else "dim.customers",
            "namespace":"dwd" if ref["dataset_id"]=="orders" else "dim","physical_name":ref["dataset_id"]}
    bundle={"bundle_id":"bundle_test","selected_metric_ref":asset("metric:orders_v2","metric"),"selected_binding_ref":asset("binding:orders_v2","binding"),
        "requested_dimension_refs":[dim],"release_id":"release_test","release_hash":"0"*64,"index_snapshot_id":"index_test","index_snapshot_hash":"1"*64,"closure_refs":[]}
    intent={"start":"2025-01-01","end_exclusive":"2026-01-01","timezone":"SOURCE","time_event":"下单时间","dimension_ids":["region"],"filters":[],"negative_terms":[]}
    return metric,binding,resolve_asset,resolve_physical,bundle,intent


class TypedCompilerIntegrity(unittest.TestCase):
    def setUp(self):
        self.metric,self.binding,resolver,physical,self.bundle,self.intent=fixture()
        self.resolver=resolver
        self.physical=physical
        self.patches=[patch("app.semantic.resolve_asset",side_effect=resolver),patch("app.semantic.resolve_physical",side_effect=physical)]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def compile(self):
        contract=derive_contract(None,3,self.binding)
        return compile_preview(contract,self.intent,self.bundle,"run_test","req_test",{})

    def assert_rejected(self):
        with self.assertRaises((HTTPException,PlanningBlocked)):self.compile()

    def test_exact_day_policy_not_silently_range(self):
        self.metric["spec"]["time_policy"]["interval"]="EXACT_DAY"
        self.assert_rejected()

    def test_scd2_policy_not_silently_current(self):
        self.metric["spec"]["time_policy"]["history_policy"]="SCD2"
        self.assert_rejected()

    def test_legacy_timezone_does_not_require_configuration_or_convert_source_time(self):
        self.metric["spec"]["time_policy"].pop("timezone", None)
        _, preview = self.compile()
        self.assertNotIn("AT TIME ZONE", preview["sql"])
        self.metric["spec"]["time_policy"]["timezone"]="Asia/Shanghai"
        _, preview = self.compile()
        self.assertNotIn("AT TIME ZONE", preview["sql"])

    def test_derived_metric_dependency_filter_not_dropped(self):
        dependency=copy.deepcopy(self.metric)
        dependency["asset_id"]="metric:filtered_orders"
        dependency["spec"]["default_filter"]={"op":"binary","operator":"=",
            "left":{"op":"property","property_ref":{"entity_ref":{"workspace_id":3,"asset_id":"entity:order","asset_type":"entity","revision":2},"property_id":"status"}},
            "right":{"op":"literal","value":"delivered"}}
        self.metric["spec"]["expression"]={"op":"metric","metric_ref":{"workspace_id":3,"asset_id":"metric:filtered_orders","asset_type":"metric","revision":2}}
        def resolve(db,wid,ref):
            return dependency if ref.get("asset_id")=="metric:filtered_orders" else self.resolver(db,wid,ref)
        with patch("app.semantic.resolve_asset",side_effect=resolve):self.assert_rejected()

    def test_left_drop_null_keys_applied_or_rejected(self):
        self.binding["spec"]["joins"][0]["null_key_policy"]="DROP"
        try:frame,preview=self.compile()
        except (HTTPException,PlanningBlocked):return
        import duckdb
        with duckdb.connect(":memory:") as con:
            con.execute("CREATE SCHEMA dwd; CREATE SCHEMA dim")
            con.execute("CREATE TABLE dwd.orders(order_id VARCHAR, customer_id VARCHAR, purchased_at TIMESTAMP)")
            con.execute("CREATE TABLE dim.customers(customer_id VARCHAR,state VARCHAR)")
            con.execute("INSERT INTO dwd.orders VALUES ('A','C1','2025-06-01'),('B',NULL,'2025-06-01')")
            con.execute("INSERT INTO dim.customers VALUES ('C1','SP')")
            import re
            sql=re.sub(r"%\(([a-z_][a-z0-9_]*)\)s",lambda m:"CAST('"+preview["parameters"][m[1]]+"' AS TIMESTAMP)",preview["sql"])
            observed=con.execute(sql).fetchall()
        self.assertEqual(observed,[("SP",1)],"LEFT+DROP kept a null join key and returned an extra semantic group")

    def test_maintained_dimension_property_cannot_keep_old_physical_grouping(self):
        dimension=self.resolver(None,3,self.metric["spec"]["allowed_dimension_refs"][0])
        dimension["spec"]["property_id"]="city"
        self.assert_rejected()
        # An explicit matching new property mapping and implementation makes
        # the maintained business attribute control actual generated SQL.
        expression=copy.deepcopy(self.binding["spec"]["dimension_implementations"][0]["expression"])
        expression["field_ref"]["field_id"]="city"
        self.binding["spec"]["dimension_implementations"][0]["expression"]=expression
        self.binding["spec"]["property_mappings"].append({"property_ref":{"entity_ref":dimension["spec"]["entity_ref"],"property_id":"city"},"expression":expression})
        _,preview=self.compile();self.assertIn("j1.city",preview["sql"]);self.assertNotIn("j1.state",preview["sql"])

    def test_physical_join_cannot_disagree_with_logical_key_mapping(self):
        self.binding["spec"]["joins"][0]["key_pairs"][0]["to"]["field_id"]="state"
        self.assert_rejected()

    def test_maintained_relation_property_cannot_keep_old_physical_join(self):
        relation=self.resolver(None,3,self.binding["spec"]["joins"][0]["logical_relation_ref"])
        relation["spec"]["logical_key_pairs"][0]["to"]["property_id"]="external_customer_id"
        self.assert_rejected()

    def test_relation_direction_and_cardinality_are_checked_together(self):
        relation=self.resolver(None,3,self.binding["spec"]["joins"][0]["logical_relation_ref"])
        pair=relation["spec"]["logical_key_pairs"][0]
        relation["spec"]["logical_key_pairs"]=[{"from":pair["to"],"to":pair["from"]}]
        relation["spec"]["cardinality"]="1:N"
        self.compile()
        relation["spec"]["cardinality"]="N:1"
        self.assert_rejected()

    def two_hop_context(self):
        first=self.binding["spec"]["joins"][0]
        second=copy.deepcopy(first);second["join_id"]="regions"
        second["from_dataset_ref"]=copy.deepcopy(first["to_dataset_ref"])
        second["to_dataset_ref"]={**first["to_dataset_ref"],"dataset_id":"regions"}
        second["logical_relation_ref"]={**first["logical_relation_ref"],"asset_id":"relation:customer_region"}
        second["key_pairs"]=[{"from":{**second["from_dataset_ref"],"field_id":"state"},"to":{**second["to_dataset_ref"],"field_id":"region_code"}}]
        p_from={"entity_ref":copy.deepcopy(self.resolver(None,3,self.metric["spec"]["allowed_dimension_refs"][0])["spec"]["entity_ref"]),"property_id":"state"}
        p_to={"entity_ref":{**p_from["entity_ref"],"asset_id":"entity:region"},"property_id":"region_code"}
        self.binding["spec"]["property_mappings"].append({"property_ref":p_to,"expression":{"op":"field","field_ref":second["key_pairs"][0]["to"]}})
        relation={"asset_id":"relation:customer_region","spec":{"logical_key_pairs":[{"from":p_from,"to":p_to}],"cardinality":"N:1"}}
        # Deliberately reverse declaration order. Only dependency order can be
        # used by the planner; JSON list order does not bind a table alias.
        self.binding["spec"]["joins"]=[second,first]
        resolver=self.resolver
        def assets(db,wid,target):return relation if target.get("asset_id")=="relation:customer_region" else resolver(db,wid,target)
        original_physical=self.physical
        def physical(db,wid,target):
            if target.get("dataset_id")=="regions" and "field_id" not in target:
                return {"connector_kind":"POSTGRESQL","schema_fingerprint":"0"*64,"logical_locator":"dim.regions","namespace":"dim","physical_name":"regions"}
            return original_physical(db,wid,target)
        return assets,physical

    def test_join_scope_cannot_omit_its_parent(self):
        assets,physical=self.two_hop_context()
        self.binding["spec"]["base_join_ids"]=["regions"]
        with patch("app.semantic.resolve_asset",side_effect=assets),patch("app.semantic.resolve_physical",side_effect=physical):self.assert_rejected()

    def test_join_declaration_order_cannot_reorder_required_aliases(self):
        assets,physical=self.two_hop_context()
        self.binding["spec"]["base_join_ids"]=["regions","customers"]
        with patch("app.semantic.resolve_asset",side_effect=assets),patch("app.semantic.resolve_physical",side_effect=physical):
            contract=derive_contract(None,3,self.binding)
            self.assertEqual(contract["ordered_join_ids"],["customers","regions"])
            _,preview=compile_preview(contract,self.intent,self.bundle,"run","req",{})
            self.assertLess(preview["sql"].index('"dim"."customers"'),preview["sql"].index('"dim"."regions"'))


if __name__=="__main__":
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(TypedCompilerIntegrity)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    out=ROOT/"evidence/v1.0/typed-compiler-integrity.json"
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({"scope":"Isolated typed compiler regression; in-memory DuckDB only, no service/model/source mutations",
        "test_count":result.testsRun,"passed":result.wasSuccessful(),"failures":[{"name":str(t),"detail":d} for t,d in result.failures],
        "errors":[{"name":str(t),"detail":d} for t,d in result.errors]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
