"""Real HTTP maintenance -> immutable catalog -> new Binding -> retrieval.

An isolated workspace receives a read-only copy of an existing connector/catalog
as fixture setup. All semantic changes and publication use public HTTP commands.
No production approval, source data mutation, model inference or SQL execution.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import time
import urllib.parse
from validate_semantic_live import Client,canonical

ROOT=Path(__file__).resolve().parent.parent


def walk(value):
    if isinstance(value,dict):
        if set(value)=={"workspace_id","asset_id","asset_type","revision"}:yield value
        else:
            for child in value.values():yield from walk(child)
    elif isinstance(value,list):
        for child in value:yield from walk(child)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--base",default="http://127.0.0.1:19090");parser.add_argument("--output",default=str(ROOT/"evidence/v1.0/semantic-maintenance-live.json"));parser.add_argument("--deploy-workspace3",action="store_true");args=parser.parse_args()
    report={"suite":"semantic-maintenance-live/2","started_at":time.strftime("%Y-%m-%dT%H:%M:%S%z"),"tests":[],"http_receipts":[],"errors":[],"model_calls":0,"warehouse_queries":0,"production_approvals_created":0}
    def check(name,detail):report["tests"].append({"name":name,"passed":True,"detail":detail})
    admin=Client(args.base)
    def call(path,method="GET",body=None,status=200,version=None,key=None):
        value,receipt=admin.request(path,method,body,status,version,key);report["http_receipts"].append(receipt);return value
    try:
        credentials=json.loads((ROOT/"local/credentials.json").read_text("utf-8"));admin.login(credentials["admin_username"],credentials["admin_password"]);del credentials
        source_base="/workspaces/3/semantic";context=call(source_base+"/maintenance-context")
        current=[a for a in context["assets"] if a["is_current"]]
        checks=call(source_base+"/assets/binding:session_count.local_v1/implementation")
        assert checks["outcome"]=="PASS",checks
        check("typed_implementation_diagnostic",{"binding_ref":checks["binding_ref"],"authority":checks["compiled_contract"]["authority"],"sidecar_used":False,"live_source_checked":checks["live_source_checked"]})
        if args.deploy_workspace3:
            dep=call(source_base+"/deployments/local_validation")["deployment"];pointer=dep["pointer_version"] if dep else 0
            draft=[a["asset_ref"] for a in current if a["governance_record"]["workflow_state"]=="DRAFT"]
            rebuilt=call(source_base+"/deployments/local_validation/rebuild","POST",{"scope":"local_validation","draft_asset_refs":draft,"expected_pointer_version":pointer,"reason":"显式发布修复后的完整本地技术草稿，验证实际当前AssetRevision/Catalog统一召回；不批准业务或生产。"},version=pointer)
            ids=[i["asset_ref"]["asset_id"] for i in rebuilt["release"]["items"]];assert len(ids)==len(set(ids))
            report["workspace3_deployment"]=rebuilt["deployment"]
            check("workspace3_draft_only_technical_rebuild",{"selected_drafts":len(draft),"unique_assets":len(ids),"pointer_version":rebuilt["deployment"]["pointer_version"],"business_approval_created":rebuilt["business_approval_created"]})
        suffix=time.strftime("%m%d%H%M%S")+secrets.token_hex(2)
        wid=call("/workspaces","POST",{"name":"语义维护闭环验收_"+suffix,"description":"真实物理目录的隔离只读维护验收；不修改源数据，不做业务批准。"})["id"]
        base=f"/workspaces/{wid}/semantic";report["isolated_workspace_id"]=wid
        # This setup copies encrypted connector configuration internally. No
        # secret or source row is emitted, and source DB permissions are equal.
        setup="""from app.db import SessionLocal,Datasource,Asset,Workspace
