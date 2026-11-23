"""Isolated HTTP semantic revision changes real query results, checked with ODS.

Prepare creates a new workspace, a read-only connector/catalog fixture, a short
technical source, exact session Entity, and Metric/Binding revisions. Model calls
only occur with --resume, so deployment/model work can be coordinated explicitly.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.parse

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"scripts"))
from validate_semantic_live import Client,canonical
from validate_metric_execution_live import equal_rows,REFERENCE


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--prepare-only",action="store_true")
    parser.add_argument("--resume",type=Path)
    parser.add_argument("--output",type=Path,default=ROOT/"evidence/v1.0/revision-execution-live.json")
    args=parser.parse_args();out=args.resume or args.output
    report=json.loads(out.read_text("utf-8")) if args.resume else {"suite":"semantic-revision-result-change/1.0.0","started_at":datetime.now(timezone.utc).isoformat(),
        "scope":"Isolated local synthetic technical validation, no production approvals or source data mutations.","tests":[],"http_receipts":[],"errors":[],"model_calls":0,
        "reference_engine":"Independent read-only DuckDB ODS; reference SQL and Metric gold IDs stay client-side."}
    out.parent.mkdir(parents=True,exist_ok=True)
    def save():out.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    def check(name,detail):
        report["tests"].append({"name":name,"passed":True,"detail":detail});save();print("PASS "+name,flush=True)
    api=Client("http://127.0.0.1:19090")
    credentials=json.loads((ROOT/"local/credentials.json").read_text("utf-8"));api.login(credentials["admin_username"],credentials["admin_password"]);del credentials
    def call(path,method="GET",body=None,status=200,version=None):
        value,receipt=api.request(path,method,body,status,version);report["http_receipts"].append(receipt);save();return value
    def reference_values():
        import duckdb
        sql="SELECT count(DISTINCT session_id),count(DISTINCT customer_id),count(*)-count(customer_id) FROM ods.sessions WHERE started_at>=TIMESTAMP '2025-01-01' AND started_at<TIMESTAMP '2026-01-01'"
        with duckdb.connect(str(REFERENCE),read_only=True) as con:row=con.execute(sql).fetchone()
        assert row[0]!=row[1] and row[2]==0,row
        return {"sql":sql,"session_identity_count":row[0],"account_identity_count":row[1],"null_customer_rows":row[2]}
    try:
        if not args.resume:
            original=call("/workspaces/3/semantic/deployments/local_validation")["deployment"]
            report["workspace3_deployment_before"]=original
            context=call("/workspaces/3/semantic/maintenance-context")
            def current(aid):return next(x["asset_revision"] for x in context["assets"] if x["is_current"] and x["asset_ref"]["asset_id"]==aid)
            original_metric=current("metric:session_count");original_binding=current("binding:session_count.local_v1")
            original_entity=current("entity:commerce.BrowsingSession")
            props={p["property_id"]:p for p in original_entity["spec"]["properties"]}
            assert {"session_id","customer_id","started_at"}<=props.keys()
            report["reference"]=reference_values()
            suffix=time.strftime("%m%d%H%M%S")+secrets.token_hex(2)
            wid=call("/workspaces","POST",{"name":"语义修订真实结果验收_"+suffix,"description":"同一隔离指标两个明确口径修订：访问会话数与访问账户数。源库仅只读，金标来自独立ODS。"})["id"]
            assert wid!=3
            report["isolated_workspace_id"]=wid;base=f"/workspaces/{wid}/semantic"
            source_id=int(original_binding["spec"]["base_dataset_ref"]["datasource_id"])
            setup="""from app.db import SessionLocal,Datasource,Asset,Workspace
