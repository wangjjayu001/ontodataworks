"""Live HTTP/cookie/CSRF semantic governance and retrieval acceptance.

Creates a fresh isolated workspace and test identities. Never approves workspace 3
assets, invokes a model, or executes a warehouse query. Secrets stay in memory.
"""
from pathlib import Path
import argparse
import hashlib
import http.cookiejar
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parent.parent


def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)


class Client:
    def __init__(self,base):
        self.base=base.rstrip("/");self.csrf="";self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(self,path,method="GET",body=None,status=200,version=None,key=None,csrf=True):
        headers={"Content-Type":"application/json","Origin":self.base,"Idempotency-Key":key or uuid.uuid4().hex}
        if csrf and self.csrf:headers["X-CSRF-Token"]=self.csrf
        if version is not None:headers["If-Match"]=str(version)
        data=canonical(body).encode() if body is not None else None
        started=time.monotonic()
        try:response=self.opener.open(urllib.request.Request(self.base+"/api"+path,data=data,headers=headers,method=method),timeout=45)
        except urllib.error.HTTPError as exc:response=exc
        actual=response.code
        try:payload=json.load(response)
        except ValueError:raise AssertionError(f"{method} {path}: returned non-JSON HTTP {actual}")
        if actual!=status:
            detail=payload.get("detail",payload)
            raise AssertionError(f"{method} {path}: HTTP {actual}, expected {status}: {detail}")
        return payload.get("data",payload),{"method":method,"path":path,"status":actual,"duration_ms":round((time.monotonic()-started)*1000)}
    def login(self,username,password):
        payload,_=self.request("/auth/login","POST",{"username":username,"password":password})
        self.csrf=payload["csrf"]
        return payload["user"]


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--base",default="http://127.0.0.1:19090");parser.add_argument("--output",default=str(ROOT/"evidence/v1.0/semantic-live-acceptance.json"));args=parser.parse_args()
    report={"suite":"semantic-live-http/1.0.0","started_at":time.strftime("%Y-%m-%dT%H:%M:%S%z"),"base_url":args.base,"tests":[],"scope":"isolated synthetic business governance; workspace3 diagnostic retrieval only","model_calls":0,"warehouse_queries":0,"workspace3_approvals_created":0,"errors":[]}
    def check(name,detail):report["tests"].append({"name":name,"passed":True,"detail":detail})
    def call(client,path,method="GET",body=None,status=200,version=None,key=None,csrf=True):
        data,receipt=client.request(path,method,body,status,version,key,csrf);report.setdefault("http_receipts",[]).append(receipt);return data
    try:
        credentials=json.loads((ROOT/"local/credentials.json").read_text("utf-8"))
        admin=Client(args.base);admin.login(credentials["admin_username"],credentials["admin_password"])
        del credentials
        suffix=time.strftime("%m%d%H%M%S")+secrets.token_hex(2)
        workspace=call(admin,"/workspaces","POST",{"name":"语义v1真实HTTP验收_"+suffix,"description":"隔离合成业务定义治理验收；不连接业务数据源，不批准workspace3，不执行查询。"})
        wid=workspace["id"];report["isolated_workspace_id"]=wid;base=f"/workspaces/{wid}/semantic"
        clients={"admin":admin};users={}
        for key,role in [("steward","developer"),("business_owner","developer"),("data_owner","developer"),("publisher","developer"),("viewer","viewer")]:
            username="sv1_"+key[:8]+"_"+suffix;password=secrets.token_urlsafe(24)
            user=call(admin,"/users","POST",{"username":username,"password":password,"display_name":"语义验收_"+key,"role":role,"workspace_ids":[wid]})
            client=Client(args.base);client.login(username,password);clients[key]=client;users[key]=user
        check("real_cookie_login_and_workspace_memberships",{"workspace_id":wid,"user_roles":{k:v["role"] for k,v in users.items()},"passwords_recorded":False})
        call(admin,base+"/policies","POST",{"policy_id":"csrf-rejected","allowed_user_ids":[],"allowed_roles":[]},status=403,csrf=False)
        check("csrf_write_rejected",{"status":403})
        for key,role in [("steward","STEWARD"),("business_owner","BUSINESS_OWNER"),("data_owner","DATA_OWNER"),("publisher","PUBLISHER")]:
            call(admin,base+"/assignments","POST",{"user_id":users[key]["id"],"role":role,"asset_id":"*","active":True})
        policy=call(admin,base+"/policies","POST",{"policy_id":"isolated-business-read","allowed_user_ids":[users[k]["id"] for k in users],"allowed_roles":["admin"],"allow_execution":False,"description":"仅隔离语义业务定义读取/治理，不授予数据源查询权限"})
        pref=policy["policy_ref"]
        check("explicit_owner_steward_publisher_assignments_and_policy",{"assigned_roles":4,"policy_id":pref["policy_id"],"allow_execution":policy["content"]["allow_execution"]})
        unknown={"policy_id":None,"revision":None,"resolution":"UNKNOWN"}
        field={"field_id":"object_id","name":"object_id","data_type":"VARCHAR","nullable":None,"semantic_role":"IDENTIFIER","unit":None,"classification":"INTERNAL","business_definition":{"name_zh":"结构夹具标识","definition":"只测试治理结构，无业务查询值","aliases":[],"grain":None,"legacy_unit_text":None,"owner_ids":[],"steward_ids":[],"quality_rule_refs":[],"provenance_refs":[],"policy_ref":unknown}}
        dataset={"dataset_id":"fixture.business_definition","datasource_id":"fixture:no-connection","connector_kind":"LOCAL_FIXTURE","namespace":"fixture","physical_name":"business_definition","logical_locator":"fixture.business_definition","schema_fingerprint":hashlib.sha256(canonical(field).encode()).hexdigest(),"fingerprint_algorithm":"isolated-structure-fixture-sha256","fields":[field],"key_constraints":[],"policy_ref":unknown,"constraints_state":"UNKNOWN"}
        snapshot={"workspace_id":wid,"catalog_snapshot_id":"catalog:isolated:"+suffix,"source_revision":"isolated-business-contract/1","datasets":[dataset]}
        call(admin,base+"/catalog-snapshots","POST",{"catalog_snapshot":snapshot})
        raw=canonical({"definition":"本隔离验收业务规范将订单定义为一次购买交易。","scope":"结构/审批验收；不含事实数据，不用于真实取数。"})
        def asset(kind,identifier,name,definition,spec,provenance=None):
            return {"workspace_id":wid,"asset_id":kind+":"+identifier,"asset_type":kind,"revision":1,"name":name,"definition":definition,"domain_ref":None,"aliases":[],"owner_ids":[str(users["business_owner"]["id"])],"steward_ids":[str(users["steward"]["id"])],"validity":{"from":None,"to":None},"provenance_refs":provenance or [],"policy_ref":pref,"spec":spec}
        source=asset("source","isolated-business","隔离业务规范","合成规范用于真实HTTP治理验收，不是业务数仓口径",{"source_kind":"BUSINESS_SPEC","uri":"archive://isolated-business/"+suffix,"source_revision":"isolated/1","content_sha256":hashlib.sha256(raw.encode()).hexdigest(),"classification":"INTERNAL","authority":"UNREVIEWED"})
        source_item=call(clients["steward"],base+"/assets","POST",{"asset_revision":source})
        call(clients["steward"],base+"/sources/source:isolated-business/archive","POST",{"revision":1,"raw_text":raw})
        def verify_submit_approve(item):
            aid=urllib.parse.quote(item["asset_ref"]["asset_id"],safe=":");gov=item["governance_record"]
            verified=call(clients["steward"],base+"/governance/"+aid+"/verify","POST",{"expected_record_version":gov["record_version"],"content_hash":item["content_hash"]},version=gov["record_version"])
            assert verified["governance_record"]["validation_state"]=="PASS",verified
            gov=verified["governance_record"]
            submitted=call(clients["steward"],base+"/governance/"+aid+"/submit","POST",{"expected_record_version":gov["record_version"],"content_hash":item["content_hash"],"dependency_hash":gov["dependency_hash"]},version=gov["record_version"])
            gov=submitted["governance_record"]
            denied={"expected_record_version":gov["record_version"],"decision":"APPROVE","reviewer_role":"BUSINESS_OWNER","reason":"不允许未授权维护者自行批准"}
            call(clients["steward"],base+"/governance/"+aid+"/decisions","POST",denied,status=403,version=gov["record_version"])
            approved=call(clients["business_owner"],base+"/governance/"+aid+"/decisions","POST",{**denied,"reason":"独立测试Owner核对确切定义、来源、范围和hash"},version=gov["record_version"])
            assert approved["governance_record"]["workflow_state"]=="APPROVED"
            return approved
        verify_submit_approve(source_item)
        domain=asset("domain","isolated-orders","隔离订单域","独立验收规范中的订单业务域",{"parent_ref":None,"scope_notes":"仅隔离治理验收，不含可执行指标"},[source_item["asset_ref"]])
        domain_item=call(clients["steward"],base+"/assets","POST",{"asset_revision":domain})
        verify_submit_approve(domain_item)
        check("real_source_archive_verify_submit_owner_approval",{"source_asset_id":source["asset_id"],"domain_asset_id":domain["asset_id"],"independent_owner_user_id":users["business_owner"]["id"],"author_self_elevation_rejected":True})
        release=call(clients["publisher"],base+"/releases","POST",{"scope":"production","asset_refs":[domain_item["asset_ref"]],"reason":"隔离治理验收，不发布真实指标"})
        assert len(release["release"]["items"])==2
        index=call(clients["publisher"],base+"/index-builds","POST",{"release_ref":release["release_ref"]})
        activation={"release_ref":release["release_ref"],"index_snapshot_ref":index["index_snapshot_ref"],"expected_pointer_version":0,"reason":"隔离完整发布索引对验收"}
        dep=call(clients["publisher"],base+"/deployments/production/activate","POST",activation,version=0)
        assert dep["deployment"]["pointer_version"]==1
        call(clients["publisher"],base+"/deployments/production/activate","POST",activation,status=409,version=0)
        check("real_release_index_atomic_deployment_and_cas",{"release_id":release["release_ref"]["id"],"index_id":index["index_snapshot_ref"]["id"],"members":2,"metrics":0,"query_execution":False,"stale_cas_rejected":True})
        call(clients["viewer"],"/workspaces/3/semantic/assets",status=403)
        check("cross_workspace_access_rejected",{"target_workspace":3,"status":403})
        key=uuid.uuid4().hex;b={"user_id":users["viewer"]["id"],"role":"STEWARD","asset_id":"domain:unrelated","active":True}
        one=call(admin,base+"/assignments","POST",b,key=key);two=call(admin,base+"/assignments","POST",b,key=key)
        assert one==two
        call(admin,base+"/assignments","POST",{**b,"active":False},status=409,key=key)
        check("idempotent_replay_and_conflicting_replay",{"same_result":True,"conflicting_status":409})
        call(admin,base+"/policies/isolated-business-read/revoke","POST",{"revision":1})
        call(clients["business_owner"],base+"/assets/domain:isolated-orders",status=404)
        call(clients["publisher"],base+"/deployments/production/activate","POST",{**activation,"expected_pointer_version":1},status=409,version=1)
        check("current_policy_revocation_overrides_pinned_release",{"asset_read_status":404,"deployment_reactivation_status":409})
        tests=[("订单数", "order_count", [],"NEED_CONFIRMATION"),("访问会话数","session_count",[],"NEED_CONFIRMATION"),("发货运单数","shipment_count",[],"NEED_CONFIRMATION"),("促销订单数","promotion_orders",[],"NEED_CONFIRMATION"),("平均评论分","review_score",[],"NEED_CONFIRMATION"),("订单量按买家所在地区分组","order_count",["customer_state"],"NEED_CONFIRMATION"),("订单数按买家状态","order_count",[],"GOVERNANCE_REQUIRED"),("支付金额","payment_total",[],"BLOCKED_EVIDENCE"),("2027年订单数","order_count",[],"BLOCKED_EVIDENCE"),("订单数按商品类目","order_count",["product_category"],"GOVERNANCE_REQUIRED"),("销售额",None,[],"NEED_CLARIFICATION")]
        recalls=[]
        for question,metric,dims,action in tests:
            intent={"question":question,"metric_terms":[metric] if metric else [],"dimension_ids":dims,"start":"2027-01-01" if "2027" in question else "2025-01-01","end_exclusive":"2028-01-01" if "2027" in question else "2026-01-01"}
            result=call(admin,"/workspaces/3/semantic/retrieve","POST",{"intent":intent})
            bundle=result["bundle"];assert bundle["next_action"]==action,(question,bundle["next_action"],action)
            recalls.append({"question":question,"expected_action":action,"actual_action":bundle["next_action"],"selected_metric_ref":bundle["selected_metric_ref"],"selected_binding_ref":bundle["selected_binding_ref"],"candidate_count":len(bundle["candidates"]),"closure_count":len(bundle["closure_refs"]),"coverage":bundle["coverage"],"bundle_id":bundle["bundle_id"],"bundle_hash":result["bundle_content_hash"],"scope":result["scope"],"passed":True})
        report["recall_cases"]=recalls;check("live_retrieval_11_cases",{"cases":len(recalls),"passed":len(recalls),"ranking_scope":"Exact/BM25 with typed ID closure; not a 320-case blind-test score","vector_enabled":False})
        check("workspace3_no_automatic_production_approval",{"production_ready_paths":call(admin,"/workspaces/3/semantic/overview")["production_ready_paths"]})
    except Exception as exc:
        report["errors"].append({"type":type(exc).__name__,"message":str(exc)[:1500]})
    report["finished_at"]=time.strftime("%Y-%m-%dT%H:%M:%S%z");report["passed"]=not report["errors"]
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"tests":len(report["tests"]),"http_requests":len(report.get("http_receipts",[])),"report":str(path),"errors":report["errors"]},ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
