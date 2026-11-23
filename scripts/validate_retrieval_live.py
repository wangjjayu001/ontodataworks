"""Real cookie/HTTP semantic recall acceptance; no model calls or SQL execution.

Gold IDs are scoring-only. No metric_id/metric hints are sent to retrieve.
Only independent known-ID resolution is counted separately from search.
"""
from __future__ import annotations
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import time
import uuid
import sys
import urllib.parse
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from validate_v1_live import Api


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def main():
    api=Api();api.login();base="/workspaces/3/semantic"
    cases_path=ROOT/"scripts/retrieval-live-cases.v1.json"
    case_set=json.loads(cases_path.read_text("utf-8"))
    schema=json.loads((ROOT/"backend/app/contracts/semantic-retrieval.schema.json").read_text("utf-8"))
    validator=Draft202012Validator({"$schema":schema["$schema"],"$defs":schema["$defs"],"$ref":"#/$defs/retrieval_bundle"})
    report={"case_set_version":case_set["case_set_version"],"case_set_hash":hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "scope":"Real localhost HTTP/CSRF/cookie, Exact+BM25 recall on local synthetic technical snapshot. New development set; not independent business blind test, no model ranking gain, no SQL result correctness claim.",
        "gold_ids_sent_to_retrieve":False,"model_inference_performed":False,"sql_executed":False,"vector_enabled":False,
        "started_at":datetime.now(timezone.utc).isoformat(),"retrieval_cases":[],"known_id_resolution":[],"governance_policy_tests":[],"errors":[]}
    out=ROOT/"evidence/v1.0/retrieval-live-acceptance.json";out.parent.mkdir(parents=True,exist_ok=True)
    def save():out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    dep=api.call(base+"/deployments/local_validation")["data"]["deployment"]
    report["deployment"]=dep
    release_views=api.call(base+"/releases")["data"]["items"]
    release=next(v["release"] for v in release_views if v["release_ref"]["id"]==dep["release_id"])
    release_hashes={(i["asset_ref"]["asset_id"],i["asset_ref"]["revision"]):i["content_hash"] for i in release["items"]}
    snapshots=api.call(base+"/catalog-snapshots")["data"]["items"]
    snapshot_map={s["catalog_snapshot_id"]:s for s in snapshots}
    asset_cache={}
    def resolve(ref):
        key=(ref["asset_id"],ref["revision"])
        if key not in asset_cache:
            data=api.call(base+"/assets/"+urllib.parse.quote(ref["asset_id"],safe="")+"?revision="+str(ref["revision"]))["data"]
            asset=data["asset_revision"]
            assert data["asset_ref"]==ref and data["content_hash"]==sha(asset)
            asset_cache[key]=data
        return asset_cache[key]
    for case in case_set["cases"]:
        request={"run_id":"recall_"+uuid.uuid4().hex,"requirement_id":case["id"],"intent":{
            "question":case["question"],"start":case.get("start","2025-01-01"),"end_exclusive":case.get("end_exclusive","2026-01-01"),
            "dimension_ids":case.get("dimensions",[]),"negative_terms":case.get("negative_terms",[]),"timezone":"SOURCE"}}
        gold="metric:"+case["expected_metric"] if case.get("expected_metric") else None
        result={"id":case["id"],"question":case["question"],"request":request,"passed":False,
            "search":{"gold_metric_id":gold,"metric_top1":[],"metric_top5":[],"gold_rank":None,
                "hit1":False if gold else None,"hit5":False if gold else None,"rr":0 if gold else None}}
        try:
            started=time.monotonic();response=api.call(base+"/retrieve","POST",request);latency=(time.monotonic()-started)*1000
            data=response["data"];bundle=data["bundle"]
            errors=sorted(validator.iter_errors(bundle),key=lambda e:list(map(str,e.path)))
            assert not errors, "Bundle schema mismatch: "+"; ".join(e.message for e in errors[:3])
            assert bundle["release_hash"]==dep["release_hash"] and bundle["index_snapshot_hash"]==dep["index_snapshot_hash"]
            assert response["request_id"]==bundle["request_id"] and response["trace_id"]==bundle["trace_id"]
            assert data["bundle_content_hash"]==sha(bundle)
            metric_candidates=[c for c in bundle["candidates"] if c["role"]=="metric"]
            candidate_ids=[c["target_ref"]["asset_id"] for c in metric_candidates]
            expected=case.get("expected_metric");gold="metric:"+expected if expected else None
            rank=candidate_ids.index(gold)+1 if gold in candidate_ids else None
            selected=bundle["selected_metric_ref"]["asset_id"] if bundle["selected_metric_ref"] else None
            assertions={"schema":True,"fixed_snapshot":True,"envelope_identity":True,"bundle_hash":True}
            if gold:assertions["selected_path"]=selected==gold
            if case.get("expected_selected_null"):assertions["selected_null"]=selected is None
            if "expected_route" in case:assertions["route"]=bundle["next_action"]==case["expected_route"]
            required=set(case.get("required_fields",[]));fields={r["target_ref"]["field_id"] for r in bundle["closure_refs"] if r["kind"]=="FIELD"}
            if required:assertions["required_field_closure"]=required<=fields
            asset_resolved=physical_resolved=evidence_count=0
            for dep_ref in bundle["closure_refs"]:
                target=dep_ref["target_ref"]
                if dep_ref["kind"]=="ASSET":
                    resolved=resolve(target);asset_resolved+=1
                    assert resolved["content_hash"]==release_hashes[(target["asset_id"],target["revision"])]
                    if target["asset_type"]=="evidence":evidence_count+=1
                else:
                    snap=snapshot_map[target["catalog_snapshot_id"]]
                    dataset=next(d for d in snap["datasets"] if d["dataset_id"]==target["dataset_id"] and d["datasource_id"]==target["datasource_id"])
                    if dep_ref["kind"]=="FIELD":assert any(f["field_id"]==target["field_id"] for f in dataset["fields"])
                    physical_resolved+=1
            assertions["closure_resolves_to_fixed_authority"]=True
            result.update(passed=all(assertions.values()),assertions=assertions,latency_ms=round(latency,3),
                search={"gold_metric_id":gold,"metric_top1":candidate_ids[:1],"metric_top5":candidate_ids[:5],"gold_rank":rank,"hit1":rank==1 if gold else None,"hit5":bool(rank and rank<=5) if gold else None,"rr":1/rank if rank else 0 if gold else None,"candidate_count_by_role":{role:sum(c["role"]==role for c in bundle["candidates"]) for role in {c["role"] for c in bundle["candidates"]}},"metric_channel_details":metric_candidates},
                path={"selected_metric":bundle["selected_metric_ref"],"selected_binding":bundle["selected_binding_ref"],"requested_dimensions":bundle["requested_dimension_refs"]},
                closure={"asset_refs_resolved":asset_resolved,"physical_refs_resolved":physical_resolved,"evidence_refs":evidence_count,"required_fields":sorted(required),"observed_fields":sorted(fields),"refs":bundle["closure_refs"]},
                route={"next_action":bundle["next_action"],"coverage":bundle["coverage"],"truncated":bundle["truncated"],"timed_out_channels":bundle["timed_out_channels"]},bundle=bundle)
        except Exception as exc:
            result["error"]=str(exc)
        report["retrieval_cases"].append(result);save();print(("PASS " if result["passed"] else "FAIL ")+case["id"],flush=True)
    # Known-ID GET is independently measured; these IDs never enter search requests.
    metric_ids=sorted({c["expected_metric"] for c in case_set["cases"] if c.get("expected_metric")})
    for mid in metric_ids:
        r={"id":"metric:"+mid,"passed":False,"resolution_scope":"Deployed fixed Release revision; separate known-ID GET"}
        try:
            deployed_ref=next(item["asset_ref"] for item in release["items"] if item["asset_ref"]["asset_id"]=="metric:"+mid)
            started=time.monotonic();data=api.call(base+"/assets/"+urllib.parse.quote("metric:"+mid,safe="")+"?revision="+str(deployed_ref["revision"]))["data"]
            r.update(passed=data["asset_ref"]==deployed_ref and data["content_hash"]==sha(data["asset_revision"])==release_hashes[(deployed_ref["asset_id"],deployed_ref["revision"])],latency_ms=round((time.monotonic()-started)*1000,3),content_hash=data["content_hash"],asset_ref=data["asset_ref"])
        except Exception as exc:r["error"]=str(exc)
        report["known_id_resolution"].append(r);save()
    def policy_check(name, fn):
        try:detail=fn();r={"name":name,"passed":True,"detail":detail}
        except Exception as exc:r={"name":name,"passed":False,"error":str(exc)}
        report["governance_policy_tests"].append(r);save();print(("PASS " if r["passed"] else "FAIL ")+name,flush=True)
    state={}
    def create_policy_source():
        suffix=uuid.uuid4().hex[:12];pid="acceptance:source:"+suffix
        wid=api.call("/workspaces","POST",{"name":"召回权限隔离验收_"+suffix,"description":"仅验证来源读取政策；不变更workspace3语义资产、部署或业务批准。"})["id"]
        policy_base=f"/workspaces/{wid}/semantic"
        p=api.call(policy_base+"/policies","POST",{"policy_id":pid,"allowed_user_ids":[],"allowed_roles":["admin"],"allow_execution":False,"description":"Isolated acceptance source-read policy; not a production execution grant"})["data"]
        source=api.call(base+"/assets?type=source")["data"]["items"][0]["asset_revision"]
        source=copy.deepcopy(source);source["workspace_id"]=wid;source["asset_id"]="source:acceptance-policy-"+suffix;source["revision"]=1;source["name"]="接口验收来源（隔离草稿）";source["policy_ref"]=p["policy_ref"]
        created=api.call(policy_base+"/assets","POST",{"asset_revision":source})["data"]
        state.update(policy_base=policy_base,policy_id=pid,policy_ref=p["policy_ref"],asset_ref=created["asset_ref"])
        read=api.call(policy_base+"/assets/"+urllib.parse.quote(source["asset_id"],safe="")+"?revision=1")["data"]
        assert read["asset_revision"]["policy_ref"]==p["policy_ref"]
        return {"isolated_workspace_id":wid,"source_ref":created["asset_ref"],"policy_ref":p["policy_ref"],"added_to_deployment":False,"business_approved":False}
    policy_check("isolated_source_current_policy_read",create_policy_source)
    def revoke():
        assert state,"policy setup did not complete"
        api.call(state["policy_base"]+"/policies/"+urllib.parse.quote(state["policy_id"],safe="")+"/revoke","POST",{"revision":state["policy_ref"]["revision"]})
        response=api.call(state["policy_base"]+"/assets/"+urllib.parse.quote(state["asset_ref"]["asset_id"],safe="")+"?revision=1",expected=404)
        return {"source_ref":state["asset_ref"],"status":404,"error":response.get("detail"),"administrator_fallback_after_explicit_revocation":False}
    policy_check("revoked_source_policy_blocks_current_read",revoke)
    def immutable_deployment():
        after=api.call(base+"/deployments/local_validation")["data"]["deployment"]
        assert after==dep
        return {"pointer_version":after["pointer_version"],"release_hash":after["release_hash"],"index_snapshot_hash":after["index_snapshot_hash"]}
    policy_check("acceptance_drafts_do_not_change_active_deployment",immutable_deployment)
    scored=[r for r in report["retrieval_cases"] if r.get("search",{}).get("gold_metric_id")]
    required_ids={c["id"] for c in case_set["cases"] if c.get("required_fields")}
    route_ids={c["id"] for c in case_set["cases"] if "expected_route" in c}
    required=[r for r in report["retrieval_cases"] if r["id"] in required_ids]
    routes=[r for r in report["retrieval_cases"] if r["id"] in route_ids]
    latency=[r["latency_ms"] for r in report["retrieval_cases"] if "latency_ms" in r]
    report["metrics"]={"search":{"scored_cases":len(scored),"top1_hits":sum(r["search"]["hit1"] for r in scored),"top5_hits":sum(r["search"]["hit5"] for r in scored),"mrr":statistics.mean(r["search"]["rr"] for r in scored) if scored else None},
        "path":{"selected_correct":sum(r.get("assertions",{}).get("selected_path",False) for r in report["retrieval_cases"]),"scored_cases":len(scored)},
        "closure":{"required_field_cases":len(required),"complete_cases":sum(r.get("assertions",{}).get("required_field_closure",False) for r in required)},
        "route":{"scored_cases":len(routes),"correct_cases":sum(r.get("assertions",{}).get("route",False) for r in routes)},
        "schema":{"cases":len(report["retrieval_cases"]),"passed":sum(r.get("assertions",{}).get("schema",False) for r in report["retrieval_cases"])},
        "latency_ms":{"median":statistics.median(latency) if latency else None,"p95":sorted(latency)[min(len(latency)-1,int(len(latency)*.95))] if latency else None,"max":max(latency) if latency else None}}
    alltests=report["retrieval_cases"]+report["known_id_resolution"]+report["governance_policy_tests"]
    report.update(completed_at=datetime.now(timezone.utc).isoformat(),passed=all(t["passed"] for t in alltests),test_count=len(alltests))
    save();print(json.dumps({"passed":report["passed"],"test_count":report["test_count"],"metrics":report["metrics"]},ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
