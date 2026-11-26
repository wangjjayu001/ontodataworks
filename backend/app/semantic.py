"""Semantic authority, governance and permission-scoped deterministic retrieval.

Technical validation snapshots are explicitly NOT production Releases. Production
publication fails closed until business ownership and policies are proven.
"""
import copy
import hashlib
import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select, func
from .db import Asset, CatalogMetadata, Datasource, Audit, User, Membership, now
from .security import identity, db_session, workspace_access
from .semantic_models import *

CONTRACT_DIR = Path(__file__).parent / "contracts"
AUTHORITY = json.loads((CONTRACT_DIR / "semantic-authority.schema.json").read_text("utf-8"))
RETRIEVAL = json.loads((CONTRACT_DIR / "semantic-retrieval.schema.json").read_text("utf-8"))
TYPES = ("domain", "term", "entity", "dimension", "relation", "metric", "binding", "source", "claim", "evidence")
router = APIRouter(prefix="/api/workspaces/{wid}/semantic", tags=["semantic-v1"])


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def validity_active(validity):
    current=datetime.now(timezone.utc)
    try:
        lower=datetime.fromisoformat(validity["from"].replace("Z","+00:00")) if validity.get("from") else None
        upper=datetime.fromisoformat(validity["to"].replace("Z","+00:00")) if validity.get("to") else None
        return (lower is None or lower<=current) and (upper is None or current<upper)
    except (ValueError,TypeError):return False


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex


def failure(code, message, status=400, **extra):
    raise HTTPException(status, {"code": code, "message": message, **extra})