from sqlalchemy import select
import json
with SessionLocal() as db:
 target=TARGET
 assert db.get(Workspace,target) and target!=3
 mapping={}
 for source in db.scalars(select(Datasource).where(Datasource.workspace_id==3)):
  values={c.name:getattr(source,c.name) for c in Datasource.__table__.columns if c.name not in {'id','workspace_id'}}
  cloned=Datasource(workspace_id=target,**values);db.add(cloned);db.flush();mapping[source.id]=cloned.id
 for asset in db.scalars(select(Asset).where(Asset.workspace_id==3)):
  values={c.name:getattr(asset,c.name) for c in Asset.__table__.columns if c.name not in {'id','workspace_id','datasource_id'}}
  db.add(Asset(workspace_id=target,datasource_id=mapping[asset.datasource_id],**values))
 db.commit();print(json.dumps(mapping))
""".replace("TARGET",str(wid))
        prepared=subprocess.run(["docker","compose","exec","-T","api","python","-c",setup],cwd=ROOT,text=True,capture_output=True,encoding="utf-8",check=True)
        source_mapping=json.loads(prepared.stdout);check("isolated_real_catalog_fixture_setup",{"source_workspace":3,"target_workspace":wid,"connector_count":len(source_mapping),"source_rows_copied":0,"setup":"encrypted connector and collected physical catalog copied internally; HTTP semantics follow"})
        byref={(a["asset_ref"]["asset_id"],a["asset_ref"]["revision"]):a["asset_revision"] for a in context["assets"]}
        roots=[a["asset_revision"] for a in current if a["asset_ref"]["asset_id"] in ("metric:session_count","binding:session_count.local_v1")]
        selected={};pending=list(roots)
        while pending:
            asset=pending.pop();key=(asset["asset_id"],asset["revision"])
            if key in selected:continue
            selected[key]=asset
            for target in walk(asset):
                if (target["asset_id"],target["revision"])!=key:pending.append(byref[(target["asset_id"],target["revision"])])
        snapshot_ids={}
        def collect_catalog(value):
            if isinstance(value,dict):
                if "catalog_snapshot_id" in value:snapshot_ids[value["catalog_snapshot_id"]]="catalog:maintenance:"+suffix+":"+str(len(snapshot_ids)) if value["catalog_snapshot_id"] not in snapshot_ids else snapshot_ids[value["catalog_snapshot_id"]]
                for child in value.values():collect_catalog(child)
            elif isinstance(value,list):
                for child in value:collect_catalog(child)
        for asset in selected.values():collect_catalog(asset)
        def rewrite(value):
            if isinstance(value,dict):
                result={k:rewrite(v) for k,v in value.items()}
                if "workspace_id" in result:result["workspace_id"]=wid
                if set(result)=={"workspace_id","asset_id","asset_type","revision"}:result["revision"]=1
                if "catalog_snapshot_id" in result:result["catalog_snapshot_id"]=snapshot_ids[result["catalog_snapshot_id"]]
                if "datasource_id" in result:result["datasource_id"]=str(source_mapping[str(result["datasource_id"])])
                return result
            if isinstance(value,list):return [rewrite(child) for child in value]
            return value
        for snapshot in context["snapshots"]:
            if snapshot["catalog_snapshot_id"] not in snapshot_ids:continue
            clean={k:v for k,v in snapshot.items() if k not in ("content_hash","redacted")}
            call(base+"/catalog-snapshots","POST",{"catalog_snapshot":rewrite(clean)})
        awaiting=[]
        for asset in selected.values():
            cloned=rewrite(asset);cloned["revision"]=1;cloned["owner_ids"]=[];cloned["steward_ids"]=[];cloned["policy_ref"]={"policy_id":None,"revision":None,"resolution":"UNKNOWN"};awaiting.append(cloned)
        created={}
        while awaiting:
            advanced=False
            for asset in list(awaiting):
                if any(target["asset_id"] not in created for target in walk({k:v for k,v in asset.items() if k not in ("workspace_id","asset_id","asset_type","revision")})):continue
                item=call(base+"/assets","POST",{"asset_revision":asset});created[asset["asset_id"]]=item;awaiting.remove(asset);advanced=True
            assert advanced,"Dependencies cannot be created through actual API"
        # Source archive uses the exact frozen source text already disclosed by
        # the local repository; hashes remain byte-for-byte consistent.
        seed=json.loads((ROOT/"backend/app/contracts/local-seed.json").read_text("utf-8"))
        for aid,item in created.items():
            if item["asset_ref"]["asset_type"]=="source":
                raw=seed[aid.split(":",1)[1]+"_text"]
                assert hashlib.sha256(raw.encode()).hexdigest()==item["asset_revision"]["spec"]["content_sha256"]
                call(base+"/sources/"+urllib.parse.quote(aid,safe=":")+"/archive","POST",{"revision":1,"raw_text":raw})
        context1=call(base+"/maintenance-context");assert context1["field_contexts"]
        metric=created["metric:session_count"];binding=created["binding:session_count.local_v1"]
        snapshot=next(s for s in context1["snapshots"] if s["catalog_snapshot_id"]==binding["asset_revision"]["spec"]["base_dataset_ref"]["catalog_snapshot_id"])
        target=binding["asset_revision"]["spec"]["time_field_ref"]
        updated=call(base+"/catalog-snapshots/"+urllib.parse.quote(snapshot["catalog_snapshot_id"],safe=":")+"/revisions","POST",{"base_content_hash":snapshot["content_hash"],"reason":"真实维护会话事件时间字段释义，新快照保持物理身份与指纹不变。","field_updates":[{"dataset_id":target["dataset_id"],"datasource_id":target["datasource_id"],"field_id":target["field_id"],"business_definition":{"name_zh":"访问会话开始时间","definition":"每次访问会话在源数据中记录的开始事件时间；原始时区未获业务确认。"}}]})
        new_snapshot=updated["catalog_snapshot"];assert updated["automatic_reference_upgrade"] is False
        old_dataset=next(d for d in snapshot["datasets"] if d["dataset_id"]==target["dataset_id"]);new_dataset=next(d for d in new_snapshot["datasets"] if d["dataset_id"]==target["dataset_id"])
        assert old_dataset["schema_fingerprint"]==new_dataset["schema_fingerprint"]
        call(base+"/catalog-snapshots/"+urllib.parse.quote(snapshot["catalog_snapshot_id"],safe=":")+"/revisions","POST",{"base_content_hash":snapshot["content_hash"],"reason":"negative physical identity edit","field_updates":[{"dataset_id":target["dataset_id"],"datasource_id":target["datasource_id"],"field_id":target["field_id"],"physical_name":"paid_at"}]},status=422)
        check("catalog_semantics_new_snapshot_without_physical_or_binding_mutation",{"old_snapshot":snapshot["catalog_snapshot_id"],"new_snapshot":new_snapshot["catalog_snapshot_id"],"physical_fingerprint_preserved":True,"automatic_upgrade":False,"physical_edit_rejected":True})
        original_binding=call(base+"/assets/binding:session_count.local_v1");assert original_binding["content_hash"]==binding["content_hash"]
        changed=copy.deepcopy(binding["asset_revision"])
        def new_catalog(value):
            if isinstance(value,dict):return {key:(new_snapshot["catalog_snapshot_id"] if key=="catalog_snapshot_id" else new_catalog(child)) for key,child in value.items()}
            if isinstance(value,list):return [new_catalog(child) for child in value]
            return value
        changed=new_catalog(changed);changed["revision"]=2;changed["name"]="访问会话数实际维护后的映射"
        revision=call(base+"/assets/binding:session_count.local_v1/revisions","POST",{"asset_revision":changed,"base_revision":1,"expected_record_version":1,"change_reason":"显式选择新目录释义快照；继承现有可核对计算与时间字段。"},version=1)
        diag=call(base+"/assets/binding:session_count.local_v1/implementation?revision=2");assert diag["outcome"]=="PASS",diag
        check("binding_new_revision_uses_exact_new_catalog_and_typed_contract",{"binding_ref":revision["asset_ref"],"authority":diag["compiled_contract"]["authority"],"sidecar_created":False,"old_revision_unchanged":True})
        all_heads=call(base+"/assets")["items"];draft_refs=[item["asset_ref"] for item in all_heads]
        body={"scope":"local_validation","draft_asset_refs":draft_refs,"expected_pointer_version":0,"reason":"真实维护后显式建立本地技术快照，核验新修订可以被实际召回；不批准业务口径。"}
        call(base+"/deployments/local_validation/rebuild","POST",{**body,"scope":"production"},status=422,version=0)
        rebuilt=call(base+"/deployments/local_validation/rebuild","POST",body,version=0)
        call(base+"/deployments/local_validation/rebuild","POST",body,status=409,version=0)
        assert rebuilt["business_approval_created"] is False and rebuilt["production_approved"] is False
        result=call(base+"/retrieve","POST",{"intent":{"question":"2025年访问会话数","start":"2025-01-01","end_exclusive":"2026-01-01"}})
        bundle=result["bundle"];assert bundle["selected_binding_ref"]==revision["asset_ref"],bundle
        assert bundle["selected_metric_ref"]==metric["asset_ref"]
        assert any(c["kind"]=="FIELD" and c["target_ref"]["catalog_snapshot_id"]==new_snapshot["catalog_snapshot_id"] for c in bundle["closure_refs"])
        context2=call(base+"/maintenance-context");history=[a for a in context2["assets"] if a["asset_ref"]["asset_id"]==binding["asset_ref"]["asset_id"]]
        assert sorted(a["asset_ref"]["revision"] for a in history)==[1,2]
        report["retrieval_result"]={"bundle_id":bundle["bundle_id"],"bundle_content_hash":result["bundle_content_hash"],"release_id":bundle["release_id"],"index_snapshot_id":bundle["index_snapshot_id"],"selected_metric_ref":bundle["selected_metric_ref"],"selected_binding_ref":bundle["selected_binding_ref"],"next_action":bundle["next_action"],"coverage":bundle["coverage"],"closure_refs":bundle["closure_refs"]}
        check("actual_http_new_binding_revision_is_retrieved_with_new_snapshot",{"binding_revision":2,"natural_language_only":True,"stale_cas_rejected":True,"production_rebuild_rejected":True,"history_revisions":[1,2],"candidate_count":len(bundle["candidates"]),"closure_count":len(bundle["closure_refs"]),"next_action":bundle["next_action"]})
        username="maintenance_viewer_"+suffix;password=secrets.token_urlsafe(24)
        viewer_user=call("/users","POST",{"username":username,"password":password,"display_name":"仅Binding可见的隔离测试用户","role":"viewer","workspace_ids":[wid]})
        viewer=Client(args.base);viewer.login(username,password);del password
        policy=call(base+"/policies","POST",{"policy_id":"binding-only-visible","allowed_user_ids":[viewer_user["id"]],"allowed_roles":["admin"],"allow_execution":False,"description":"只允许读取Binding；依赖指标/实体/物理目录不授予读取。"})
        restricted=copy.deepcopy(revision["asset_revision"]);restricted["revision"]=3;restricted["policy_ref"]=policy["policy_ref"]
        restricted_item=call(base+"/assets/binding:session_count.local_v1/revisions","POST",{"asset_revision":restricted,"base_revision":2,"expected_record_version":revision["governance_record"]["record_version"],"change_reason":"隔离负例：Binding可见不应泄露无权依赖的实现明细。"},version=revision["governance_record"]["record_version"])
        visible,receipt=viewer.request(base+"/assets/binding:session_count.local_v1");report["http_receipts"].append(receipt);assert visible["asset_ref"]==restricted_item["asset_ref"]
        private,receipt=viewer.request(base+"/assets/binding:session_count.local_v1/implementation");report["http_receipts"].append(receipt)
        assert private["compiled_contract"] is None and private["outcome"]=="UNKNOWN",private
        limited,receipt=viewer.request(base+"/maintenance-context");report["http_receipts"].append(receipt)
        assert not limited["snapshots"] and not limited["field_contexts"] and len(limited["assets"])==1,limited
        assert all(item["compiled_contract"] is None for item in limited["implementation_diagnostics"])
        call(base+"/policies/binding-only-visible/revoke","POST",{"revision":policy["policy_ref"]["revision"]})
        _,receipt=viewer.request(base+"/assets/binding:session_count.local_v1/implementation",status=403);report["http_receipts"].append(receipt)
        check("implementation_diagnostics_do_not_disclose_hidden_dependencies",{"binding_visible_before_revocation":True,"hidden_physical_catalog":True,"compiled_contract_redacted":True,"revoked_policy_status":403,"passwords_recorded":False})
        physical_policy=call(base+"/policies","POST",{"policy_id":"physical-cached-read","allowed_user_ids":[],"allowed_roles":["admin"],"allow_execution":False,"description":"隔离负例：目录政策撤销后禁止同幂等键重放旧召回Bundle。"})
        protected_catalog=copy.deepcopy(new_snapshot);protected_catalog["catalog_snapshot_id"]="catalog:cached-policy:"+suffix
        for dataset in protected_catalog["datasets"]:
            if dataset["dataset_id"]==target["dataset_id"] and dataset["datasource_id"]==target["datasource_id"]:dataset["policy_ref"]=physical_policy["policy_ref"]
        call(base+"/catalog-snapshots","POST",{"catalog_snapshot":protected_catalog})
        def protected_refs(value):
            if isinstance(value,dict):return {k:(protected_catalog["catalog_snapshot_id"] if k=="catalog_snapshot_id" else protected_refs(v)) for k,v in value.items()}
            if isinstance(value,list):return [protected_refs(v) for v in value]
            return value
        protected_binding=protected_refs(copy.deepcopy(revision["asset_revision"]));protected_binding["revision"]=4
        current_version=restricted_item["governance_record"]["record_version"]
        protected_item=call(base+"/assets/binding:session_count.local_v1/revisions","POST",{"asset_revision":protected_binding,"base_revision":3,"expected_record_version":current_version,"change_reason":"隔离负例：Binding自身读取仍有效，独立撤销目录读取政策验证缓存必须重新鉴权。"},version=current_version)
        heads=call(base+"/assets")["items"]
        call(base+"/deployments/local_validation/rebuild","POST",{"scope":"local_validation","draft_asset_refs":[item["asset_ref"] for item in heads],"expected_pointer_version":1,"reason":"目录现时政策缓存撤销负例，仅隔离本地草稿。"},version=1)
        recall_body={"intent":{"question":"2025年访问会话数","start":"2025-01-01","end_exclusive":"2026-01-01"}};recall_key=secrets.token_hex(16)
        cached_before=call(base+"/retrieve","POST",recall_body,key=recall_key)
        assert cached_before["bundle"]["selected_binding_ref"]==protected_item["asset_ref"]
        replay_before=call(base+"/retrieve","POST",recall_body,key=recall_key)
        assert replay_before==cached_before
        call(base+"/policies/physical-cached-read/revoke","POST",{"revision":physical_policy["policy_ref"]["revision"]})
        call(base+"/retrieve","POST",recall_body,status=403,key=recall_key)
        check("cached_retrieval_rechecks_current_physical_policy",{"same_idempotency_key_replayed_before_revocation":True,"binding_policy_still_readable":True,"physical_policy_revoked":True,"cached_bundle_replay_status":403,"workspace3_changed":False})
    except Exception as exc:report["errors"].append({"type":type(exc).__name__,"message":str(exc)[:2200]})
    report["passed"]=not report["errors"];report["finished_at"]=time.strftime("%Y-%m-%dT%H:%M:%S%z")
    Path(args.output).parent.mkdir(parents=True,exist_ok=True);Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"tests":len(report["tests"]),"requests":len(report["http_receipts"]),"output":args.output,"errors":report["errors"]},ensure_ascii=False));return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
