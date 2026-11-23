"""Four real model -> retrieval -> Gate -> authorization -> query acceptances.

Questions are natural language only. Gold metric identities and ODS reference SQL
stay on the client and never enter model or retrieval requests.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from decimal import Decimal,InvalidOperation
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from validate_v1_live import Api

REFERENCE = Path(os.environ.get('ONTODATAWORKS_TEST_DATA_ROOT', str(ROOT/'local/test-data'))) / 'ecommerce_validation.duckdb'
CASES = [
    {"id":"session_count", "question":"2025年按会话开始时间统计访问会话数，按会话编号去重，返回总计，使用源库时间。",
     "start":"2025-01-01", "end_exclusive":"2026-01-01",
     "reference_sql":"SELECT count(DISTINCT session_id) FROM ods.sessions WHERE started_at >= ?::TIMESTAMP AND started_at < ?::TIMESTAMP"},
    {"id":"shipment_count", "question":"按发货时间统计2025年2月1日至2025年12月31日的发货运单数，按运单编号去重，返回总计，使用源库时间。",
     "start":"2025-02-01", "end_exclusive":"2026-01-01",
     "reference_sql":"SELECT count(DISTINCT shipment_id) FROM ods.shipments WHERE shipped_at >= ?::TIMESTAMP AND shipped_at < ?::TIMESTAMP"},
    {"id":"promotion_orders", "question":"2025年按下单时间统计促销订单数，按订单编号去重，返回总计，使用源库时间。",
     "start":"2025-01-01", "end_exclusive":"2026-01-01",
     "reference_sql":"SELECT count(DISTINCT p.order_id) FROM ods.order_promotions p JOIN ods.orders o ON p.order_id=o.order_id WHERE o.purchased_at >= ?::TIMESTAMP AND o.purchased_at < ?::TIMESTAMP"},
    {"id":"review_score", "question":"按评价创建时间统计2025年2月1日至2025年12月31日的平均评论分，按评价记录计算平均值，使用源库时间。",
     "start":"2025-02-01", "end_exclusive":"2026-01-01",
     "reference_sql":"SELECT avg(review_score) FROM ods.reviews WHERE reviewed_at >= ?::TIMESTAMP AND reviewed_at < ?::TIMESTAMP"},
]


def equal_rows(observed, expected):
    if len(observed) != len(expected): return False
    for left, right in zip(observed, expected):
        if len(left) != len(right): return False
        for a,b in zip(left,right):
            if isinstance(b,(float,int,Decimal)):
                try:numeric=Decimal(str(a));reference=Decimal(str(b))
                except InvalidOperation:return False
                if not numeric.is_finite() or not reference.is_finite():return False
                if abs(numeric-reference)>max(Decimal("1e-12"),abs(reference)*Decimal("1e-12")):return False
            elif a != b:return False
    return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--metric",choices=[c["id"] for c in CASES])
    args=parser.parse_args()
    cases=[c for c in CASES if not args.metric or c["id"]==args.metric]
    suffix="-"+args.metric if args.metric else ""
    output=ROOT/("evidence/v1.0/metric-execution-live"+suffix+".json")
    output.parent.mkdir(parents=True,exist_ok=True)
    report={"scope":"Real local synthetic technical validation; not production business approval.",
        "gold_metric_ids_sent_to_model_or_retrieval":False,"reference_engine":"DuckDB read-only, independent raw ODS tables",
        "reference_path":str(REFERENCE),"started_at":datetime.now(timezone.utc).isoformat(),"cases":[]}
    def save():output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    api=Api();api.login();prefix="/workspaces/3/agent"
    directory=api.call(prefix+"/models")["data"]
    assert directory["source"]=="codex-app-server-model/list" and directory["models"]
    model=next((m for m in directory["models"] if m.get("isDefault")),directory["models"][0])
    selection={"catalog_version":directory["catalog_version"],"model":model.get("model",model["id"]),
        "reasoningEffort":model["defaultReasoningEffort"],"serviceTier":None}
    report["model_selection"]=selection;save()
    def get(rid):return api.call(prefix+"/runs/"+rid)["data"]["run"]
    def wait(run):
        deadline=time.monotonic()+180
        while run["state"] in {"DRAFT","EXECUTING","VALIDATING"}:
            if time.monotonic()>deadline:raise AssertionError("Run timeout: "+run["id"])
            time.sleep(.5);run=get(run["id"])
        return run
    import duckdb
    with duckdb.connect(str(REFERENCE),read_only=True) as reference:
        for case in cases:
            entry={"metric_id":case["id"],"question":case["question"],"passed":False,"stages":[],
                "expected_time":{"start":case["start"],"end_exclusive":case["end_exclusive"],"timezone":"SOURCE"},
                "reference_sql":case["reference_sql"],"reference_parameters":[case["start"],case["end_exclusive"]]}
            report["cases"].append(entry);save();run=None;started=time.monotonic()
            def record(stage,details=None):
                entry["stages"].append({"stage":stage,"passed":True,"details":details});save()
                print("PASS "+case["id"]+" "+stage,flush=True)
            def action(name,extra=None):
                nonlocal run
                req=run["requirements"][0]
                body={"expected_run_version":run["run_version"],"expected_requirement_version":req["requirement_version"],
                    "intent_frame_hash":req.get("intent_frame_hash"),"bundle_content_hash":req.get("bundle_content_hash"),
                    "metric_card_hash":req.get("metric_card_hash"),"query_frame_ref":req.get("query_frame_ref"),
                    "sql_hash":req.get("sql_hash"),"gate_result_ref":req.get("gate_result_ref"),
                    "authorization_id":req.get("authorization",{}).get("id"),"acknowledgement":True}
                body.update(extra or {})
                run=api.call(prefix+"/runs/"+run["id"]+"/requirements/"+req["id"]+"/"+name,"POST",body)["data"]["run"]
                entry["latest_run"]=run;save()
                return run["requirements"][0]
            try:
                expected=[list(row) for row in reference.execute(case["reference_sql"],entry["reference_parameters"]).fetchall()]
                entry["expected_rows"]=expected;save()
                body={"message":{"text":case["question"],"attachment_refs":[]},"model_selection":selection,
                    "execution_mode":"local_validation","interaction_mode":"CONVERSATION"}
                entry["create_request"]=body;save()
                run=wait(api.call(prefix+"/runs","POST",body,202)["data"]["run"])
                entry["run_id"]=run["id"];entry["latest_run"]=run;save()
                assert len(run["requirements"])==1,"One question unexpectedly decomposed into several requirements"
                req=run["requirements"][0]
                while req["state"]=="NEED_CLARIFICATION":
                    c=req["clarifications"][0]
                    assert c["slot"]=="timezone","Unexpected clarification; preserve model event semantics: "+str(c)
                    req=action("clarifications",{"clarification_id":c["id"],"answer":"SOURCE"})
                assert req["state"]=="TARGET_CONFIRMATION",req
                call=run.get("model_call",{})
                assert call.get("real_inference") is True and call.get("thread_id") and call.get("turn_id"),call
                intent=req["intent_frame"]
                assert intent.get("start")==case["start"] and intent.get("end_exclusive")==case["end_exclusive"],intent
                assert intent.get("timezone")=="SOURCE" and not intent.get("dimension_ids") and not intent.get("filters") and not intent.get("negative_terms"),intent
                assert intent.get("time_event"),"Explicit question event was lost"
                record("real_model_intent",{"model_call":call,"intent":intent})
                req=action("confirm-intent")
                bundle=req.get("bundle") or {}
                metric_candidates=[c for c in bundle["candidates"] if c["role"]=="metric"]
                metric_ids=[c["target_ref"]["asset_id"] for c in metric_candidates]
                gold="metric:"+case["id"]
                entry["search"]={"top1":metric_ids[:1],"top5":metric_ids[:5],"gold_rank":metric_ids.index(gold)+1 if gold in metric_ids else None,
                    "gold_sent_to_retrieve":False,"metric_candidates":metric_candidates}
                save()
                assert req["state"]=="NEED_CONFIRMATION",req
                assert bundle["selected_metric_ref"]["asset_id"]==gold,bundle
                assert bundle["selected_binding_ref"] and bundle["next_action"]=="NEED_CONFIRMATION",bundle
                record("confirmed_scoped_retrieval",{"bundle":bundle})
                req=action("confirm-metric")
                assert req["state"]=="PREVIEW_READY",req
                assert req["preview"]["parameters"]=={"start_date":case["start"],"end_date":case["end_exclusive"]},req["preview"]
                record("typed_query_preview",{"preview":req["preview"],"query_frame":req.get("query_frame")})
                req=action("gate");assert req["state"]=="GATE_PASSED",req
                record("live_readonly_source_gate",req["gate"])
                req=action("authorize");assert req["state"]=="AUTHORIZED",req
                record("explicit_execution_authorization",req["authorization"])
                req=action("execute");run=wait(run);entry["latest_run"]=run;req=run["requirements"][0];save()
                assert run["state"]=="DELIVERED" and req["state"]=="DELIVERED",run
                result=req["result"]
                assert result["scope"]=="local_validation" and result["validation"]["business_approved"] is False,result
                record("real_query_delivery",{"query_id":result["query_id"],"execution_operation_id":result["execution_operation_id"],"result":result})
                entry["observed_rows"]=result["rows"]
                assert equal_rows(result["rows"],expected),{"actual":result["rows"],"expected":expected}
                record("independent_ods_duckdb_comparison",{"actual_rows":result["rows"],"expected_rows":expected,"tolerance":1e-12})
                entry["passed"]=True
            except Exception as exc:
                entry["error"]=str(exc)
                if run:entry["latest_run"]=run
                print("FAIL "+case["id"]+" "+str(exc)[:400],flush=True)
            entry["elapsed_ms"]=round((time.monotonic()-started)*1000,3);save()
    report.update(completed_at=datetime.now(timezone.utc).isoformat(),passed=all(c["passed"] for c in report["cases"]),
        case_count=len(report["cases"]),passed_cases=sum(c["passed"] for c in report["cases"]),
        passed_stages=sum(len(c["stages"]) for c in report["cases"]))
    save();print(json.dumps({k:report[k] for k in ("passed","case_count","passed_cases","passed_stages")},ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