def validate(value, definition, schema=AUTHORITY):
    root = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/" + definition}
    errors = sorted(Draft202012Validator(root, format_checker=FormatChecker()).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        failure("SCHEMA_INVALID", "对象不符合语义v1类型契约", 422,
                field_errors=[{"path": "/" + "/".join(map(str, e.path)), "message": e.message[:250]} for e in errors[:12]])


def ref(asset):
    return {k: asset[k] for k in ("workspace_id", "asset_id", "asset_type", "revision")}


def walk_refs(value, path=""):
    if isinstance(value, dict):
        if set(value) == {"workspace_id", "asset_id", "asset_type", "revision"}:
            yield path, "ASSET", value
            return
        if set(value) in ({"catalog_snapshot_id", "datasource_id", "dataset_id"}, {"catalog_snapshot_id", "datasource_id", "dataset_id", "field_id"}):
            yield path, "FIELD" if "field_id" in value else "DATASET", value
            return
        for key, val in value.items():
            yield from walk_refs(val, path + "/" + key)
    elif isinstance(value, list):
        for i, val in enumerate(value):
            yield from walk_refs(val, path + "/" + str(i))


def semantic_access(db, wid, user, write=False):
    workspace_access(db, user, wid)
    if not user.enabled:failure("BLOCKED_PERMISSION","身份已停用",403)
    if user.role=="admin":return
    assigned=db.scalar(select(SemanticAssignment.user_id).where(SemanticAssignment.workspace_id==wid,SemanticAssignment.user_id==user.id,SemanticAssignment.active==1).limit(1))
    policies=db.scalars(select(SemanticPolicy).where(SemanticPolicy.workspace_id==wid,SemanticPolicy.active==1)).all()
    if not assigned and not any(policy_allows(p,user) for p in policies):
        failure("BLOCKED_PERMISSION","没有语义资产职责或有效对象政策授权",403)


def assignment(db,wid,user,role,asset_id):
    return db.scalar(select(SemanticAssignment.user_id).where(SemanticAssignment.workspace_id==wid,SemanticAssignment.user_id==user.id,SemanticAssignment.role==role,SemanticAssignment.asset_id.in_(["*",asset_id]),SemanticAssignment.active==1).limit(1)) is not None


def policy_allows(policy,user):
    return bool(policy.active and (user.id in policy.content.get("allowed_user_ids",[]) or user.role in policy.content.get("allowed_roles",[])))


def active_policy(db,wid,pref):
    if not pref or pref.get("resolution")!="RESOLVED":return None
    policy=db.get(SemanticPolicy,(wid,pref.get("policy_id"),pref.get("revision")))
    return policy if policy and policy.active and sha(policy.content)==policy.content_hash else None


def can_read_asset(db,wid,user,asset):
    policy=active_policy(db,wid,asset["policy_ref"])
    if policy:return policy_allows(policy,user)
    # Unknown local technical drafts are only visible to administrators; an
    # explicit but revoked policy never falls back to administrator access.
    return asset["policy_ref"]["resolution"]=="UNKNOWN" and user.role=="admin"


def require_steward(db,wid,user,asset_id):
    if user.role!="admin" and not assignment(db,wid,user,"STEWARD",asset_id):
        failure("BLOCKED_PERMISSION","没有该资产的维护职责",403)


def owner_assignment_valid(db,wid,asset):
    if not asset["owner_ids"]:return False
    for raw in asset["owner_ids"]:
        try:uid=int(raw)
        except (ValueError,TypeError):return False
        owner=db.get(User,uid)
        if not owner or not owner.enabled or not assignment(db,wid,owner,"BUSINESS_OWNER",asset["asset_id"]):return False
        try:workspace_access(db,owner,wid)
        except HTTPException:return False
    return True


def resolve_asset(db, wid, target):
    if not target or target.get("workspace_id") != wid:
        failure("REFERENCE_INVALID", "语义引用不可用", 404)
    row = db.get(SemanticRevision, (wid, target.get("asset_id"), target.get("revision")))
    if not row or row.asset_type != target.get("asset_type") or sha(row.content) != row.content_hash:
        failure("REFERENCE_INVALID", "语义引用或内容校验不可用", 404)
    return copy.deepcopy(row.content)


def resolve_binding(db, wid, target):
    asset = resolve_asset(db, wid, target)
    if asset["asset_type"] != "binding":
        failure("REFERENCE_INVALID", "引用类型不是Binding")
    return asset


def get_execution_contract(db, wid, binding_ref):
    binding = resolve_binding(db, wid, binding_ref)
    gov=db.get(SemanticGovernance,(wid,binding["asset_id"]))
    if gov and gov.revision==binding["revision"] and gov.workflow_state in ("WITHDRAWN","REJECTED"):
        failure("BINDING_NOT_EXECUTABLE","当前Binding修订已撤回或退回")
    from .semantic_compiler import derive_contract
    return derive_contract(db, wid, binding)


def resolve_physical(db, wid, target, snapshot_cache=None):
    key=(wid,target["catalog_snapshot_id"])
    row=snapshot_cache.get(key) if snapshot_cache is not None else None
    if row is None:
        row = db.get(SemanticSnapshot, target["catalog_snapshot_id"])
        if not row or row.workspace_id != wid or sha(row.content) != row.content_hash:
            failure("REFERENCE_INVALID", "目录快照不可用")
        if snapshot_cache is not None:snapshot_cache[key]=row
    dataset = next((d for d in row.content["datasets"] if d["dataset_id"] == target["dataset_id"] and d["datasource_id"] == target["datasource_id"]), None)
    if not dataset:
        failure("REFERENCE_INVALID", "目录数据集引用不可用")
    if "field_id" in target:
        field = next((f for f in dataset["fields"] if f["field_id"] == target["field_id"]), None)
        if not field:
            failure("REFERENCE_INVALID", "字段不属于指定数据集和快照")
        return field
    return dataset


def can_read_physical(db,wid,user,target,scope="local_validation",snapshot_cache=None):
    try:
        dataset=resolve_physical(db,wid,{k:v for k,v in target.items() if k!="field_id"},snapshot_cache)
        policies=[dataset["policy_ref"]]
        if "field_id" in target:
            field=resolve_physical(db,wid,target,snapshot_cache)
            policies.append(field["business_definition"]["policy_ref"])
        for pref in policies:
            policy=active_policy(db,wid,pref)
            if policy:
                if not policy_allows(policy,user):return False
            elif not (pref["resolution"]=="UNKNOWN" and scope=="local_validation" and user.role=="admin"):
                return False
        return True
    except HTTPException:return False


def check_bundle_access(db,wid,user,bundle):
    """Current revocation overrides a pinned bundle. Called again at every gate."""
    semantic_access(db,wid,user)
    if bundle.get("workspace_id")!=wid:failure("BLOCKED_PERMISSION","运行工作空间不一致",403)
    release=db.get(SemanticRelease,bundle.get("release_id"));index=db.get(SemanticIndex,bundle.get("index_snapshot_id"))
    if not release or not index or release.workspace_id!=wid or index.workspace_id!=wid or release.content_hash!=bundle.get("release_hash") or index.content_hash!=bundle.get("index_snapshot_hash") or index.release_id!=release.id or index.release_hash!=release.content_hash or sha(release.content)!=release.content_hash or sha(index.content)!=index.content_hash:failure("INDEX_RELEASE_MISMATCH","固定发布和索引已不可验证",409)
    if release.scope=="local_validation" and user.role!="admin":failure("BLOCKED_PERMISSION","本地技术验证权限已失效",403)
    snapshot_cache={}
    for candidate in bundle.get("candidates",[]):
        target=candidate["target_ref"]
        if "asset_id" in target:
            candidate_asset=resolve_asset(db,wid,target)
            if not can_read_asset(db,wid,user,candidate_asset):failure("BLOCKED_PERMISSION","历史候选含当前无权读取的内容",403)
            if not validity_active(candidate_asset["validity"]):failure("SOURCE_STALE","历史候选已超业务有效期",409)
        elif not can_read_physical(db,wid,user,target,release.scope,snapshot_cache):failure("BLOCKED_PERMISSION","历史目录候选的现时政策已失效",403)
    targets=[t for t in [bundle.get("selected_metric_ref"),bundle.get("selected_binding_ref")]+bundle.get("requested_dimension_refs",[]) if t]
    targets += [d["target_ref"] for d in bundle.get("closure_refs",[])]
    for target in targets:
        if "asset_id" in target:
            asset=resolve_asset(db,wid,target)
            if not can_read_asset(db,wid,user,asset) or (release.scope=="production" and not active_policy(db,wid,asset["policy_ref"])):failure("BLOCKED_PERMISSION","必要语义依赖的现时政策已失效",403)
            if not validity_active(asset["validity"]):failure("SOURCE_STALE","必要依赖已超业务有效期",409)
            gov=db.get(SemanticGovernance,(wid,asset["asset_id"]))
            if gov and gov.revision==asset["revision"] and gov.workflow_state in ("WITHDRAWN","REJECTED"):failure("APPROVAL_STALE","必要依赖已撤回或退回",409)
            if release.scope=="production":
                required={"BUSINESS_OWNER","DATA_OWNER"} if asset["asset_type"] in ("binding","relation") else {"BUSINESS_OWNER"}
                if not required.issubset({a["reviewer_role"] for a in valid_approvals(db,wid,asset)}):failure("APPROVAL_STALE","必要依赖审批或职责分派已失效",409)
            if asset["asset_type"]=="evidence" and not verify_archived_evidence(db,wid,asset):failure("SOURCE_STALE","证据原文回源已失效",409)
        elif not can_read_physical(db,wid,user,target,release.scope,snapshot_cache):failure("BLOCKED_PERMISSION","物理数据依赖的现时政策已失效",403)
    return None


def reference_issues(db, wid, asset, available=None):
    issues = []
    def exact_asset(target):
        candidate=(available or {}).get((target["asset_id"],target["revision"]))
        return candidate if candidate is not None else resolve_asset(db,wid,target)
    def logical_properties(value,path=""):
        if isinstance(value,dict):
            if set(value)=={"entity_ref","property_id"}:
                try:
                    entity=exact_asset(value["entity_ref"])
                    if entity["asset_type"]!="entity" or value["property_id"] not in {p["property_id"] for p in entity["spec"]["properties"]}:
                        issues.append("PROPERTY_REFERENCE_INVALID:"+path)
                except (HTTPException,KeyError):issues.append("PROPERTY_REFERENCE_INVALID:"+path)
            for key,child in value.items():logical_properties(child,path+"/"+key)
        elif isinstance(value,list):
            for index,child in enumerate(value):logical_properties(child,path+"/"+str(index))
    logical_properties(asset)
    for path, kind, target in walk_refs({k:v for k,v in asset.items() if k not in ("workspace_id", "asset_id", "asset_type", "revision")}):
        try:
            if kind == "ASSET":
                if target["workspace_id"] != wid:
                    raise ValueError("cross workspace")
                if available is not None and (target["asset_id"], target["revision"]) in available:
                    found = available[(target["asset_id"], target["revision"])]
                    if found["asset_type"] != target["asset_type"]:
                        raise ValueError("wrong type")
                else:
                    resolve_asset(db, wid, target)
            else:
                resolve_physical(db, wid, target)
        except (HTTPException, ValueError, KeyError):
            issues.append("REFERENCE_INVALID:" + path)
    if asset["asset_type"] == "dimension":
        try:
            entity = exact_asset(asset["spec"]["entity_ref"])
            if not entity or asset["spec"]["property_id"] not in {p["property_id"] for p in entity["spec"]["properties"]}:
                issues.append("PROPERTY_REFERENCE_INVALID")
        except HTTPException:
            issues.append("PROPERTY_REFERENCE_INVALID")
    if asset["asset_type"] == "metric" and any(t["asset_id"] == asset["asset_id"] for t in asset["spec"]["depends_on_metric_refs"]):
        issues.append("METRIC_DEPENDENCY_CYCLE")
    cycle_slot={"metric":"depends_on_metric_refs","dimension":"parent_dimension_refs","domain":"parent_ref"}.get(asset["asset_type"])
    if cycle_slot:
        initial=asset["spec"].get(cycle_slot)
        pending=list(initial) if isinstance(initial,list) else [initial] if initial else []
        seen=set()
        while pending:
            target=pending.pop()
            if target["asset_id"]==asset["asset_id"]:
                issues.append("SEMANTIC_DEPENDENCY_CYCLE");break
            key=(target["asset_id"],target["revision"])
            if key in seen:continue
            seen.add(key)
            try:
                dependency=available.get(key) if available is not None else resolve_asset(db,wid,target)
                if dependency:
                    more=dependency["spec"].get(cycle_slot)
                    pending.extend(more if isinstance(more,list) else [more] if more else [])
            except HTTPException:pass
    if asset["asset_type"] == "binding":
        spec = asset["spec"]
        join_ids = {j["join_id"] for j in spec["joins"]}
        if not set(spec["base_join_ids"]).issubset(join_ids) or any(not set(i["required_join_ids"]).issubset(join_ids) for i in spec["dimension_implementations"]):
            issues.append("JOIN_REFERENCE_INVALID")
        if len(join_ids) != len(spec["joins"]):
            issues.append("DUPLICATE_JOIN_ID")
        mapped=[canonical(item["property_ref"]) for item in spec["property_mappings"]]
        if len(mapped)!=len(set(mapped)):issues.append("DUPLICATE_PROPERTY_MAPPING")
        implemented=[canonical(item["dimension_ref"]) for item in spec["dimension_implementations"]]
        if len(implemented)!=len(set(implemented)):issues.append("DUPLICATE_DIMENSION_IMPLEMENTATION")
        try:
            metric=exact_asset(spec["metric_ref"])
            if not set(implemented).issubset({canonical(item) for item in metric["spec"]["allowed_dimension_refs"]}):issues.append("DIMENSION_NOT_ALLOWED_BY_METRIC")
        except (HTTPException,KeyError):pass
    if asset["asset_type"] == "entity":
        properties=[p["property_id"] for p in asset["spec"]["properties"]]
        if len(properties)!=len(set(properties)):issues.append("DUPLICATE_PROPERTY_ID")
        if not set(asset["spec"]["business_key"]).issubset(properties):issues.append("BUSINESS_KEY_PROPERTY_INVALID")
    if asset["asset_type"] == "relation":
        spec=asset["spec"]
        for pair in spec["logical_key_pairs"]:
            if pair["from"]["entity_ref"]!=spec["from_entity_ref"] or pair["to"]["entity_ref"]!=spec["to_entity_ref"]:
                issues.append("RELATION_KEY_ENTITY_MISMATCH")
    return sorted(set(issues))


def dependency_hash(asset):
    return sha([{"slot":p,"kind":k,"target":t} for p,k,t in walk_refs(asset) if p])


def governance_json(row, asset):
    return {"asset_ref":ref(asset), "content_hash":row.content_hash, "dependency_hash":row.dependency_hash,
            "workflow_state":row.workflow_state, "validation_state":row.validation_state,
            "record_version":row.record_version, "issue_codes":row.issue_codes}


def asset_item(db, row):
    gov = db.get(SemanticGovernance, (row.workspace_id,row.asset_id))
    result = {"asset_revision":row.content,"asset_ref":ref(row.content),"content_hash":row.content_hash,
              "governance_record":governance_json(gov,row.content) if gov and gov.revision == row.revision else None}
    return result


def record_event(db,wid,user,kind,content):
    db.add(SemanticEvent(id=new_id("event"),workspace_id=wid,event_type=kind,content=content))
    db.add(Audit(workspace_id=wid,actor=user.username,action="semantic."+kind,detail=canonical({"target":content.get("asset_id"),"scope":content.get("scope")})))


def save_asset(db,wid,user,asset,base_revision=None,expected_record_version=None,available=None):
    validate(asset,"asset_revision")
    if asset["workspace_id"] != wid:
        failure("REFERENCE_INVALID","工作空间不一致")
    require_steward(db,wid,user,asset["asset_id"])
    gov = db.scalar(select(SemanticGovernance).where(SemanticGovernance.workspace_id==wid,SemanticGovernance.asset_id==asset["asset_id"]).with_for_update())
    if gov:
        if base_revision != gov.revision or expected_record_version != gov.record_version:
            failure("REVISION_CONFLICT","资产已被其他操作修改",409,record_version=gov.record_version,revision=gov.revision)
        old = resolve_asset(db,wid,{"workspace_id":wid,"asset_id":gov.asset_id,"asset_type":asset["asset_type"],"revision":gov.revision})
        if old["asset_type"] != asset["asset_type"]:
            failure("REFERENCE_INVALID","资产类型不能修改")
        expected_revision=gov.revision+1
    else:
        if base_revision is not None:
            failure("REFERENCE_INVALID","待编辑资产不存在",404)
        expected_revision=1
    asset = copy.deepcopy(asset)
    asset["revision"]=expected_revision
    issues=reference_issues(db,wid,asset,available)
    if issues:
        failure("REFERENCE_INVALID","依赖引用不完整",422,asset_id=asset["asset_id"],issue_codes=issues)
    row=SemanticRevision(workspace_id=wid,asset_id=asset["asset_id"],revision=expected_revision,asset_type=asset["asset_type"],content=asset,content_hash=sha(asset),actor=user.username)
    db.add(row);db.flush()
    if not gov:
        gov=SemanticGovernance(workspace_id=wid,asset_id=asset["asset_id"],revision=expected_revision,content_hash=row.content_hash,dependency_hash=dependency_hash(asset),record_version=1)
        db.add(gov)
    else:
        gov.revision=expected_revision;gov.content_hash=row.content_hash;gov.dependency_hash=dependency_hash(asset)
        gov.record_version+=1
    gov.workflow_state="DRAFT";gov.validation_state="NOT_RUN";gov.issue_codes=[]
    for path,kind,target in walk_refs(asset):
        if path:
            db.add(SemanticReference(workspace_id=wid,asset_id=asset["asset_id"],revision=expected_revision,slot=path,target={"kind":kind,"target_ref":target}))
    record_event(db,wid,user,"revision.created",{"asset_id":asset["asset_id"],"revision":expected_revision})
    db.flush()
    return asset_item(db,row)


def verify_asset(db,wid,user,asset):
    issues=reference_issues(db,wid,asset)
    checks=[("TYPE_AND_REFERENCES","FAIL" if issues else "PASS",",".join(issues) or "类型与固定引用校验通过")]
    owner_ok=owner_assignment_valid(db,wid,asset)
    policy=active_policy(db,wid,asset["policy_ref"])
    checks += [("BUSINESS_OWNERSHIP","PASS" if owner_ok else "UNKNOWN","真实Owner身份/分派核验通过" if owner_ok else "需要已登记的真实Owner身份和职责分派"),
               ("POLICY","PASS" if policy else "UNKNOWN","当前对象访问政策已解析" if policy else "生产对象政策未配置或已撤销")]
    if asset["asset_type"]=="source":
        source_ok=verify_archived_source(db,wid,asset)
        checks.append(("SOURCE_CONTENT","PASS" if source_ok else "UNKNOWN","已归档原文与来源hash一致" if source_ok else "来源原文尚未归档核验"))
        checks.append(("BUSINESS_PROVENANCE","PASS" if asset["spec"]["source_kind"]=="BUSINESS_SPEC" else "UNKNOWN","来源为业务规范" if asset["spec"]["source_kind"]=="BUSINESS_SPEC" else "技术材料不代替业务证据"))
    elif asset["asset_type"] not in ("evidence","claim"):
        business_source=False
        for target in asset["provenance_refs"]:
            try:
                source=resolve_asset(db,wid,target)
                if source["asset_type"]=="source" and source["spec"]["source_kind"]=="BUSINESS_SPEC" and verify_archived_source(db,wid,source):business_source=True
                if source["asset_type"]=="evidence" and source["spec"]["evidence_kind"]=="BUSINESS_TEXT" and verify_archived_evidence(db,wid,source):business_source=True
            except HTTPException:pass
        checks.append(("BUSINESS_PROVENANCE","PASS" if business_source else "UNKNOWN","业务原文可定位" if business_source else "缺少可回查的业务定义证据"))
    if asset["asset_type"]=="evidence":
        spec=asset["spec"]
        fragment_ok=hashlib.sha256(spec["excerpt"].encode()).hexdigest()==spec["fragment_sha256"]
        checks.append(("EVIDENCE_FRAGMENT","PASS" if fragment_ok else "FAIL","原文片段hash"))
        source_ok=verify_archived_evidence(db,wid,asset)
        checks.append(("EVIDENCE_SOURCE","PASS" if source_ok else "FAIL","准确来源版本、JSON定位和原文一致性"))
    if asset["asset_type"]=="metric":
        spec=asset["spec"]
        unit_ok=spec["unit"]["authority"]=="CONFIRMED" and spec["unit"]["kind"]!="UNKNOWN" and (spec["unit"]["kind"]!="CURRENCY" or bool(spec["unit"]["currency"]))
        time_ok=spec["time_policy"]["kind"]!="UNKNOWN" and spec["time_policy"]["history_policy"]!="UNKNOWN"
        checks += [("METRIC_UNIT","PASS" if unit_ok else "UNKNOWN","单位/币种必须有权威确认"),("METRIC_TIME","PASS" if time_ok else "UNKNOWN","时间事件与历史策略必须明确")]
    if asset["asset_type"]=="dimension":
        known=asset["spec"]["history_policy"]!="UNKNOWN" and asset["spec"]["null_member"]!="UNKNOWN"
        checks.append(("DIMENSION_SEMANTICS","PASS" if known else "UNKNOWN","历史归属和空成员规则必须明确"))
    if asset["asset_type"]=="binding":
        spec=asset["spec"];coverage_ok=all(spec["coverage"].get(k) for k in ("start","end_exclusive","data_as_of"))
        join_ok=all(j["null_key_policy"]!="UNKNOWN" and j["equivalence_evidence_refs"] for j in spec["joins"])
        checks += [("BINDING_COVERAGE","PASS" if coverage_ok else "UNKNOWN","数据覆盖与截止必须明确"),("JOIN_EQUIVALENCE","PASS" if join_ok else "UNKNOWN","JOIN等价证据和空键策略必须明确；物理核验仍由数据Owner审核")]
        diagnostic=implementation_diagnostic(db,wid,asset,user)
        checks.extend((check["rule_id"],check["outcome"],check["reason"]) for check in diagnostic["checks"])
    if asset["asset_type"]=="claim":
        links=asset["spec"]["evidence_links"]
        conflict=any(link["stance"]=="REFUTE" for link in links)
        checks.append(("CLAIM_CONFLICT","UNKNOWN" if conflict else "PASS","存在反驳证据，需显式裁决并修订主张" if conflict else "无未裁决反驳链接"))
    checks.append(("VALIDITY","PASS" if validity_active(asset["validity"]) else "FAIL","修订业务有效期核对"))
    receipts=[]
    for rule,outcome,reason in checks:
        receipt={"id":new_id("verify"),"workspace_id":wid,"target_ref":ref(asset),"target_hash":sha(asset),"rule_id":rule,"rule_revision":"1.0.0","outcome":outcome,"reason":reason,"evidence_refs":[],"catalog_snapshot_ids":sorted({t["catalog_snapshot_id"] for _,k,t in walk_refs(asset) if k!="ASSET"}),"observed_at":stamp(),"expires_at":None,"coverage_description":"本地技术检查，不代替业务批准或生产政策"}
        validate(receipt,"verification_receipt")
        db.add(SemanticVerification(id=receipt["id"],workspace_id=wid,asset_id=asset["asset_id"],revision=asset["revision"],content=receipt,content_hash=sha(receipt)))
        receipts.append(receipt)
    gov=db.get(SemanticGovernance,(wid,asset["asset_id"]))
    gov.validation_state="FAIL" if any(r["outcome"]=="FAIL" for r in receipts) else "UNKNOWN" if any(r["outcome"]=="UNKNOWN" for r in receipts) else "PASS"
    gov.issue_codes=issues+[r["rule_id"]+"_REQUIRED" for r in receipts if r["outcome"]!="PASS"]
    gov.record_version+=1
    record_event(db,wid,user,"verification.completed",{"asset_id":asset["asset_id"],"outcome":gov.validation_state})
    return receipts


def implementation_diagnostic(db,wid,asset,user=None):
    """Pure typed-contract diagnostics; no warehouse access or approval."""
    if asset["asset_type"]!="binding":failure("REFERENCE_INVALID","实现诊断需要Binding修订",422)
    if user is not None:
        pending=[asset];seen=set();cache={}
        while pending:
            dependency=pending.pop();key=canonical(ref(dependency))
            if key in seen:continue
            seen.add(key)
            if not can_read_asset(db,wid,user,dependency):
                return {"binding_ref":ref(asset),"checks":[{"rule_id":"IMPLEMENTATION_PERMISSION","outcome":"UNKNOWN","reason":"必要依赖的现时读取政策不可用；实现明细不返回"}],"outcome":"UNKNOWN","compiled_contract":None,"live_source_checked":False,"business_approved":False}
            for _,kind,target in walk_refs(dependency):
                if kind=="ASSET":pending.append(resolve_asset(db,wid,target))
                elif not can_read_physical(db,wid,user,target,"local_validation",cache):
                    return {"binding_ref":ref(asset),"checks":[{"rule_id":"IMPLEMENTATION_PERMISSION","outcome":"UNKNOWN","reason":"必要物理目录的现时读取政策不可用；实现明细不返回"}],"outcome":"UNKNOWN","compiled_contract":None,"live_source_checked":False,"business_approved":False}
    from .semantic_compiler import derive_contract
    try:
        contract=derive_contract(db,wid,asset)
        supported=contract["result_kind"]!="UNSUPPORTED" and not contract["unsupported_filters"]
        checks=[{"rule_id":"LOGICAL_PHYSICAL_MAPPING","outcome":"PASS","reason":"指标逻辑计算经确切属性映射后与Binding物理计算一致；业务时间映射明确"},
            {"rule_id":"LOCAL_EXECUTOR_CAPABILITY","outcome":"PASS" if supported else "UNKNOWN","reason":"当前本地执行器支持该计算" if supported else "结构可编译，但当前本地执行器未支持该单位/筛选；不代表业务定义错误"}]
        return {"binding_ref":ref(asset),"checks":checks,"outcome":"PASS" if supported else "UNKNOWN","compiled_contract":contract,"live_source_checked":False,"business_approved":False}
    except HTTPException as exc:
        detail=exc.detail if isinstance(exc.detail,dict) else {"message":str(exc.detail)}
        reason=detail.get("message","类型映射不能编译")
        incompatible=any(text in reason for text in ("不一致","缺少对应","显式且一致","不能存在多个","无效 JOIN","必须归属","不存在","事件冲突"))
        return {"binding_ref":ref(asset),"checks":[{"rule_id":"LOGICAL_PHYSICAL_MAPPING" if incompatible else "LOCAL_EXECUTOR_CAPABILITY","outcome":"FAIL" if incompatible else "UNKNOWN","reason":reason}],"outcome":"FAIL" if incompatible else "UNKNOWN","compiled_contract":None,"live_source_checked":False,"business_approved":False}


def verify_archived_source(db,wid,source):
    spec=source["spec"]
    blob=db.get(SemanticSourceBlob,(wid,source["asset_id"],spec["content_sha256"]))
    return bool(blob and hashlib.sha256(blob.content.get("raw_text","").encode()).hexdigest()==spec["content_sha256"])


def verify_archived_evidence(db,wid,asset):
    try:
        spec=asset["spec"]
        source=resolve_asset(db,wid,spec["source_ref"])
        digest=source["spec"]["content_sha256"]
        blob=db.get(SemanticSourceBlob,(wid,source["asset_id"],digest))
        if not blob or hashlib.sha256(blob.content["raw_text"].encode()).hexdigest()!=digest:return False
        value=json.loads(blob.content["raw_text"])
        pointer=spec["locator"].get("pointer")
        if spec["locator"]["kind"]!="JSON_POINTER" or not isinstance(pointer,str):return False
        for part in pointer.lstrip("/").split("/"):
            key=part.replace("~1","/").replace("~0","~")
            value=value[int(key)] if isinstance(value,list) else value[key]
        return canonical(value)==spec["excerpt"] and hashlib.sha256(spec["excerpt"].encode()).hexdigest()==spec["fragment_sha256"]
    except (HTTPException,KeyError,ValueError,TypeError,IndexError):return False


def current_assets(db,wid):
    return db.scalars(select(SemanticRevision).join(SemanticGovernance,(SemanticGovernance.workspace_id==SemanticRevision.workspace_id)&(SemanticGovernance.asset_id==SemanticRevision.asset_id)&(SemanticGovernance.revision==SemanticRevision.revision)).where(SemanticRevision.workspace_id==wid).order_by(SemanticRevision.asset_type,SemanticRevision.asset_id)).all()


def get_deployment(db,wid,user,environment="local_validation"):
    semantic_access(db,wid,user)
    if environment=="local_validation" and user.role!="admin":failure("BLOCKED_PERMISSION","本地技术验证部署仅对管理员开放",403)
    row=db.get(SemanticDeployment,(wid,environment))
    if not row:return None
    release=db.get(SemanticRelease,row.release_id);index=db.get(SemanticIndex,row.index_snapshot_id)
    if not release or not index or release.workspace_id!=wid or index.workspace_id!=wid or release.scope!=environment or sha(release.content)!=row.release_hash or sha(index.content)!=row.index_snapshot_hash or index.release_hash!=row.release_hash:
        failure("INDEX_RELEASE_MISMATCH","发布与索引校验失败",409)
    return {"workspace_id":wid,"scope":environment,"manifest_kind":release.content.get("kind","Release"),"release_id":row.release_id,"release_hash":row.release_hash,"index_snapshot_id":row.index_snapshot_id,"index_snapshot_hash":row.index_snapshot_hash,"release_ref":{"id":row.release_id,"content_hash":row.release_hash},"index_snapshot_ref":{"id":row.index_snapshot_id,"content_hash":row.index_snapshot_hash},"pointer_version":row.pointer_version,"activated_at":row.activated_at.isoformat(),"actor_id":str(row.actor_id),"production_approved":environment=="production"}


def create_release(db,wid,user,body):
    scope=body.get("scope","production")
    if scope not in ("local_validation","production"):failure("SCHEMA_INVALID","不支持的发布范围")
    if scope=="local_validation" and user.role!="admin":failure("BLOCKED_PERMISSION","本地技术验证发布仅对管理员开放",403)
    if scope=="production" and not assignment(db,wid,user,"PUBLISHER","*"):failure("BLOCKED_PERMISSION","需要明确的发布职责分派",403)
    rows=current_assets(db,wid)
    wanted=body.get("asset_refs")
    if wanted:
        rows=[db.get(SemanticRevision,(wid,r["asset_id"],r["revision"])) for r in wanted if r.get("workspace_id")==wid]
        if not rows or any(r is None for r in rows):failure("REFERENCE_INVALID","发布成员不可用")
    by_id={(r.asset_id,r.revision):r for r in rows}
    revision_by_id={}
    # A local technical manifest must still close over every exact asset dependency.
    pending=list(rows)
    while pending:
        row=pending.pop()
        if row.asset_id in revision_by_id and revision_by_id[row.asset_id]!=row.revision:
            failure("REVISION_CONFLICT","同一技术快照不能同时包含同资产的多个修订；请更新依赖引用",409,asset_id=row.asset_id)
        revision_by_id[row.asset_id]=row.revision
        for _,kind,target in walk_refs(row.content):
            if kind=="ASSET" and target!=ref(row.content) and (target["asset_id"],target["revision"]) not in by_id:
                resolved=resolve_asset(db,wid,target)
                dep=db.get(SemanticRevision,(wid,resolved["asset_id"],resolved["revision"]))
                by_id[(dep.asset_id,dep.revision)]=dep;pending.append(dep)
    if not by_id:failure("RELEASE_NOT_READY","没有可发布的技术验证资产",409)
    snapshots=db.scalars(select(SemanticSnapshot).where(SemanticSnapshot.workspace_id==wid)).all()
    for snapshot in snapshots:
        if sha(snapshot.content)!=snapshot.content_hash:failure("REFERENCE_INVALID","目录快照内容hash不一致",409)
        validate(snapshot.content,"catalog_snapshot")
    items=[{"asset_ref":ref(r.content),"content_hash":r.content_hash} for r in sorted(by_id.values(),key=lambda r:(r.asset_id,r.revision))]
    if scope=="production":
        approvals=[];verifications=[]
        for row in by_id.values():
            a=row.content;g=db.get(SemanticGovernance,(wid,row.asset_id))
            from .db import SchemaVersion
            if db.get(SchemaVersion,5):
                from .pipeline_catalog import batch_gate
                for _,kind,target in walk_refs(a):
                    if kind not in {"FIELD","DATASET"}:continue
                    dataset=resolve_physical(db,wid,{k:v for k,v in target.items() if k!="field_id"})
                    if dataset.get("connector_kind")!="POSTGRESQL":continue
                    physical=db.scalar(select(Asset).where(Asset.workspace_id==wid,Asset.datasource_id==int(dataset["datasource_id"]),Asset.namespace==dataset["namespace"],Asset.table_name==dataset["physical_name"]))
                    if physical:batch_gate(db,wid,physical)
            if not g or g.revision!=row.revision or g.workflow_state!="APPROVED" or g.validation_state!="PASS" or g.content_hash!=row.content_hash or g.dependency_hash!=dependency_hash(a):failure("APPROVAL_REQUIRED","发布成员必须是核验和审批均有效的确切修订",409)
            if not can_read_asset(db,wid,user,a) or not active_policy(db,wid,a["policy_ref"]):failure("POLICY_UNAVAILABLE","发布成员的现时政策不可用",403)
            if not validity_active(a["validity"]):failure("SOURCE_STALE","发布成员不在业务有效期",409)
            required={"BUSINESS_OWNER","DATA_OWNER"} if row.asset_type in ("binding","relation") else {"BUSINESS_OWNER"}
            valid=valid_approvals(db,wid,a)
            if not required.issubset({v["reviewer_role"] for v in valid}):failure("APPROVAL_REQUIRED","发布成员缺少有效职责审批",409)
            approvals.extend({"id":v["id"],"content_hash":sha(v)} for v in valid)
            receipts=db.scalars(select(SemanticVerification).where(SemanticVerification.workspace_id==wid,SemanticVerification.asset_id==row.asset_id,SemanticVerification.revision==row.revision)).all()
            verifications.extend({"id":v.id,"content_hash":v.content_hash} for v in receipts if v.content["target_hash"]==row.content_hash and v.content["outcome"]=="PASS")
        if not snapshots:failure("RELEASE_NOT_READY","正式发布需要确切目录快照",409)
        content={"id":new_id("release"),"workspace_id":wid,"schema_version":"semantic-contract/1.0.0","items":items,"catalog_snapshots":[{"id":s.id,"content_hash":s.content_hash} for s in snapshots],"approval_refs":list({x["id"]:x for x in approvals}.values()),"verification_refs":list({x["id"]:x for x in verifications}.values()),"validity":{"from":None,"to":None},"created_at":stamp()}
        validate(content,"release")
    else:
        content={"id":new_id("local_manifest"),"kind":"TechnicalSnapshotManifest","schema_version":"local-semantic-validation/1.0.0","workspace_id":wid,"scope":"local_validation","items":items,"catalog_snapshots":[{"id":s.id,"content_hash":s.content_hash} for s in snapshots],"production_approval":False,"limitations":["synthetic source data","business approval absent","production object/row/column policy absent"],"created_at":stamp()}
    row=SemanticRelease(id=content["id"],workspace_id=wid,scope=scope,content=content,content_hash=sha(content));db.add(row)
    record_event(db,wid,user,"technical_manifest.created",{"scope":scope,"release_id":row.id})
    return {"release":content,"release_ref":{"id":row.id,"content_hash":row.content_hash},"scope":scope,"production_approved":scope=="production"}


def valid_approvals(db,wid,asset):
    approvals=db.scalars(select(SemanticApproval).where(SemanticApproval.workspace_id==wid,SemanticApproval.asset_id==asset["asset_id"],SemanticApproval.revision==asset["revision"]).order_by(SemanticApproval.created_at.desc())).all()
    seen=set();valid=[]
    for row in approvals:
        a=row.content;key=(a["reviewer_id"],a["reviewer_role"])
        if key in seen:continue
        seen.add(key)
        try:reviewer=db.get(User,int(a["reviewer_id"]))
        except ValueError:continue
        if a["decision"]=="APPROVE" and reviewer and reviewer.enabled and assignment(db,wid,reviewer,a["reviewer_role"],asset["asset_id"]) and a["content_hash"]==sha(asset) and a["dependency_hash"]==dependency_hash(asset):valid.append(a)
    return valid


def tokens(text):
    value=text.lower()
    out=re.findall(r"[a-z0-9_]+",value)
    for chunk in re.findall(r"[\u3400-\u9fff]+",value):
        out.extend(chunk[i:i+2] for i in range(max(1,len(chunk)-1)))
    return out


def build_index(db,wid,user,release_ref):
    release=db.get(SemanticRelease,release_ref.get("id"))
    if not release or release.workspace_id!=wid or release.content_hash!=release_ref.get("content_hash") or sha(release.content)!=release.content_hash:failure("REFERENCE_INVALID","发布引用不可用")
    if release.scope=="local_validation" and user.role!="admin":failure("BLOCKED_PERMISSION","本地技术索引仅管理员可构建",403)
    if release.scope=="production" and not assignment(db,wid,user,"PUBLISHER","*"):failure("BLOCKED_PERMISSION","需要发布职责才能构建正式索引",403)
    docs=[]
    for item in release.content["items"]:
        a=resolve_asset(db,wid,item["asset_ref"])
        if sha(a)!=item["content_hash"]:failure("REFERENCE_INVALID","发布内容hash不一致")
        search={"name":a["name"],"definition":a["definition"],"aliases":[v["text"] for v in a["aliases"]],"grain":a["spec"].get("grain",{}).get("description") if isinstance(a["spec"].get("grain"),dict) else a["spec"].get("grain"),"time_kind":a["spec"].get("time_policy",{}).get("kind"),"unit_code":a["spec"].get("unit",{}).get("code")}
        deps=[]
        for path,kind,target in walk_refs(a):
            if path:deps.append({"kind":kind,"target_ref":target,"binding_scope_ref":ref(a) if a["asset_type"]=="binding" else None,"dimension_scope_ref":None})
        doc={"document_id":"doc:"+a["asset_id"]+":"+str(a["revision"]),"workspace_id":wid,"release_id":release.id,"release_hash":release.content_hash,"target_ref":ref(a),"role":a["asset_type"],"projection_version":"semantic-search/1.0.0","search_fields":search,"filters":{"domain_ids":[a["domain_ref"]["asset_id"]] if a["domain_ref"] else [],"entity_ids":[],"binding_ids":[a["asset_id"]] if a["asset_type"]=="binding" else [],"policy_ref":a["policy_ref"]},"dependency_refs":deps}
        validate(doc,"search_document",RETRIEVAL);docs.append(doc)
    # Physical dataset/field identities remain Catalog refs, never fake semantic
    # AssetRefs. Index them under their own roles for metadata discovery.
    for sr in release.content["catalog_snapshots"]:
        snapshot=db.get(SemanticSnapshot,sr["id"])
        if not snapshot or snapshot.workspace_id!=wid or snapshot.content_hash!=sr["content_hash"] or sha(snapshot.content)!=snapshot.content_hash:failure("REFERENCE_INVALID","目录快照hash不一致")
        for dataset in snapshot.content["datasets"]:
            target={"catalog_snapshot_id":snapshot.id,"datasource_id":dataset["datasource_id"],"dataset_id":dataset["dataset_id"]}
            for catalog_field in [None]+dataset["fields"]:
                role="field" if catalog_field else "dataset"
                tref={**target,"field_id":catalog_field["field_id"]} if catalog_field else target
                definition=catalog_field["business_definition"] if catalog_field else {}
                fields={"name":definition.get("name_zh") or (catalog_field["name"] if catalog_field else dataset["logical_locator"]),"definition":definition.get("definition") or dataset["logical_locator"],"aliases":[v["text"] for v in definition.get("aliases",[])]+([catalog_field["name"]] if catalog_field and catalog_field["name"]!=definition.get("name_zh") else []),"grain":definition.get("grain"),"time_kind":catalog_field.get("semantic_role") if catalog_field else None,"unit_code":None}
                doc={"document_id":"catalog:"+sha(tref),"workspace_id":wid,"release_id":release.id,"release_hash":release.content_hash,"target_ref":tref,"role":role,"projection_version":"semantic-search/1.0.0","search_fields":fields,"filters":{"domain_ids":[],"entity_ids":[],"binding_ids":[],"policy_ref":dataset["policy_ref"]},"dependency_refs":[]}
                validate(doc,"search_document",RETRIEVAL);docs.append(doc)
    build_id=new_id("build")
    files=[{"channel":c,"content_hash":sha({"channel":c,"documents":docs})} for c in ("EXACT","BM25","GRAPH")]
    receipt={"id":new_id("index_check"),"workspace_id":wid,"index_build_id":build_id,"release_id":release.id,"release_hash":release.content_hash,"projection_version":"semantic-search/1.0.0","index_files":files,"checks":[{"rule_id":"MANIFEST_CLOSURE_HASH","outcome":"PASS","reason":"确切引用、成员hash和投影Schema通过；仅技术范围"}],"observed_at":stamp()}
    validate(receipt,"index_validation_receipt")
    content={"id":new_id("index"),"workspace_id":wid,"release_id":release.id,"release_hash":release.content_hash,"projection_version":"semantic-search/1.0.0","analyzer_version":"cjk-bigram-en-token/1.0.0","embedding_model":"DISABLED_NO_VECTOR_CHANNEL","embedding_weight_hash":sha({"vector_enabled":False}),"fusion_config_hash":sha({"method":"RRF","k":60,"channels":["EXACT","BM25"]}),"builder_code_hash":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"index_files":files,"validation_receipt_ref":{"id":receipt["id"],"content_hash":sha(receipt)},"index_build_id":build_id}
    validate(content,"index_snapshot")
    db.add(SemanticIndex(id=content["id"],workspace_id=wid,release_id=release.id,release_hash=release.content_hash,content=content,documents=docs,content_hash=sha(content)))
    db.add(SemanticEvent(id=receipt["id"],workspace_id=wid,event_type="index.validation",content=receipt))
    record_event(db,wid,user,"index.completed",{"scope":release.scope,"index_id":content["id"]})
    return {"index_snapshot":content,"index_snapshot_ref":{"id":content["id"],"content_hash":sha(content)},"validation_receipt":receipt,"channels":["EXACT","BM25","GRAPH"],"vector_enabled":False}


def activate(db,wid,user,environment,body):
    if environment not in ("local_validation","production"):failure("SCHEMA_INVALID","未知部署环境")
    if environment=="local_validation" and user.role!="admin":failure("BLOCKED_PERMISSION","本地技术部署仅管理员可用",403)
    if environment=="production" and not assignment(db,wid,user,"PUBLISHER","*"):failure("BLOCKED_PERMISSION","需要明确的发布职责",403)
    rr=body.get("release_ref",{});ir=body.get("index_snapshot_ref",{})
    release=db.get(SemanticRelease,rr.get("id"));index=db.get(SemanticIndex,ir.get("id"))
    if not release or not index or release.workspace_id!=wid or index.workspace_id!=wid or release.scope!=environment or release.content_hash!=rr.get("content_hash") or index.content_hash!=ir.get("content_hash") or index.release_id!=release.id or index.release_hash!=release.content_hash:
        failure("INDEX_RELEASE_MISMATCH","发布与索引不属于同一个完整快照",409)
    if environment=="production":
        for item in release.content["items"]:
            asset=resolve_asset(db,wid,item["asset_ref"])
            required={"BUSINESS_OWNER","DATA_OWNER"} if asset["asset_type"] in ("binding","relation") else {"BUSINESS_OWNER"}
            if not required.issubset({a["reviewer_role"] for a in valid_approvals(db,wid,asset)}) or not active_policy(db,wid,asset["policy_ref"]):failure("APPROVAL_STALE","发布成员审批或政策已失效",409)
    row=db.scalar(select(SemanticDeployment).where(SemanticDeployment.workspace_id==wid,SemanticDeployment.environment==environment).with_for_update())
    expected=body.get("expected_pointer_version")
    if expected!=(row.pointer_version if row else 0):failure("REVISION_CONFLICT","部署指针已变化",409)
    if not row:
        row=SemanticDeployment(workspace_id=wid,environment=environment,pointer_version=1,release_id=release.id,release_hash=release.content_hash,index_snapshot_id=index.id,index_snapshot_hash=index.content_hash,actor_id=user.id)
        db.add(row)
    else:
        row.pointer_version+=1;row.release_id=release.id;row.release_hash=release.content_hash;row.index_snapshot_id=index.id;row.index_snapshot_hash=index.content_hash;row.actor_id=user.id;row.activated_at=now()
    db.flush();record_event(db,wid,user,"deployment.activated",{"scope":environment,"release_id":release.id,"index_id":index.id})
    return {"deployment":get_deployment(db,wid,user,environment),"scope":environment}


def hybrid_rank(query,documents):
    query_terms=tokens(query);counts=[]
    for doc in documents:
        fields=doc["search_fields"]
        counts.append(Counter(tokens(" ".join(str(v) for v in [fields["name"],fields["definition"]," ".join(fields["aliases"])]))))
    df=Counter(t for c in counts for t in c);n=len(counts);avg=sum(sum(c.values()) for c in counts)/max(1,n)
    scored=[]
    for doc,c in zip(documents,counts):
        length=sum(c.values());bm=0.0
        for t in set(query_terms):
            tf=c[t]
            if tf:bm+=math.log(1+(n-df[t]+.5)/(df[t]+.5))*tf*2.2/(tf+1.2*(.25+.75*length/max(1,avg)))
        phrases=[doc["search_fields"]["name"]]+doc["search_fields"]["aliases"]
        exact=max([len(p) for p in phrases if len(p)>=2 and p.lower() in query.lower()]+[0])
        if exact or bm:scored.append({"doc":doc,"exact":exact,"bm25":bm})
    for channel in ("exact","bm25"):
        ranked=sorted([s for s in scored if s[channel]>0],key=lambda s:(-s[channel],s["doc"]["document_id"]))
        for i,s in enumerate(ranked,1):s[channel+"_rank"]=i
    for s in scored:s["rrf"]=sum(1/(60+s[c+"_rank"]) for c in ("exact","bm25") if c+"_rank" in s)
    return sorted(scored,key=lambda s:(-s["rrf"],s["doc"]["document_id"]))


def metadata_candidates_for_bundle(db,wid,user,bundle,*,max_tables=3,max_fields=24,allow_selected=False):
    """Return ACL-filtered, fixed-snapshot metadata for a no-metric discovery turn.

    Ranked table/field hits are suggestions only. If retrieval has no physical hit,
    a small unranked directory sample is returned for user-guided discovery; it is
    never selected as a metric or an execution binding by this function.
    """
    if bundle.get("selected_metric_ref") and not allow_selected:
        return None
    release=db.get(SemanticRelease,bundle.get("release_id"))
    index=db.get(SemanticIndex,bundle.get("index_snapshot_id"))
    if not release or not index or release.workspace_id!=wid or index.workspace_id!=wid:
        return None
    if release.content_hash!=bundle.get("release_hash") or index.content_hash!=bundle.get("index_snapshot_hash"):
        return None
    snapshots={item["id"]:item["content_hash"] for item in release.content.get("catalog_snapshots",[])}
    snapshot_cache={}
    ranked_targets=[]
    field_ranks={}
    for item in bundle.get("candidates",[]):
        role=item.get("role")
        target=item.get("target_ref") or {}
        if role not in {"dataset","field"} or not target.get("catalog_snapshot_id"):
            continue
        dref={k:target[k] for k in ("catalog_snapshot_id","datasource_id","dataset_id") if k in target}
        if set(dref)!={"catalog_snapshot_id","datasource_id","dataset_id"} or dref["catalog_snapshot_id"] not in snapshots:
            continue
        key=canonical(dref)
        if key not in [entry[0] for entry in ranked_targets]:
            ranked_targets.append((key,dref,item.get("fused_rank",999)))
        if role=="field" and target.get("field_id"):
            field_ranks[(key,target["field_id"])]=min(field_ranks.get((key,target["field_id"]),999),item.get("fused_rank",999))
    unranked=not ranked_targets
    if unranked or allow_selected:
        for doc in index.documents:
            target=doc.get("target_ref") or {}
            if doc.get("role")!="dataset" or set(target)!={"catalog_snapshot_id","datasource_id","dataset_id"}:
                continue
            if target["catalog_snapshot_id"] not in snapshots:
                continue
            key=canonical(target)
            if key not in [entry[0] for entry in ranked_targets]:
                ranked_targets.append((key,target,999))
    ranked_targets.sort(key=lambda item:(item[2],item[0]))
    tables=[]
    for key,dref,rank in ranked_targets[:max_tables]:
        if not can_read_physical(db,wid,user,dref,release.scope,snapshot_cache):
            continue
        dataset=resolve_physical(db,wid,dref,snapshot_cache)
        asset=db.scalar(select(Asset).where(Asset.workspace_id==wid,Asset.datasource_id==int(dref["datasource_id"]),
            Asset.namespace==dataset["namespace"],Asset.table_name==dataset["physical_name"],Asset.state=="active"))
        if not asset:
            continue
        metadata=db.scalar(select(CatalogMetadata).where(CatalogMetadata.workspace_id==wid,CatalogMetadata.asset_id==asset.id))
        table_spec=metadata.spec if metadata else {}
        visible_fields=[]
        ordered=sorted(dataset["fields"],key=lambda field:(field_ranks.get((key,field["field_id"]),999),field["field_id"]))
        for field in ordered:
            fref={**dref,"field_id":field["field_id"]}
            if not can_read_physical(db,wid,user,fref,release.scope,snapshot_cache):
                continue
            definition=field.get("business_definition") or {}
            visible_fields.append({
                "field_id":field["field_id"],"name":field["name"],
                "name_zh":definition.get("name_zh") or field["name"],
                "definition":(definition.get("definition") or "")[:320],
                "aliases":[alias.get("text","") for alias in definition.get("aliases",[]) if isinstance(alias,dict) and alias.get("text")][:8],
                "data_type":field["data_type"],"nullable":field.get("nullable"),
                "semantic_role":field.get("semantic_role"),"unit":field.get("unit"),
                "legacy_unit_text":definition.get("legacy_unit_text"),
                "classification":field.get("classification"),
                "is_primary":any(c.get("kind")=="PRIMARY_KEY" and field["field_id"] in c.get("field_ids",[]) for c in dataset.get("key_constraints",[]))})
            if len(visible_fields)>=max_fields:
                break
        tables.append({
            "dataset_ref":dref,"catalog_snapshot_hash":snapshots[dref["catalog_snapshot_id"]],
            "dataset_id":dataset["dataset_id"],"datasource_id":dataset["datasource_id"],
            "catalog_snapshot_id":dref["catalog_snapshot_id"],"datasource_name":db.get(Datasource,int(dref["datasource_id"])).name if db.get(Datasource,int(dref["datasource_id"])) else "",
            "table_label":table_spec.get("name_zh") or table_spec.get("table_name_zh") or asset.name or dataset["logical_locator"],
            "table_definition":(table_spec.get("definition") or asset.description or "")[:320],
            "logical_locator":dataset["logical_locator"],"namespace":dataset["namespace"],"physical_name":dataset["physical_name"],
            "schema_fingerprint":dataset["schema_fingerprint"],"constraints_state":dataset.get("constraints_state"),
            "key_constraints":[c for c in dataset.get("key_constraints",[]) if set(c.get("field_ids",[])).issubset({f["field_id"] for f in visible_fields})],
            "match_rank":rank if not unranked else None,"match_status":"CATALOG_DISCOVERY_ONLY" if unranked else "RETRIEVAL_CANDIDATE",
            "fields":visible_fields})
    if not tables:
        return None
    result={"schema_version":"query-metadata-context/1.0.0","release_id":release.id,
        "release_hash":release.content_hash,"index_snapshot_id":index.id,"index_snapshot_hash":index.content_hash,
        "tables":tables,"match_status":"CATALOG_DISCOVERY_ONLY" if unranked else "RETRIEVAL_CANDIDATE",
        "notice":"固定发布快照中的可访问目录候选；表字段相关性不等于业务口径或执行授权"}
    result["content_hash"]=sha(result)
    return result


def retrieve(db,wid,user,run_id,requirement_id,intent,pinned_deployment=None,*,request_id=None,trace_id=None):
    semantic_access(db,wid,user)
    dep=pinned_deployment or get_deployment(db,wid,user)
    if not dep:failure("SEMANTIC_DEPLOYMENT_UNAVAILABLE","尚无有效语义技术验证部署",409)
    release=db.get(SemanticRelease,dep.get("release_id",dep.get("release_ref",{}).get("id")))
    index=db.get(SemanticIndex,dep.get("index_snapshot_id",dep.get("index_snapshot_ref",{}).get("id")))
    expected_rh=dep.get("release_hash",dep.get("release_ref",{}).get("content_hash"))
    expected_ih=dep.get("index_snapshot_hash",dep.get("index_snapshot_ref",{}).get("content_hash"))
    if not release or not index or release.workspace_id!=wid or index.workspace_id!=wid or release.content_hash!=expected_rh or index.content_hash!=expected_ih or sha(release.content)!=expected_rh or sha(index.content)!=expected_ih or index.release_id!=release.id or index.release_hash!=expected_rh:
        failure("INDEX_RELEASE_MISMATCH","运行固定发布和索引不一致",409)
    if release.scope=="local_validation" and user.role!="admin":failure("BLOCKED_PERMISSION","本地技术问数仅对管理员开放",403)
    query=intent.get("query_text") or intent.get("text") or intent.get("question") or ""
    if not isinstance(query,str) or not query.strip() or len(query)>8000:failure("SCHEMA_INVALID","检索问题必须是1到8000字文本",422)
    metric_hint=intent.get("metric_id") or intent.get("metric")
    if isinstance(metric_hint,dict):metric_hint=metric_hint.get("asset_id","").removeprefix("metric:")
    metric_terms=intent.get("metric_terms",[]) or []
    if not isinstance(metric_terms,list) or len(metric_terms)>20 or any(not isinstance(t,str) for t in metric_terms):failure("SCHEMA_INVALID","指标词必须是有限文本列表",422)
    if not metric_hint:
        exact_ids={d["target_ref"]["asset_id"].removeprefix("metric:") for d in index.documents if d["role"]=="metric"}
        id_hints=[t.removeprefix("metric:") for t in metric_terms if isinstance(t,str) and t.removeprefix("metric:") in exact_ids]
        if len(set(id_hints))==1:metric_hint=id_hints[0]
    requested=intent.get("dimension_ids",intent.get("dimensions",[])) or []
    if isinstance(requested,str):requested=[requested]
    if not isinstance(requested,list) or len(requested)>10:failure("SCHEMA_INVALID","维度请求必须是有限列表",422)
    dimension_ids=[d.get("asset_id",d.get("id","")) if isinstance(d,dict) else d for d in requested]
    if any(not isinstance(d,str) for d in dimension_ids):failure("SCHEMA_INVALID","维度身份必须为文本",422)
    dimension_ids=[d.removeprefix("dimension:") for d in dimension_ids]
    visible_documents=[];snapshot_cache={}
    for document in index.documents:
        target=document["target_ref"]
        if "asset_id" in target:
            target_asset=resolve_asset(db,wid,target)
            if not can_read_asset(db,wid,user,target_asset):continue
            if not validity_active(target_asset["validity"]):continue
            current_gov=db.get(SemanticGovernance,(wid,target["asset_id"]))
            if current_gov and current_gov.revision==target["revision"] and current_gov.workflow_state in ("WITHDRAWN","REJECTED"):
                continue
        elif not can_read_physical(db,wid,user,target,release.scope,snapshot_cache):
            continue
        visible_documents.append(document)
    ranked=hybrid_rank(query,visible_documents)
    candidates=[]
    by_role=Counter()
    for s in ranked:
        role=s["doc"]["role"]
        if by_role[role]>=10:continue
        by_role[role]+=1
        channels=[{"channel":c.upper() if c=="exact" else "BM25","raw_score":s[c],"rank":s[c+"_rank"]} for c in ("exact","bm25") if c+"_rank" in s]
        candidates.append({"target_ref":s["doc"]["target_ref"],"role":role,"channel_matches":channels,"fused_rank":len(candidates)+1,"match_reasons":["固定快照中Exact/BM25匹配；排序不代表业务批准"],"conflict_reasons":[]})
    metric_candidates=[s for s in ranked if s["doc"]["role"]=="metric"]
    # Exact business names/aliases resolve semantic identity. RRF remains the
    # discovery ranking and is reported unchanged, including its own misses.
    longest_exact=max((candidate["exact"] for candidate in metric_candidates),default=0)
    exact_metrics=[candidate for candidate in metric_candidates if candidate["exact"]==longest_exact and longest_exact>0]
    selected=None
    if metric_hint:
        selected=next((d["target_ref"] for d in visible_documents if d["role"]=="metric" and d["target_ref"]["asset_id"]=="metric:"+metric_hint),None)
    elif "销售额" in query and not (len(exact_metrics)==1 and longest_exact>len("销售额")):
        selected=None
    elif len(exact_metrics)==1:
        selected=exact_metrics[0]["doc"]["target_ref"]
    elif metric_candidates:
        if len(metric_candidates)==1 or metric_candidates[0]["exact"]>metric_candidates[1]["exact"]:
            selected=metric_candidates[0]["doc"]["target_ref"]
    coverage=[];closure=[];vrefs=[];binding_ref=None;next_action="NEED_CONFIRMATION"
    def cover(slot,status,reason):coverage.append({"slot":slot,"status":status,"reason":reason})
    if re.search(r"买家状态|客户状态|用户状态",query) or "buyer_status" in dimension_ids:
        selected=None;next_action="GOVERNANCE_REQUIRED";cover("dimension_mapping","MISSING","缺少已治理买家业务状态维度；地域state不可代替状态")
    if not selected:
        cover("metric_definition","MISSING","指标不唯一或没有受支持匹配，需要澄清")
        if next_action!="GOVERNANCE_REQUIRED":next_action="NEED_CLARIFICATION"
    else:
        metric=resolve_asset(db,wid,selected);cover("metric_definition","COMPLETE","固定指标定义已回源；仅本地技术验证")
        bindings=[]
        for d in visible_documents:
            if d["role"]=="binding":
                a=resolve_asset(db,wid,d["target_ref"])
                if a["spec"]["metric_ref"]==selected:bindings.append(a)
        if len(bindings)!=1:
            cover("binding","MISSING" if not bindings else "CONFLICT","没有唯一可判定Binding")
            next_action="GOVERNANCE_REQUIRED" if not bindings else "NEED_CLARIFICATION"
        else:
            binding=bindings[0];binding_ref=ref(binding)
            allowed={r["asset_id"] for r in metric["spec"]["allowed_dimension_refs"]}
            implemented={i["dimension_ref"]["asset_id"]:i for i in binding["spec"]["dimension_implementations"]}
            dims=[]
            for dim in dimension_ids:
                did="dimension:"+dim
                if did not in allowed or did not in implemented:
                    cover("dimension_mapping","MISSING","该Binding不支持所请求维度："+dim);next_action="GOVERNANCE_REQUIRED"
                else:dims.append(implemented[did]["dimension_ref"])
            if not dimension_ids or len(dims)==len(dimension_ids):cover("dimension_mapping","COMPLETE","按选中Binding和Dimension修订验证实现范围")
            pending=[selected,binding_ref]+dims;seen=set();budget=400
            release_refs={(i["asset_ref"]["asset_id"],i["asset_ref"]["revision"]):i["content_hash"] for i in release.content["items"]}
            while pending and len(seen)<budget:
                target=pending.pop();key=canonical(target)
                if key in seen:continue
                seen.add(key)
                if "asset_id" in target:
                    asset=resolve_asset(db,wid,target)
                    if not can_read_asset(db,wid,user,asset):
                        cover("permission","UNKNOWN","必要依赖现时政策不可用");next_action="BLOCKED_PERMISSION";continue
                    if not validity_active(asset["validity"]):
                        cover("source_validity","CONFLICT","必要依赖不在业务有效期");next_action="BLOCKED_EVIDENCE";continue
                    current_gov=db.get(SemanticGovernance,(wid,asset["asset_id"]))
                    if current_gov and current_gov.revision==asset["revision"] and current_gov.workflow_state in ("WITHDRAWN","REJECTED"):
                        cover("dependency_closure","CONFLICT","必要依赖修订已撤回或退回");next_action="BLOCKED_EVIDENCE";continue
                    if release_refs.get((asset["asset_id"],asset["revision"]))!=sha(asset):
                        cover("dependency_closure","CONFLICT","依赖不在同一固定发布清单");next_action="BLOCKED_EVIDENCE";continue
                    closure.append({"kind":"ASSET","target_ref":target,"binding_scope_ref":binding_ref,"dimension_scope_ref":None})
                    if asset["asset_type"]=="evidence" and not verify_archived_evidence(db,wid,asset):
                        cover("source_evidence","CONFLICT","证据原文与固定来源不一致");next_action="BLOCKED_EVIDENCE"
                    # Do not expand unrelated dimension implementations. Their
                    # conditional JOIN is not a global capability of a Binding.
                    body=copy.deepcopy(asset)
                    if asset["asset_type"]=="binding":
                        wanted=set(dimension_ids)
                        body["spec"]["dimension_implementations"]=[i for i in body["spec"]["dimension_implementations"] if i["dimension_ref"]["asset_id"].removeprefix("dimension:") in wanted]
                        required_joins=set(body["spec"]["base_join_ids"])
                        for impl in body["spec"]["dimension_implementations"]:required_joins.update(impl["required_join_ids"])
                        body["spec"]["joins"]=[j for j in body["spec"]["joins"] if j["join_id"] in required_joins]
                    for p,k,t in walk_refs(body):
                        if p:pending.append(t)
                else:
                    try:resolve_physical(db,wid,target,snapshot_cache)
                    except HTTPException:
                        cover("physical_mapping","MISSING","目录引用回源失败");next_action="BLOCKED_EVIDENCE";continue
                    if not can_read_physical(db,wid,user,target,release.scope,snapshot_cache):
                        cover("permission","UNKNOWN","必要物理依赖现时政策不可用");next_action="BLOCKED_PERMISSION";continue
                    scope=next((r for r in dims if any(canonical(target)==canonical(t) for _,_,t in walk_refs(implemented[r["asset_id"]]))),None)
                    closure.append({"kind":"FIELD" if "field_id" in target else "DATASET","target_ref":target,"binding_scope_ref":binding_ref,"dimension_scope_ref":scope})
            cover("dependency_closure","COMPLETE" if not pending else "UNKNOWN","显式ID补链与回源完成" if not pending else "补链达到资源上限")
            cover("grain","COMPLETE","固定统计粒度和去重表达式；本地样本技术范围")
            unit=metric["spec"]["unit"]
            if unit["kind"]=="CURRENCY":
                cover("unit_currency","UNKNOWN","金额币种尚未获权威确认；合成CNY不构成生产币种");next_action="BLOCKED_EVIDENCE"
            else:cover("unit_currency","COMPLETE","非金额指标单位已在技术契约中明确")
            time_req=intent.get("time_request",intent.get("time",{})) or {}
            if isinstance(time_req,dict):
                start=time_req.get("start") or intent.get("start")
                end=time_req.get("end_exclusive") or time_req.get("end") or intent.get("end_exclusive") or intent.get("end")
            else:start=end=None
            coverage_spec=binding["spec"]["coverage"]
            # Coverage authority belongs to this exact Binding revision.
            # Frozen seed sidecars never prove live database completeness.
            lower=coverage_spec["start"]
            upper=coverage_spec["end_exclusive"]
            if start and end and lower and upper and (str(start)[:10]<lower[:10] or str(end)[:10]>upper[:10]):
                cover("time","MISSING","请求时间超出本地合成数据覆盖");next_action="BLOCKED_EVIDENCE"
            elif start and end:
                cover("time","COMPLETE" if coverage_spec["start"] and coverage_spec["end_exclusive"] else "UNKNOWN","已明确请求事件范围；按源数据时间核对真实覆盖")
            else:
                cover("time","UNKNOWN","需要明确时间范围");next_action="NEED_CLARIFICATION"
            cover("technical_verification","UNKNOWN","已完成类型、固定引用与归档来源核对；当前数据库结构、覆盖和JOIN须由执行前live source_check另行验证")
    cover("business_evidence","UNKNOWN","缺真实业务Owner审批；生产执行始终关闭")
    if any(c["slot"]=="permission" and c["status"]!="COMPLETE" for c in coverage):next_action="BLOCKED_PERMISSION"
    elif any(c["status"]=="CONFLICT" for c in coverage):next_action="BLOCKED_EVIDENCE"
    elif intent.get("negative_terms"):
        cover("negative_constraints","UNKNOWN","排除/否定约束需要精确口径确认");next_action="NEED_CLARIFICATION"
    reqdims=[]
    for dim in dimension_ids:
        found=next((d["target_ref"] for d in index.documents if d["role"]=="dimension" and d["target_ref"]["asset_id"]=="dimension:"+dim),None)
        if found:reqdims.append(found)
    bundle={"schema_version":"semantic-retrieval/1.0.0","bundle_id":new_id("bundle"),"request_id":request_id or intent.get("request_id") or new_id("req"),"trace_id":trace_id or intent.get("trace_id") or new_id("trace"),"workspace_id":wid,"run_id":str(run_id),"requirement_id":str(requirement_id),"intent_frame_hash":sha(intent),"release_id":release.id,"release_hash":release.content_hash,"index_snapshot_id":index.id,"index_snapshot_hash":index.content_hash,"permission_decision_id":new_id("local_policy"),"permission_checked_at":stamp(),"requested_dimension_refs":reqdims,"selected_metric_ref":selected,"selected_binding_ref":binding_ref,"candidates":candidates,"closure_refs":closure,"coverage":coverage,"verification_refs":vrefs,"truncated":False,"timed_out_channels":[],"next_action":next_action}
    validate(bundle,"retrieval_bundle",RETRIEVAL)
    db.add(SemanticBundle(id=bundle["bundle_id"],workspace_id=wid,run_id=str(run_id),requirement_id=str(requirement_id),content=bundle,content_hash=sha(bundle)))
    db.flush()
    return bundle


def envelope(wid,data,request=None):
    rid=request.headers.get("X-Request-ID") if request else None
    bundle=data.get("bundle",{}) if isinstance(data,dict) else {}
    return {"api_version":"semantic-api/1.0.0","request_id":bundle.get("request_id") or rid or new_id("req"),"trace_id":bundle.get("trace_id") or new_id("trace"),"workspace_id":wid,"data":data}


async def command(request,db,wid,user,handler):
    workspace_access(db,user,wid)
    if not user.enabled:failure("BLOCKED_PERMISSION","账号不可用",403)
    key=request.headers.get("Idempotency-Key","")
    if not key or len(key)>200:failure("IDEMPOTENCY_KEY_REQUIRED","写命令需要Idempotency-Key",400)
    try:body=await request.json()
    except ValueError:failure("SCHEMA_INVALID","请求正文不是合法JSON",422)
    if not isinstance(body,dict):failure("SCHEMA_INVALID","命令必须是对象",422)
    cid=sha([wid,user.id,request.method,request.url.path,key]);ih=sha(body)
    if request.url.path.endswith("/assignments") or "/policies" in request.url.path:
        if user.role!="admin":failure("BLOCKED_PERMISSION","治理权限配置仅管理员可用",403)
    cached=db.get(SemanticCommand,cid)
    if cached:
        if cached.input_hash!=ih:failure("IDEMPOTENCY_CONFLICT","同幂等键对应不同命令",409)
        if not (request.url.path.endswith("/assignments") or "/policies" in request.url.path):semantic_access(db,wid,user,write=True)
        # An idempotent receipt is not a permanent read grant. Recheck exact
        # references before returning a historical response after revocation.
        previous_bundle=cached.response.get("data",{}).get("bundle")
        if isinstance(previous_bundle,dict):check_bundle_access(db,wid,user,previous_bundle)
        snapshot_cache={}
        for _,kind,target in walk_refs(cached.response):
            if kind=="ASSET" and not can_read_asset(db,wid,user,resolve_asset(db,wid,target)):
                failure("BLOCKED_PERMISSION","历史命令结果的现时读取政策已失效",403)
            if kind in ("FIELD","DATASET") and not can_read_physical(db,wid,user,target,"local_validation",snapshot_cache):
                failure("BLOCKED_PERMISSION","历史命令结果的现时物理目录读取政策已失效",403)
        return cached.response
    if request.url.path.endswith("/assignments") or "/policies" in request.url.path:
        if user.role!="admin":failure("BLOCKED_PERMISSION","治理权限配置仅管理员可用",403)
    else:semantic_access(db,wid,user,write=True)
    result=handler(body)
    response=envelope(wid,result,request)
    db.add(SemanticCommand(id=cid,workspace_id=wid,actor_id=user.id,input_hash=ih,response=response));db.commit()
    return response


def cas_header(request,expected):
    header=request.headers.get("If-Match","").strip('"')
    if header!=str(expected):failure("PRECONDITION_REQUIRED","If-Match必须与期望版本一致",428)


@router.get("/overview")
def overview(wid:int,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    rows=[r for r in current_assets(db,wid) if can_read_asset(db,wid,user,r.content)]
    admin=user.role=="admin"
    return envelope(wid,{"counts":dict(Counter(r.asset_type for r in rows)),"staging_count":db.scalar(select(func.count()).select_from(SemanticStaging).where(SemanticStaging.workspace_id==wid)) if admin else None,"catalog_snapshot_count":db.scalar(select(func.count()).select_from(SemanticSnapshot).where(SemanticSnapshot.workspace_id==wid)) if admin else None,"production_ready_paths":0,"local_validation_deployment":get_deployment(db,wid,user) if admin else None,"issues":["本地导入默认草稿，不含业务批准","金额币种与真实行列策略需逐项治理"],"scope":"governance"})


@router.get("/assets")
def list_assets(wid:int,type:str="",q:str="",user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    rows=current_assets(db,wid)
    rows=[r for r in rows if can_read_asset(db,wid,user,r.content) and (not type or r.asset_type==type) and (not q or q.lower() in canonical(r.content).lower())]
    return envelope(wid,{"items":[asset_item(db,r) for r in rows],"total":len(rows),"scope":"governance"})


@router.get("/templates")
def templates(wid:int,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    found={}
    for row in current_assets(db,wid):
        if can_read_asset(db,wid,user,row.content) and row.asset_type not in found:
            template=copy.deepcopy(row.content);template["asset_id"]=row.asset_type+":"+uuid.uuid4().hex[:12];template["revision"]=1
            template["name"]="新建"+row.asset_type;template["definition"]="待维护业务定义";template["owner_ids"]=[];template["steward_ids"]=[]
            found[row.asset_type]=template
    return envelope(wid,{"templates":found,"schemas":{"authority":AUTHORITY},"types":list(TYPES)})


@router.get("/assets/{asset_id}")
def get_asset(wid:int,asset_id:str,revision:int|None=None,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    gov=db.get(SemanticGovernance,(wid,asset_id))
    row=db.get(SemanticRevision,(wid,asset_id,revision or (gov.revision if gov else 0)))
    if not row or not can_read_asset(db,wid,user,row.content):failure("REFERENCE_INVALID","资产不可用",404)
    data=asset_item(db,row)
    data["verification_receipts"]=[r.content for r in db.scalars(select(SemanticVerification).where(SemanticVerification.workspace_id==wid,SemanticVerification.asset_id==asset_id,SemanticVerification.revision==row.revision))]
    data["approvals"]=[r.content for r in db.scalars(select(SemanticApproval).where(SemanticApproval.workspace_id==wid,SemanticApproval.asset_id==asset_id,SemanticApproval.revision==row.revision))]
    data["revisions"]=[{"revision":r.revision,"content_hash":r.content_hash,"created_at":r.created_at.isoformat()} for r in db.scalars(select(SemanticRevision).where(SemanticRevision.workspace_id==wid,SemanticRevision.asset_id==asset_id).order_by(SemanticRevision.revision.desc()))]
    return envelope(wid,data)


@router.get("/assets/{asset_id}/implementation")
def asset_implementation(wid:int,asset_id:str,revision:int|None=None,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    gov=db.get(SemanticGovernance,(wid,asset_id))
    row=db.get(SemanticRevision,(wid,asset_id,revision or (gov.revision if gov else 0)))
    if not row or not can_read_asset(db,wid,user,row.content):failure("REFERENCE_INVALID","资产不可用",404)
    return envelope(wid,implementation_diagnostic(db,wid,row.content,user))


@router.post("/assets")
async def create_asset(wid:int,request:Request,user=Depends(identity),db=Depends(db_session)):
    from .semantic_maintenance import save_with_attachments
    return await command(request,db,wid,user,lambda b:save_with_attachments(db,wid,user,b))


@router.post("/assets/{asset_id}/revisions")
async def revision_asset(wid:int,asset_id:str,request:Request,user=Depends(identity),db=Depends(db_session)):
    def action(b):
        cas_header(request,b.get("expected_record_version"))
        a=b.get("asset_revision",{})
        if a.get("asset_id")!=asset_id:failure("REFERENCE_INVALID","资产ID不一致")
        from .semantic_maintenance import save_with_attachments
        return save_with_attachments(db,wid,user,b,b.get("base_revision"),b.get("expected_record_version"))
    return await command(request,db,wid,user,action)


@router.post("/governance/{asset_id}/{action}")
async def governance_command(wid:int,asset_id:str,action:str,request:Request,user=Depends(identity),db=Depends(db_session)):
    def execute(b):
        gov=db.scalar(select(SemanticGovernance).where(SemanticGovernance.workspace_id==wid,SemanticGovernance.asset_id==asset_id).with_for_update())
        if not gov:failure("REFERENCE_INVALID","资产不可用",404)
        cas_header(request,b.get("expected_record_version"))
        if b.get("expected_record_version")!=gov.record_version:failure("REVISION_CONFLICT","治理记录已变化",409)
        a=resolve_asset(db,wid,{"workspace_id":wid,"asset_id":asset_id,"asset_type":db.get(SemanticRevision,(wid,asset_id,gov.revision)).asset_type,"revision":gov.revision})
        if not can_read_asset(db,wid,user,a):failure("REFERENCE_INVALID","资产不可用",404)
        if b.get("content_hash") and b["content_hash"]!=gov.content_hash:failure("REVISION_CONFLICT","内容hash已变化",409)
        if action=="verify":
            require_steward(db,wid,user,asset_id)
            receipts=verify_asset(db,wid,user,a)
            return {"verification_receipts":receipts,"governance_record":governance_json(gov,a)}
        if action=="submit":
            require_steward(db,wid,user,asset_id)
            if b.get("dependency_hash") and b["dependency_hash"]!=gov.dependency_hash:failure("REVISION_CONFLICT","依赖hash不一致",409)
            if not owner_assignment_valid(db,wid,a) or not active_policy(db,wid,a["policy_ref"]):failure("APPROVAL_NOT_READY","需要真实Owner和已解析政策后才能提交业务审核",409)
            if gov.validation_state!="PASS":failure("VERIFICATION_REQUIRED","所有必要机器核验必须PASS后才能提交审核",409,issue_codes=gov.issue_codes)
            if gov.workflow_state not in ("DRAFT","REJECTED"):failure("INVALID_STATE","该修订不处于可提交状态",409)
            gov.workflow_state="IN_REVIEW";gov.record_version+=1
            record_event(db,wid,user,"governance.submitted",{"asset_id":asset_id,"content_hash":gov.content_hash,"dependency_hash":gov.dependency_hash})
            return {"governance_record":governance_json(gov,a)}
        if action=="decisions":
            decision=b.get("decision")
            role=b.get("reviewer_role","BUSINESS_OWNER")
            if decision not in ("APPROVE","REJECT","WITHDRAW") or role not in ("BUSINESS_OWNER","DATA_OWNER","PUBLISHER"):failure("SCHEMA_INVALID","无效审核决定或角色",422)
            if not assignment(db,wid,user,role,asset_id):failure("BLOCKED_PERMISSION","没有该资产的审核职责分派",403)
            if role=="BUSINESS_OWNER" and str(user.id) not in a["owner_ids"]:failure("BLOCKED_PERMISSION","审核人不是该修订登记的业务Owner",403)
            creator=db.get(SemanticRevision,(wid,asset_id,gov.revision)).actor
            if decision=="APPROVE" and creator==user.username:failure("SEPARATION_OF_DUTIES","维护人不能批准自己编写的修订",403)
            if decision in ("APPROVE","REJECT") and gov.workflow_state!="IN_REVIEW":failure("INVALID_STATE","仅可审核明确送审的修订",409)
            if decision=="APPROVE" and (gov.validation_state!="PASS" or not owner_assignment_valid(db,wid,a) or not active_policy(db,wid,a["policy_ref"])):failure("APPROVAL_NOT_READY","核验、Owner或政策已不满足审批条件",409)
            if not b.get("reason"):failure("SCHEMA_INVALID","审核必须填写理由",422)
            prior=valid_approvals(db,wid,a)
            if decision=="APPROVE" and a["asset_type"] in ("binding","relation") and any(p["reviewer_id"]==str(user.id) and p["reviewer_role"]!=role for p in prior):failure("SEPARATION_OF_DUTIES","业务与数据Owner必须由不同身份审核",403)
            approval={"id":new_id("approval"),"workspace_id":wid,"target_ref":ref(a),"content_hash":gov.content_hash,"dependency_hash":gov.dependency_hash,"reviewer_id":str(user.id),"reviewer_role":role,"decision":decision,"reason":b["reason"],"decided_at":stamp()}
            validate(approval,"approval")
            db.add(SemanticApproval(id=approval["id"],workspace_id=wid,asset_id=asset_id,revision=gov.revision,content=approval,content_hash=sha(approval)))
            db.flush()
            if decision=="APPROVE":
                required={"BUSINESS_OWNER","DATA_OWNER"} if a["asset_type"] in ("binding","relation") else {"BUSINESS_OWNER"}
                gov.workflow_state="APPROVED" if required.issubset({p["reviewer_role"] for p in valid_approvals(db,wid,a)}) else "IN_REVIEW"
            else:gov.workflow_state="REJECTED" if decision=="REJECT" else "WITHDRAWN"
            gov.record_version+=1
            record_event(db,wid,user,"governance.decision",{"asset_id":asset_id,"decision":decision})
            return {"approval":approval,"governance_record":governance_json(gov,a)}
        failure("NOT_FOUND","不支持的治理动作",404)
    return await command(request,db,wid,user,execute)


@router.get("/catalog-snapshots")
def catalog_snapshots(wid:int,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    items=[]
    for row in db.scalars(select(SemanticSnapshot).where(SemanticSnapshot.workspace_id==wid)):
        datasets=[];redacted=False;cache={}
        for dataset in row.content["datasets"]:
            target={"catalog_snapshot_id":row.id,"datasource_id":dataset["datasource_id"],"dataset_id":dataset["dataset_id"]}
            if not can_read_physical(db,wid,user,target,"local_validation",cache):redacted=True;continue
            visible=copy.deepcopy(dataset)
            visible["fields"]=[f for f in dataset["fields"] if can_read_physical(db,wid,user,{**target,"field_id":f["field_id"]},"local_validation",cache)]
            if len(visible["fields"])!=len(dataset["fields"]):redacted=True
            # Key constraints could reveal hidden field names; retain only fully
            # visible local members, and never expose hidden remote definitions.
            visible_ids={f["field_id"] for f in visible["fields"]}
            visible["key_constraints"]=[c for c in visible["key_constraints"] if set(c["field_ids"]).issubset(visible_ids) and c["target_dataset_ref"] is None]
            datasets.append(visible)
        if datasets:items.append({**row.content,"catalog_snapshot_id":row.id,"content_hash":row.content_hash,"datasets":datasets,"redacted":redacted})
    return envelope(wid,{"items":items})


@router.post("/catalog-snapshots")
async def import_catalog_snapshot(wid:int,request:Request,user=Depends(identity),db=Depends(db_session)):
    def action(b):
        if user.role!="admin":failure("BLOCKED_PERMISSION","目录快照导入仅管理员可用",403)
        snapshot=b.get("catalog_snapshot",{})
        validate(snapshot,"catalog_snapshot")
        if snapshot["workspace_id"]!=wid:failure("REFERENCE_INVALID","目录快照工作空间不一致",422)
        for dataset in snapshot["datasets"]:
            if dataset["connector_kind"]=="POSTGRESQL":
                try:source_id=int(dataset["datasource_id"])
                except ValueError:failure("REFERENCE_INVALID","物理数据源身份无效",422)
                asset=db.scalar(select(Asset).where(Asset.workspace_id==wid,Asset.datasource_id==source_id,Asset.namespace==dataset["namespace"],Asset.table_name==dataset["physical_name"],Asset.state=="active"))
                if not asset or asset.fingerprint!=dataset["schema_fingerprint"] or {f["field_id"] for f in dataset["fields"]}!={f["name"] for f in asset.columns}:failure("SOURCE_STALE","物理快照必须与本空间已采集的目录和指纹一致",409)
            elif dataset["constraints_state"]!="UNKNOWN" or any(k["observed_status"]!="UNKNOWN" for k in dataset["key_constraints"]):
                failure("SCHEMA_INVALID","LOCAL_FIXTURE结构夹具不能自报物理约束已核验",422)
        sid=snapshot["catalog_snapshot_id"];digest=sha(snapshot)
        existing=db.get(SemanticSnapshot,sid)
        if existing:
            if existing.workspace_id!=wid or existing.content_hash!=digest:failure("REVISION_CONFLICT","该快照ID已固定为不同内容",409)
        else:db.add(SemanticSnapshot(id=sid,workspace_id=wid,content=snapshot,content_hash=digest))
        record_event(db,wid,user,"catalog.snapshot_imported",{"catalog_snapshot_id":sid,"scope":"structural_catalog_only"})
        return {"catalog_snapshot":snapshot,"catalog_snapshot_ref":{"id":sid,"content_hash":digest},"query_execution_granted":False}
    return await command(request,db,wid,user,action)


@router.get("/maintenance-context")
def maintenance_context(wid:int,user=Depends(identity),db=Depends(db_session)):
    """Exact historical references and physical context for semantic editors."""
    semantic_access(db,wid,user)
    assets=[]
    for row in db.scalars(select(SemanticRevision).where(SemanticRevision.workspace_id==wid).order_by(SemanticRevision.asset_type,SemanticRevision.asset_id,SemanticRevision.revision.desc())):
        if not can_read_asset(db,wid,user,row.content):continue
        item=asset_item(db,row)
        item["is_current"]=item["governance_record"] is not None
        assets.append(item)
    snapshots=catalog_snapshots(wid,user,db)["data"]["items"]
    fields=[]
    for snapshot in snapshots:
        for dataset in snapshot["datasets"]:
            dref={"catalog_snapshot_id":snapshot["catalog_snapshot_id"],"datasource_id":dataset["datasource_id"],"dataset_id":dataset["dataset_id"]}
            for field in dataset["fields"]:
                fields.append({"field_ref":{**dref,"field_id":field["field_id"]},"dataset_ref":dref,
                    "dataset_label":dataset["logical_locator"],"field_label":field["business_definition"]["name_zh"],
                    "physical_locator":dataset["logical_locator"]+"."+field["name"],"data_type":field["data_type"],
                    "nullable":field["nullable"],"semantic_role":field["semantic_role"],"unit":field["unit"],
                    "classification":field["classification"],"business_definition":field["business_definition"]})
    diagnostics=[implementation_diagnostic(db,wid,item["asset_revision"],user) for item in assets if item["is_current"] and item["asset_ref"]["asset_type"]=="binding"]
    return envelope(wid,{"assets":assets,"snapshots":snapshots,"field_contexts":fields,"implementation_diagnostics":diagnostics,"types":list(TYPES),"scope":"governance","automatic_reference_upgrade":False})


@router.post("/catalog-snapshots/{snapshot_id}/revisions")
async def revise_catalog_semantics(wid:int,snapshot_id:str,request:Request,user=Depends(identity),db=Depends(db_session)):
    """Copy an immutable physical snapshot while changing field semantics only."""
    def action(body):
        if user.role!="admin":failure("BLOCKED_PERMISSION","目录释义维护仅管理员可用",403)
        previous=db.get(SemanticSnapshot,snapshot_id)
        if not previous or previous.workspace_id!=wid or sha(previous.content)!=previous.content_hash:failure("REFERENCE_INVALID","目录快照不可用",404)
        if body.get("base_content_hash")!=previous.content_hash:failure("REVISION_CONFLICT","目录快照hash不一致",409)
        if not isinstance(body.get("reason"),str) or not body["reason"].strip():failure("SCHEMA_INVALID","目录释义修订需要明确原因",422)
        updates=body.get("field_updates")
        if not isinstance(updates,list) or not updates or len(updates)>500:failure("SCHEMA_INVALID","需要1到500项字段释义修订",422)
        snapshot=copy.deepcopy(previous.content);seen=set()
        for update in updates:
            if not isinstance(update,dict) or set(update)-{"dataset_id","datasource_id","field_id","business_definition","semantic_role","unit","classification"}:failure("SCHEMA_INVALID","只允许修改字段释义、语义角色、单位和分类；物理结构不能通过此接口变更",422)
            key=(update.get("datasource_id"),update.get("dataset_id"),update.get("field_id"))
            if key in seen:failure("SCHEMA_INVALID","同一命令不能重复维护相同字段",422)
            seen.add(key)
            dataset=next((d for d in snapshot["datasets"] if (d["datasource_id"],d["dataset_id"])==key[:2]),None)
            field=next((f for f in dataset["fields"] if f["field_id"]==key[2]),None) if dataset else None
            if field is None:failure("REFERENCE_INVALID","字段不属于所选目录快照",422)
            for name in ("semantic_role","unit","classification"):
                if name in update:field[name]=copy.deepcopy(update[name])
            if "business_definition" in update:
                patch=update["business_definition"]
                if not isinstance(patch,dict):failure("SCHEMA_INVALID","字段业务释义必须为对象",422)
                field["business_definition"].update(copy.deepcopy(patch))
        if snapshot==previous.content:failure("NO_CHANGES","目录释义未发生变化",409)
        snapshot["catalog_snapshot_id"]=new_id("catalog")
        snapshot["source_revision"]=previous.content["source_revision"]+"; semantic-revision:"+snapshot["catalog_snapshot_id"]
        validate(snapshot,"catalog_snapshot")
        # Physical namespace, names, types, nullability, fingerprints and keys
        # are inherited byte-for-byte. Existing Bindings stay on the old ref.
        for _,kind,target in walk_refs(snapshot):
            if kind=="ASSET":resolve_asset(db,wid,target)
        digest=sha(snapshot)
        db.add(SemanticSnapshot(id=snapshot["catalog_snapshot_id"],workspace_id=wid,content=snapshot,content_hash=digest))
        affected=[]
        for row in current_assets(db,wid):
            if row.asset_type=="binding" and can_read_asset(db,wid,user,row.content) and any(kind in ("DATASET","FIELD") and target["catalog_snapshot_id"]==snapshot_id for _,kind,target in walk_refs(row.content)):
                affected.append(ref(row.content))
        record_event(db,wid,user,"catalog.semantic_revision_created",{"catalog_snapshot_id":snapshot["catalog_snapshot_id"],"base_snapshot_id":snapshot_id,"reason":body["reason"].strip(),"field_count":len(updates),"scope":"structural_catalog_only"})
        return {"catalog_snapshot":snapshot,"catalog_snapshot_ref":{"id":snapshot["catalog_snapshot_id"],"content_hash":digest},"base_catalog_snapshot_ref":{"id":snapshot_id,"content_hash":previous.content_hash},"affected_bindings":affected,"automatic_reference_upgrade":False,"query_execution_granted":False,"production_approved":False}
    return await command(request,db,wid,user,action)


@router.get("/releases")
def releases(wid:int,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    items=[]
    for r in db.scalars(select(SemanticRelease).where(SemanticRelease.workspace_id==wid).order_by(SemanticRelease.created_at.desc())):
        if user.role!="admin" and (r.scope=="local_validation" or any(not can_read_asset(db,wid,user,resolve_asset(db,wid,i["asset_ref"])) for i in r.content["items"])):continue
        items.append({"release":r.content,"release_ref":{"id":r.id,"content_hash":r.content_hash},"scope":r.scope})
    return envelope(wid,{"items":items})


@router.post("/releases")
async def release_command(wid:int,request:Request,user=Depends(identity),db=Depends(db_session)):
    return await command(request,db,wid,user,lambda b:create_release(db,wid,user,b))


@router.get("/index-snapshots")
def indices(wid:int,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    return envelope(wid,{"items":[{"index_snapshot":r.content,"index_snapshot_ref":{"id":r.id,"content_hash":r.content_hash}} for r in db.scalars(select(SemanticIndex).where(SemanticIndex.workspace_id==wid).order_by(SemanticIndex.created_at.desc())) if user.role=="admin" or assignment(db,wid,user,"PUBLISHER","*")]})


@router.post("/index-builds")
async def index_command(wid:int,request:Request,user=Depends(identity),db=Depends(db_session)):
    return await command(request,db,wid,user,lambda b:build_index(db,wid,user,b.get("release_ref",{})))


@router.get("/deployments/{environment}")
def deployment(wid:int,environment:str,user=Depends(identity),db=Depends(db_session)):
    return envelope(wid,{"deployment":get_deployment(db,wid,user,environment),"scope":environment})


@router.post("/deployments/{environment}/activate")
async def deployment_command(wid:int,environment:str,request:Request,user=Depends(identity),db=Depends(db_session)):
    def action(b):
        cas_header(request,b.get("expected_pointer_version"))
        return activate(db,wid,user,environment,b)
    return await command(request,db,wid,user,action)


@router.post("/deployments/local_validation/rebuild")
async def rebuild_local_drafts(wid:int,request:Request,user=Depends(identity),db=Depends(db_session)):
    """Explicit draft-only technical publication; never creates approvals."""
    def action(body):
        if user.role!="admin":failure("BLOCKED_PERMISSION","本地草稿技术快照仅管理员可构建",403)
        if body.get("scope")!="local_validation":failure("SCHEMA_INVALID","必须明确选择local_validation本地技术范围",422)
        if not isinstance(body.get("reason"),str) or not body["reason"].strip():failure("SCHEMA_INVALID","本地技术快照重建需要明确原因",422)
        selected=body.get("draft_asset_refs")
        if not isinstance(selected,list) or not selected or len(selected)>500:failure("SCHEMA_INVALID","需要明确选择1到500个当前草稿修订",422)
        expected=body.get("expected_pointer_version");cas_header(request,expected)
        current=get_deployment(db,wid,user)
        if expected!=(current["pointer_version"] if current else 0):failure("REVISION_CONFLICT","本地部署指针已变化",409)
        pending=[];seen=set()
        for target in selected:
            validate(target,"asset_ref")
            asset=resolve_asset(db,wid,target);gov=db.get(SemanticGovernance,(wid,asset["asset_id"]))
            if not gov or gov.revision!=asset["revision"] or gov.workflow_state!="DRAFT":failure("DRAFT_REQUIRED","本地草稿重建仅接受明确选择的当前DRAFT修订",409)
            pending.append(asset)
        while pending:
            asset=pending.pop();key=(asset["asset_id"],asset["revision"])
            if key in seen:continue
            seen.add(key)
            if not can_read_asset(db,wid,user,asset):failure("BLOCKED_PERMISSION","技术快照依赖当前不可读取",403)
            gov=db.get(SemanticGovernance,(wid,asset["asset_id"]))
            if gov and gov.revision==asset["revision"] and gov.workflow_state in ("WITHDRAWN","REJECTED"):failure("APPROVAL_STALE","技术快照依赖已撤回或退回",409)
            issues=reference_issues(db,wid,asset)
            if issues:failure("REFERENCE_INVALID","技术快照依赖引用不完整",422,issue_codes=issues)
            for _,kind,target in walk_refs(asset):
                if kind=="ASSET":pending.append(resolve_asset(db,wid,target))
        release=create_release(db,wid,user,{"scope":"local_validation","asset_refs":selected})
        db.flush()
        index=build_index(db,wid,user,release["release_ref"]);db.flush()
        activated=activate(db,wid,user,"local_validation",{"release_ref":release["release_ref"],"index_snapshot_ref":index["index_snapshot_ref"],"expected_pointer_version":expected})
        record_event(db,wid,user,"local_drafts.rebuilt",{"scope":"local_validation","reason":body["reason"].strip(),"selected_refs":selected,"dependency_count":len(seen),"release_id":release["release_ref"]["id"]})
        return {**activated,"release":release["release"],"release_ref":release["release_ref"],"index_snapshot_ref":index["index_snapshot_ref"],"validation_receipt":index["validation_receipt"],"selected_draft_refs":selected,"production_approved":False,"business_approval_created":False}
    return await command(request,db,wid,user,action)


@router.post("/retrieve")
async def retrieve_command(wid:int,request:Request,user=Depends(identity),db=Depends(db_session)):
    def action(b):
        # Public diagnostic retrieval never accepts a caller-selected deployment.
        intent=b.get("intent",b)
        bundle=retrieve(db,wid,user,b.get("run_id") or new_id("diagnostic_run"),b.get("requirement_id") or "diagnostic",intent)
        return {"bundle":bundle,"bundle_content_hash":sha(bundle),"scope":"local_validation","production_approved":False,"vector_enabled":False}
    return await command(request,db,wid,user,action)


@router.get("/users")
def semantic_users(wid:int,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    users=db.scalars(select(User).where(User.enabled==1)).all()
    visible=[]
    for u in users:
        try:workspace_access(db,u,wid)
        except HTTPException:continue
        visible.append({"id":u.id,"username":u.username,"display_name":u.display_name,"role":u.role})
    return envelope(wid,{"items":visible})


@router.get("/assignments")
def assignments(wid:int,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    rows=db.scalars(select(SemanticAssignment).where(SemanticAssignment.workspace_id==wid)).all()
    return envelope(wid,{"items":[{"user_id":r.user_id,"role":r.role,"asset_id":r.asset_id,"active":bool(r.active),"record_version":r.record_version} for r in rows if user.role=="admin" or r.user_id==user.id]})


@router.post("/assignments")
async def assignment_command(wid:int,request:Request,user=Depends(identity),db=Depends(db_session)):
    def action(b):
        if user.role!="admin":failure("BLOCKED_PERMISSION","仅管理员可分派治理职责",403)
        target=db.get(User,b.get("user_id"));role=b.get("role");aid=b.get("asset_id","*")
        if not target or not target.enabled or role not in ("STEWARD","BUSINESS_OWNER","DATA_OWNER","PUBLISHER") or not isinstance(aid,str):failure("SCHEMA_INVALID","身份或治理职责无效",422)
        workspace_access(db,target,wid)
        row=db.get(SemanticAssignment,(wid,target.id,role,aid))
        if row:
            cas_header(request,b.get("expected_record_version"))
            if b.get("expected_record_version")!=row.record_version:failure("REVISION_CONFLICT","职责分派已变化",409)
            row.active=int(bool(b.get("active",True)));row.record_version+=1;row.updated_at=now();row.assigned_by=user.id
        else:
            row=SemanticAssignment(workspace_id=wid,user_id=target.id,role=role,asset_id=aid,active=int(bool(b.get("active",True))),assigned_by=user.id,record_version=1);db.add(row)
        record_event(db,wid,user,"assignment.changed",{"user_id":target.id,"role":role,"asset_id":aid,"active":bool(row.active)})
        return {"assignment":{"user_id":row.user_id,"role":row.role,"asset_id":row.asset_id,"active":bool(row.active),"record_version":row.record_version}}
    return await command(request,db,wid,user,action)


@router.get("/policies")
def policies(wid:int,user=Depends(identity),db=Depends(db_session)):
    semantic_access(db,wid,user)
    rows=db.scalars(select(SemanticPolicy).where(SemanticPolicy.workspace_id==wid).order_by(SemanticPolicy.policy_id,SemanticPolicy.revision.desc())).all()
    return envelope(wid,{"items":[{"policy_ref":{"policy_id":r.policy_id,"revision":r.revision,"resolution":"RESOLVED"},"content":r.content,"content_hash":r.content_hash,"active":bool(r.active)} for r in rows if user.role=="admin" or policy_allows(r,user)]})


@router.post("/policies")
async def policy_command(wid:int,request:Request,user=Depends(identity),db=Depends(db_session)):
    def action(b):
        if user.role!="admin":failure("BLOCKED_PERMISSION","仅管理员可配置对象政策",403)
        pid=b.get("policy_id");ids=b.get("allowed_user_ids",[]);roles=b.get("allowed_roles",[])
        if not isinstance(pid,str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,150}",pid) or not isinstance(ids,list) or not isinstance(roles,list) or not set(roles).issubset({"admin","developer","viewer"}):failure("SCHEMA_INVALID","政策配置无效",422)
        for uid in ids:
            u=db.get(User,uid)
            if not u or not u.enabled:failure("SCHEMA_INVALID","政策用户身份无效",422)
            workspace_access(db,u,wid)
        latest=db.scalar(select(func.max(SemanticPolicy.revision)).where(SemanticPolicy.workspace_id==wid,SemanticPolicy.policy_id==pid)) or 0
        if latest:
            cas_header(request,b.get("expected_revision"))
            if b.get("expected_revision")!=latest:failure("REVISION_CONFLICT","政策已产生新修订",409)
        if b.get("allow_execution"):failure("POLICY_UNSUPPORTED","本版对象政策仅授权读取/治理，不能代替真实数据源行列执行策略",422)
        content={"policy_id":pid,"revision":latest+1,"allowed_user_ids":sorted(set(ids)),"allowed_roles":sorted(set(roles)),"allow_execution":False,"description":b.get("description") or "语义对象读取与治理政策"}
        row=SemanticPolicy(workspace_id=wid,policy_id=pid,revision=latest+1,content=content,content_hash=sha(content),active=1,created_by=user.id);db.add(row)
        record_event(db,wid,user,"policy.created",{"policy_id":pid,"revision":row.revision})
        return {"policy_ref":{"policy_id":pid,"revision":row.revision,"resolution":"RESOLVED"},"content":content,"content_hash":row.content_hash}
    return await command(request,db,wid,user,action)


@router.post("/policies/{policy_id}/revoke")
async def revoke_policy(wid:int,policy_id:str,request:Request,user=Depends(identity),db=Depends(db_session)):
    def action(b):
        if user.role!="admin":failure("BLOCKED_PERMISSION","仅管理员可撤销对象政策",403)
        row=db.get(SemanticPolicy,(wid,policy_id,b.get("revision")))
        if not row:failure("REFERENCE_INVALID","政策不可用",404)
        row.active=0;record_event(db,wid,user,"policy.revoked",{"policy_id":policy_id,"revision":row.revision})
        return {"revoked":True,"policy_id":policy_id,"revision":row.revision}
    return await command(request,db,wid,user,action)


@router.post("/sources/{asset_id}/archive")
async def archive_source(wid:int,asset_id:str,request:Request,user=Depends(identity),db=Depends(db_session)):
    def action(b):
        require_steward(db,wid,user,asset_id)
        row=db.get(SemanticRevision,(wid,asset_id,b.get("revision")))
        if not row or row.asset_type!="source" or not can_read_asset(db,wid,user,row.content):failure("REFERENCE_INVALID","来源不可用",404)
        raw=b.get("raw_text")
        if not isinstance(raw,str) or len(raw.encode())>2_000_000:failure("SCHEMA_INVALID","来源原文需要为2MB以内的JSON文本",422)
        try:parsed=json.loads(raw)
        except (ValueError,TypeError):failure("SCHEMA_INVALID","本版来源归档仅支持JSON原文（Evidence使用JSON_POINTER）",422)
        digest=hashlib.sha256(raw.encode()).hexdigest()
        if row.content["spec"]["content_sha256"]!=digest:failure("SOURCE_HASH_MISMATCH","归档原文字节hash与来源修订不一致",409)
        key=(wid,asset_id,digest)
        if not db.get(SemanticSourceBlob,key):db.add(SemanticSourceBlob(workspace_id=wid,source_id=asset_id,content_hash=digest,content={"raw_text":raw,"json":parsed}))
        record_event(db,wid,user,"source.archived",{"asset_id":asset_id,"content_hash":digest})
        return {"archived":True,"content_hash":digest}
    return await command(request,db,wid,user,action)
