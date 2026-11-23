import os
"""Real HTTP/model/retrieval/execution acceptance against independent DuckDB.
Credentials stay in ignored local files; reports contain no cookies or tokens.
"""
from pathlib import Path
import http.cookiejar
import json
import math
import time
import urllib.error
import urllib.request
import uuid
import argparse

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "evidence" / "v1.0" / "live-acceptance.json"


class Api:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), urllib.request.ProxyHandler({}))
        self.csrf = None

    def call(self, path, method="GET", body=None, expected=200, key=None, headers=None):
        h = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:19090"}
        if self.csrf: h["X-CSRF-Token"] = self.csrf
        if method != "GET": h["Idempotency-Key"] = key or uuid.uuid4().hex
        h.update(headers or {})
        raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        try:
            response = self.opener.open(urllib.request.Request("http://127.0.0.1:19090/api"+path, data=raw, headers=h, method=method), timeout=45)
        except urllib.error.HTTPError as exc: response = exc
        data = json.load(response)
        if response.code != expected:
            raise AssertionError(f"{method} {path}: HTTP {response.code} expected {expected}: {data}")
        return data

    def login(self):
        values = json.loads((ROOT / "local" / "credentials.json").read_text("utf-8"))
        self.csrf = self.call("/auth/login", "POST", {"username": values["admin_username"], "password": values["admin_password"]})["csrf"]


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--smoke", action="store_true"); args = parser.parse_args()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    api = Api(); api.login(); base = "/workspaces/3"; prefix = base + "/agent"
    report = {"scope": "real_local_synthetic_data_validation_not_production_approval", "tests": [], "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": []}
    def save(): REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    def check(name, details):
        report["tests"].append({"name": name, "passed": True, "details": details}); save(); print("PASS", name, flush=True)
    def get(rid): return api.call(prefix+"/runs/"+rid)["data"]["run"]
    def wait(run):
        deadline=time.monotonic()+170
        while run["state"] in {"DRAFT", "EXECUTING", "VALIDATING"}:
            if time.monotonic()>deadline: raise AssertionError("Run did not reach a visible state: "+run["id"])
            time.sleep(.5); run=get(run["id"])
        return run
    directory=api.call(prefix+"/models")["data"]
    assert directory["source"]=="codex-app-server-model/list" and directory["models"]
    model=next((m for m in directory["models"] if m.get("isDefault")),directory["models"][0])
    selection={"catalog_version":directory["catalog_version"],"model":model.get("model",model["id"]),"reasoningEffort":model["defaultReasoningEffort"],"serviceTier":None}
    check("live_model_directory",{"count":len(directory["models"]),"model":selection["model"],"source":directory["source"]})
    overview=api.call(base+"/semantic/overview")["data"]
    check("live_semantic_inventory",overview)
    def create(question, key=None):
        body={"message":{"text":question,"attachment_refs":[]},"model_selection":selection,"execution_mode":"local_validation","interaction_mode":"CONVERSATION"}
        run=api.call(prefix+"/runs","POST",body,202,key)["data"]["run"]
        report["runs"].append({"run_id":run["id"],"question":question}); save()
        return wait(run),body
    def action(run, name, extra=None, expected=200, key=None):
        req=run["requirements"][0]
        body={"expected_run_version":run["run_version"],"expected_requirement_version":req["requirement_version"],
            "intent_frame_hash":req.get("intent_frame_hash"),"bundle_content_hash":req.get("bundle_content_hash"),
            "metric_card_hash":req.get("metric_card_hash"),"query_frame_ref":req.get("query_frame_ref"),
            "sql_hash":req.get("sql_hash"),"gate_result_ref":req.get("gate_result_ref"),
            "authorization_id":req.get("authorization",{}).get("id"),"acknowledgement":True}
        body.update(extra or {})
        result=api.call(prefix+"/runs/"+run["id"]+"/requirements/"+req["id"]+"/"+name,"POST",body,expected,key)
        return (result["data"]["run"] if expected==200 else result),body
    def prepare(run):
        req=run["requirements"][0]
        while req["state"]=="NEED_CLARIFICATION":
            c=req["clarifications"][0]
            answer={"timezone":"SOURCE","time_event":"订单创建时间"}.get(c["slot"])
            if answer is None: raise AssertionError("Unexpected clarification: "+str(c))
            run,_=action(run,"clarifications",{"clarification_id":c["id"],"answer":answer});req=run["requirements"][0]
        assert req["state"]=="TARGET_CONFIRMATION",req
        run,_=action(run,"confirm-intent");req=run["requirements"][0]
        assert req["state"]=="NEED_CONFIRMATION",req
        assert req["bundle"]["selected_metric_ref"]["asset_id"].endswith("order_count"),req["bundle"]
        run,_=action(run,"confirm-metric");assert run["requirements"][0]["state"]=="PREVIEW_READY",run
        return run
    try:
        question="2025年按下单时间统计订单量，按买家所在地区（省/州）分组、订单编号去重，使用源库时间。"
        key=uuid.uuid4().hex;run,body=create(question,key)
        replay=api.call(prefix+"/runs","POST",body,202,key)["data"]["run"]
        assert replay["id"]==run["id"]
        check("run_creation_idempotent",{"run_id":run["id"]})
        run=prepare(run)
        check("real_intent_and_scoped_retrieval",{"run_id":run["id"],"model_call":run.get("model_call"),"intent":run["requirements"][0]["intent_frame"],"bundle":run["requirements"][0]["bundle"]})
        req=run["requirements"][0]
        _bad,_=action(run,"execute",expected=403)
        check("execute_without_gate_or_authorization_rejected",{"run_id":run["id"]})
        stale,_=action(run,"gate",{"expected_run_version":0},expected=409)
        check("stale_cas_rejected",{"code":stale.get("detail")})
        run,_=action(run,"gate");assert run["requirements"][0]["state"]=="GATE_PASSED",run
        check("live_gate",run["requirements"][0]["gate"])
        run,_=action(run,"authorize");assert run["requirements"][0]["state"]=="AUTHORIZED",run
        altered,_=action(run,"execute",{"sql_hash":"0"*64},expected=409)
        check("changed_sql_authorization_rejected",{"error":altered.get("detail")})
        execute_key=uuid.uuid4().hex
        run,execute_body=action(run,"execute",key=execute_key)
        completed=wait(run);assert completed["state"]=="DELIVERED",completed
        result=completed["requirements"][0]["result"]
        import duckdb
        with duckdb.connect(str(Path(os.environ.get('ONTODATAWORKS_TEST_DATA_ROOT', str(ROOT/'local/test-data'))) / 'ecommerce_validation.duckdb'),read_only=True) as reference:
            expected=[list(r) for r in reference.execute("SELECT c.state, count(DISTINCT b.order_id) FROM dwd.fct_orders b JOIN dim.customers c ON b.customer_id=c.customer_id WHERE b.purchased_at >= TIMESTAMP '2025-01-01' AND b.purchased_at < TIMESTAMP '2026-01-01' GROUP BY c.state ORDER BY c.state NULLS LAST").fetchall()]
        assert result["rows"]==expected,(result["rows"],expected)
        assert result["scope"]=="local_validation" and result["validation"]["business_approved"] is False
        check("real_query_matches_independent_duckdb",{"run_id":completed["id"],"query_id":result["query_id"],"rows":result["rows"],"expected":expected,"sql":completed["requirements"][0]["preview"]["sql"],"parameters":completed["requirements"][0]["preview"]["parameters"]})
        repeated=api.call(prefix+"/runs/"+run["id"]+"/requirements/"+req["id"]+"/execute","POST",execute_body,200,execute_key)
        assert repeated["data"]["run"]["requirements"][0]["execution_operation_id"]==result["execution_operation_id"]
        check("execution_request_replay_same_operation",{"execution_operation_id":result["execution_operation_id"]})
        if not args.smoke:
            negatives=[
                ("buyer_status_not_geography","2025年按买家状态统计订单量，使用源库时间",{"BLOCKED_EVIDENCE","NEED_CLARIFICATION"}),
                ("order_status_not_dropped","2025年按订单状态统计订单量，使用源库时间",{"BLOCKED_EVIDENCE","NEED_CLARIFICATION"}),
                ("ambiguous_sales_requires_clarification","2025年销售额是多少，使用源库时间",{"NEED_CLARIFICATION"}),
            ]
            for name,q,states in negatives:
                negative,_=create(q);assert negative["requirements"][0]["state"] in states,negative
                assert not negative["requirements"][0].get("preview"),negative
                check(name,{"run_id":negative["id"],"requirements":negative["requirements"]})
            for name,q in [("wrong_time_event_blocked","2025年按支付时间统计订单量，使用源库时间"),("currency_unknown_blocked","2025年支付总额是多少，使用源库时间"),("future_coverage_blocked","2027年订单量是多少，使用源库时间")]:
                negative,_=create(q)
                if negative["requirements"][0]["state"]=="TARGET_CONFIRMATION": negative,_=action(negative,"confirm-intent")
                if negative["requirements"][0]["state"]=="NEED_CONFIRMATION": negative,_=action(negative,"confirm-metric")
                if negative["requirements"][0]["state"]=="PREVIEW_READY": negative,_=action(negative,"gate")
                assert negative["requirements"][0]["state"].startswith("BLOCKED") or negative["requirements"][0]["state"]=="NEED_CLARIFICATION",negative
                check(name,{"run_id":negative["id"],"state":negative["state"],"error":negative["requirements"][0].get("error")})
        production={**body,"execution_mode":"production"}
        api.call(prefix+"/runs","POST",production,409)
        check("production_without_business_approval_rejected",{"approved_paths":0})
        report["passed"]=True
    except Exception as exc:
        report["passed"]=False;report["error"]=str(exc);save();print("FAIL",str(exc),flush=True);raise
    finally: save()
    print(json.dumps({"passed":report["passed"],"tests":len(report["tests"]),"report":str(REPORT)},ensure_ascii=False))


if __name__=="__main__":main()