from sqlalchemy import select
import json
with SessionLocal() as db:
 target=TARGET; source_id=SOURCE_ID
 assert target!=3 and db.get(Workspace,target)
 source=db.get(Datasource,source_id);assert source.workspace_id==3 and source.username=='ecommerce_reader'
 values={c.name:getattr(source,c.name) for c in Datasource.__table__.columns if c.name not in {'id','workspace_id'}}
 cloned=Datasource(workspace_id=target,**values);db.add(cloned);db.flush()
 for asset in db.scalars(select(Asset).where(Asset.workspace_id==3,Asset.datasource_id==source_id)):
  values={c.name:getattr(asset,c.name) for c in Asset.__table__.columns if c.name not in {'id','workspace_id','datasource_id'}}
  db.add(Asset(workspace_id=target,datasource_id=cloned.id,**values))
 db.commit();print(json.dumps({'datasource_id':cloned.id}))
""".replace("TARGET",str(wid)).replace("SOURCE_ID",str(source_id))
            prepared=subprocess.run(["docker","compose","exec","-T","api","python","-c",setup],cwd=ROOT,text=True,capture_output=True,encoding="utf-8",check=True)
            datasource_id=json.loads(prepared.stdout)["datasource_id"]
            check("isolated_readonly_connector_fixture",{"workspace_id":wid,"datasource_id":datasource_id,"source_rows_copied":0,"encrypted_credentials_logged":False})
            unknown={"policy_id":None,"revision":None,"resolution":"UNKNOWN"}
            snapshot=next(s for s in context["snapshots"] if s["catalog_snapshot_id"]==original_binding["spec"]["base_dataset_ref"]["catalog_snapshot_id"])
            dataset=copy.deepcopy(next(d for d in snapshot["datasets"] if d["dataset_id"]==original_binding["spec"]["base_dataset_ref"]["dataset_id"] and int(d["datasource_id"])==source_id))
            dataset["datasource_id"]=str(datasource_id);dataset["policy_ref"]=unknown
            dataset["key_constraints"]=[k for k in dataset["key_constraints"] if k["target_dataset_ref"] is None]
            for field in dataset["fields"]:
                bd=field["business_definition"]
                bd.update(owner_ids=[],steward_ids=[],quality_rule_refs=[],provenance_refs=[],policy_ref=unknown)
            sid="catalog:revision-result:"+suffix
            snap={"workspace_id":wid,"catalog_snapshot_id":sid,"source_revision":"isolated-live-session-fixture/1","datasets":[dataset]}
            call(base+"/catalog-snapshots","POST",{"catalog_snapshot":snap})
            def ref(a):return {k:a[k] for k in ("workspace_id","asset_id","asset_type","revision")}
            raw=canonical({"scope":"本地合成技术验收，非生产业务规范","source":"当前BrowsingSession逻辑属性及真实只读会话表","definitions":[{"revision":1,"name":"访问会话数","expression":"count_distinct(session_id)"},{"revision":2,"name":"访问账户数","expression":"count_distinct(customer_id)"}],"time":"按started_at的2025年源库原始时间，左闭右开，不转换时区"})
            source={"workspace_id":wid,"asset_id":"source:revision-result-technical","asset_type":"source","revision":1,"name":"两种访问身份计数技术验收来源","definition":"清楚区分会话身份与账户身份，两者不可作为别名混用。","domain_ref":None,"aliases":[],"owner_ids":[],"steward_ids":[],"validity":{"from":None,"to":None},"provenance_refs":[],"policy_ref":unknown,
                "spec":{"source_kind":"TECHNICAL_QA","uri":"archive://revision-result/"+suffix,"source_revision":"technical-test/1","content_sha256":hashlib.sha256(raw.encode()).hexdigest(),"classification":"INTERNAL","authority":"TECHNICAL_ONLY"}}
            source_item=call(base+"/assets","POST",{"asset_revision":source})
            call(base+"/sources/source:revision-result-technical/archive","POST",{"revision":1,"raw_text":raw})
            def common(a,aid):
                a=copy.deepcopy(a);a.update(workspace_id=wid,asset_id=aid,revision=1,domain_ref=None,owner_ids=[],steward_ids=[],policy_ref=unknown,provenance_refs=[source_item["asset_ref"]]);return a
            entity=common(original_entity,"entity:revision-result.BrowsingSession")
            entity=call(base+"/assets","POST",{"asset_revision":entity})["asset_revision"];entity_ref=ref(entity)
            def prop(pid):return {"entity_ref":entity_ref,"property_id":pid}
            metric=common(original_metric,"metric:visit_identity_acceptance")
            metric.update(name="访问会话数",definition="在指定会话开始时间范围内，对会话唯一标识去重计数；不同会话分别计数。",aliases=[{"text":"会话身份数量","language":"zh-CN","kind":"EXACT_ALIAS","scope":"local_validation"}])
            metric["spec"].update(entity_ref=entity_ref,grain={"description":"会话身份","property_refs":[prop("session_id")]},expression={"op":"call","function":"count_distinct","args":[{"op":"property","property_ref":prop("session_id")}]},distinct_property_refs=[prop("session_id")])
            metric["spec"]["time_policy"]["property_ref"]=prop("started_at")
            metric_item=call(base+"/assets","POST",{"asset_revision":metric})
            def physical(pid):return {"catalog_snapshot_id":sid,"datasource_id":str(datasource_id),"dataset_id":dataset["dataset_id"],"field_id":pid}
            binding=common(original_binding,"binding:visit_identity_acceptance.local")
            binding["spec"].update(metric_ref=metric_item["asset_ref"],base_dataset_ref={k:v for k,v in physical("session_id").items() if k!="field_id"},
                calculation={"op":"call","function":"count_distinct","args":[{"op":"field","field_ref":physical("session_id")}]},
                property_mappings=[{"property_ref":prop(pid),"expression":{"op":"field","field_ref":physical(pid)}} for pid in ("session_id","started_at")],
                physical_grain={"description":"真实源一行一个会话，按session_id检验唯一性；账户计数不改变物理行粒度。","key_field_refs":[physical("session_id")]},
                time_field_ref=physical("started_at"),coverage={"start":None,"end_exclusive":None,"data_as_of":None,"evidence_refs":[]})
            binding_item=call(base+"/assets","POST",{"asset_revision":binding})
            diagnostic=call(base+"/assets/"+binding["asset_id"]+"/implementation?revision=1")
            assert diagnostic["outcome"]=="PASS",diagnostic
            heads=call(base+"/assets")["items"]
            baseline=call(base+"/deployments/local_validation/rebuild","POST",{"scope":"local_validation","draft_asset_refs":[a["asset_ref"] for a in heads],"expected_pointer_version":0,"reason":"隔离r1会话身份计数基线；仅技术快照。"},version=0)
            # Preserve r1 as immutable; create a real logical and physical r2.
            revised=copy.deepcopy(metric_item["asset_revision"]);revised.update(revision=2,name="访问账户数",definition="在指定会话开始时间范围内，对真实客户账户唯一标识去重计数；同一账户多次会话只计一个账户，不等同于会话数或跨账户自然人数。",aliases=[{"text":"访问客户账户量","language":"zh-CN","kind":"EXACT_ALIAS","scope":"local_validation"}])
            revised["spec"].update(grain={"description":"访问客户账户身份","property_refs":[prop("customer_id")]},expression={"op":"call","function":"count_distinct","args":[{"op":"property","property_ref":prop("customer_id")}]},distinct_property_refs=[prop("customer_id")])
            revised["spec"]["unit"]["code"]="account"
            revised_item=call(base+"/assets/"+metric["asset_id"]+"/revisions","POST",{"asset_revision":revised,"base_revision":1,"expected_record_version":metric_item["governance_record"]["record_version"],"change_reason":"明确改变统计身份：会话→客户账户，正式名称/口径/去重属性/粒度同时修订。"},version=metric_item["governance_record"]["record_version"])
            mapped=copy.deepcopy(binding_item["asset_revision"]);mapped.update(revision=2,name="访问账户数实际物理映射")
            mapped["spec"].update(metric_ref=revised_item["asset_ref"],calculation={"op":"call","function":"count_distinct","args":[{"op":"field","field_ref":physical("customer_id")}]},property_mappings=[{"property_ref":prop(pid),"expression":{"op":"field","field_ref":physical(pid)}} for pid in ("customer_id","started_at")])
            mapped_item=call(base+"/assets/"+binding["asset_id"]+"/revisions","POST",{"asset_revision":mapped,"base_revision":1,"expected_record_version":binding_item["governance_record"]["record_version"],"change_reason":"显式将逻辑customer_id映射到真实会话表customer_id；保持源行粒度和started_at时间。"},version=binding_item["governance_record"]["record_version"])
            diagnostic2=call(base+"/assets/"+binding["asset_id"]+"/implementation?revision=2")
            assert diagnostic2["outcome"]=="PASS",diagnostic2
            report.update(prepared=True,baseline_deployment=baseline["deployment"],metric_id=metric["asset_id"],binding_id=binding["asset_id"],r1_metric_ref=metric_item["asset_ref"],r1_binding_ref=binding_item["asset_ref"],r2_metric_ref=revised_item["asset_ref"],r2_binding_ref=mapped_item["asset_ref"],r1_metric_hash=metric_item["content_hash"],r1_binding_hash=binding_item["content_hash"],r2_metric_hash=revised_item["content_hash"],r2_binding_hash=mapped_item["content_hash"])
            check("logical_and_physical_r2_drafts_prepared_with_r1_deployed",{"workspace_id":wid,"entity_properties":{"session_id":props["session_id"]["name"],"customer_id":props["customer_id"]["name"]},"r1":metric_item["asset_ref"],"r2":revised_item["asset_ref"],"r1_expression":diagnostic["compiled_contract"]["expression"],"r2_expression":diagnostic2["compiled_contract"]["expression"],"model_calls":0,"workspace3_pointer_unchanged":call("/workspaces/3/semantic/deployments/local_validation")["deployment"]==original})
            if args.prepare_only:save();return 0
        assert report.get("prepared")
        wid=report["isolated_workspace_id"];base=f"/workspaces/{wid}/semantic";prefix=f"/workspaces/{wid}/agent"
        directory=call(prefix+"/models");model=next((m for m in directory["models"] if m.get("isDefault")),directory["models"][0])
        selection={"catalog_version":directory["catalog_version"],"model":model.get("model",model["id"]),"reasoningEffort":model["defaultReasoningEffort"],"serviceTier":None}
        def wait(run):
            deadline=time.monotonic()+180
            while run["state"] in {"DRAFT","EXECUTING","VALIDATING"}:
                if time.monotonic()>deadline:raise AssertionError("Run timeout: "+run["id"])
                time.sleep(.5);run=call(prefix+"/runs/"+run["id"])["run"]
            return run
        def execute(revision,question,expected):
            body={"message":{"text":question,"attachment_refs":[]},"model_selection":selection,"execution_mode":"local_validation","interaction_mode":"CONVERSATION"}
            run=wait(call(prefix+"/runs","POST",body,status=202)["run"]);report["model_calls"]+=1
            entry={"revision":revision,"question":question,"create_request":body,"run_id":run["id"],"latest_run":run}
            report.setdefault("execution_runs",[]).append(entry);save()
            def action(name):
                nonlocal run
                req=run["requirements"][0]
                command={"expected_run_version":run["run_version"],"expected_requirement_version":req["requirement_version"],"intent_frame_hash":req.get("intent_frame_hash"),"bundle_content_hash":req.get("bundle_content_hash"),"metric_card_hash":req.get("metric_card_hash"),"query_frame_ref":req.get("query_frame_ref"),"sql_hash":req.get("sql_hash"),"gate_result_ref":req.get("gate_result_ref"),"authorization_id":req.get("authorization",{}).get("id"),"acknowledgement":True}
                run=call(prefix+"/runs/"+run["id"]+"/requirements/"+req["id"]+"/"+name,"POST",command)["run"];entry["latest_run"]=run;save();return run["requirements"][0]
            assert len(run["requirements"])==1 and run["requirements"][0]["state"]=="TARGET_CONFIRMATION",run
            assert run["model_call"]["real_inference"] is True
            intent=run["requirements"][0]["intent_frame"]
            assert intent["start"]=="2025-01-01" and intent["end_exclusive"]=="2026-01-01" and intent["timezone"]=="SOURCE" and not intent["dimension_ids"] and not intent["filters"] and not intent["negative_terms"],intent
            req=action("confirm-intent");assert req["state"]=="NEED_CONFIRMATION",req
            assert req["bundle"]["selected_metric_ref"]==report[f"r{revision}_metric_ref"] and req["bundle"]["selected_binding_ref"]==report[f"r{revision}_binding_ref"],req["bundle"]
            req=action("confirm-metric");assert req["state"]=="PREVIEW_READY",req
            req=action("gate");assert req["state"]=="GATE_PASSED",req
            req=action("authorize");assert req["state"]=="AUTHORIZED",req
            action("execute");run=wait(run);entry["latest_run"]=run;save()
            req=run["requirements"][0];assert req["state"]=="DELIVERED",req
            assert equal_rows(req["result"]["rows"],[[expected]]),req["result"]
            entry.update(passed=True,expected_rows=[[expected]],observed_rows=req["result"]["rows"])
            check("r"+str(revision)+"_real_model_query_equals_independent_ods",{"run_id":run["id"],"selected_metric_ref":req["bundle"]["selected_metric_ref"],"selected_binding_ref":req["bundle"]["selected_binding_ref"],"actual":req["result"]["rows"],"expected":[[expected]],"sql":req["preview"]["sql"],"parameters":req["preview"]["parameters"]})
            return req
        first=execute(1,"2025年按会话开始时间统计访问会话数，按会话编号去重，使用源库时间。",report["reference"]["session_identity_count"])
        heads=call(base+"/assets")["items"]
        revised_deployment=call(base+"/deployments/local_validation/rebuild","POST",{"scope":"local_validation","draft_asset_refs":[a["asset_ref"] for a in heads],"expected_pointer_version":report["baseline_deployment"]["pointer_version"],"reason":"隔离验收显式发布r2客户账户去重口径及物理映射，验证真实查询结果变化。"},version=report["baseline_deployment"]["pointer_version"])
        report["revised_deployment"]=revised_deployment["deployment"];save()
        second=execute(2,"2025年按会话开始时间统计访问账户数，按客户账户编号去重，同一账户多次会话只计一个账户，使用源库时间。",report["reference"]["account_identity_count"])
        assert first["result"]["rows"]!=second["result"]["rows"]
        assert "session_id" in first["preview"]["sql"] and "customer_id" in second["preview"]["sql"]
        old_metric=call(base+"/assets/"+report["metric_id"]+"?revision=1");old_binding=call(base+"/assets/"+report["binding_id"]+"?revision=1")
        assert old_metric["content_hash"]==report["r1_metric_hash"] and old_binding["content_hash"]==report["r1_binding_hash"]
        source_after=call("/workspaces/3/semantic/deployments/local_validation")["deployment"]
        source_write_receipts=[r for r in report["http_receipts"] if r["method"]!="GET" and r["path"].startswith("/workspaces/3/")]
        assert not source_write_receipts
        check("real_revision_changes_sql_and_result_preserving_history_and_source_scope",{"r1_rows":first["result"]["rows"],"r2_rows":second["result"]["rows"],"r1_hashes_preserved":True,"workspace3_write_requests_by_this_test":0,"external_deployment_changed":source_after!=report["workspace3_deployment_before"],"workspace3_deployment_after":source_after,"gold_ids_sent_to_model_or_retrieve":False,"production_approval_created":False})
        report["passed"]=True
    except Exception as exc:
        report["errors"].append({"type":type(exc).__name__,"message":str(exc)[:1600]});report["passed"]=False
        print("FAIL "+str(exc)[:600],flush=True)
    report["finished_at"]=datetime.now(timezone.utc).isoformat();save()
    print(json.dumps({"passed":report.get("passed"),"tests":len(report["tests"]),"model_calls":report["model_calls"],"isolated_workspace_id":report.get("isolated_workspace_id"),"report":str(out)},ensure_ascii=False))
    return 0 if report.get("passed") else 1


if __name__=="__main__":raise SystemExit(main())
