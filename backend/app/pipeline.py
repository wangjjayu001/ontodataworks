"""v1.1 data engineering, governance and dataset service API (contract v1)."""
import copy
import csv
import hashlib
import io
import json
import secrets
import time
from datetime import date, datetime, timedelta
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select, func, text
from sqlglot import exp
from .db import Asset, Datasource, User, Audit, SessionLocal, now
from .security import identity, db_session, workspace_access
from .warehouse import read_query, table_refs
from .pipeline_models import *
from .pipeline_contracts import *
from .pipeline_catalog import owned, require_actor, source_for, digest, guard_asset
from .pipeline_execution import enqueue, reconcile, emit
from .pipeline_publication import make_plan, publish
from .pipeline_query import execute_read

router = APIRouter(prefix="/api/workspaces/{wid}/pipeline", tags=["pipeline-v1"])
service_router = APIRouter()


def out(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name != "token_hash"}


def audit(db, wid, user, action, detail):
    db.add(Audit(workspace_id=wid, actor=user.username, action="v1.1."+action, detail=detail))


def validate_config(db, wid, kind, raw, did=None):
    try: cfg = (ServiceConfig if kind == "service" else TaskConfig).model_validate(raw).model_dump(mode="json")
    except (ValidationError, ValueError) as exc: raise HTTPException(422, "任务配置无效：" + str(exc)[:1200])
    source = source_for(db, wid, cfg["datasource_id"])
    pins = {}
    for key in ("source_asset_id", "target_asset_id"):
        if cfg.get(key):
            pinned = owned(db, Asset, wid, cfg[key]); pins[str(pinned.id)] = pinned.fingerprint
    if kind == "service":
        tree = read_query(cfg["sql"])
        if set(p.name for p in tree.find_all(exp.Placeholder)) != set(cfg["parameters"]): raise HTTPException(400, "参数定义与 SQL 占位符不匹配")
    else:
        if len(set(cfg["dependencies"])) != len(cfg["dependencies"]) or did in cfg["dependencies"]: raise HTTPException(409, "依赖重复或循环")
        def visit(node, path):
            if node in path: raise HTTPException(409, "任务依赖形成循环")
            d = owned(db, Definition, wid, node)
            if d.deleted or d.kind == "service": raise HTTPException(409, "前置任务不可用")
            for parent in (cfg["dependencies"] if node == did else d.config.get("dependencies", [])): visit(parent, path | {node})
        for parent in cfg["dependencies"]: visit(parent, {did} if did else set())
        if kind != "collect":
            target = owned(db, Asset, wid, cfg["target_asset_id"])
            names = {c["name"] for c in target.columns}
            if target.state != "active": raise HTTPException(409, "目标表需重新采集")
            if kind == "quality" and target.datasource_id != source.id: raise HTTPException(400, "质量检查连接与目标不一致")
            if kind in {"sql", "sync"}:
                source_for(db, wid, target.datasource_id, "write")
                if kind == "sync":
                    origin = owned(db, Asset, wid, cfg["source_asset_id"])
                    if origin.datasource_id != source.id or not cfg["mapping"]: raise HTTPException(400, "同步来源或字段映射无效")
                    if (source.host, source.port, source.database, origin.namespace, origin.table_name) == (db.get(Datasource, target.datasource_id).host, db.get(Datasource, target.datasource_id).port, db.get(Datasource, target.datasource_id).database, target.namespace, target.table_name):
                        raise HTTPException(400, "同步源与目标不能是同一物理表")
                    if not set(cfg["mapping"]) <= {c["name"] for c in origin.columns}: raise HTTPException(400, "同步源字段不存在")
                    if len(set(cfg["mapping"].values())) != len(cfg["mapping"]): raise HTTPException(400, "目标映射字段不能重复")
                    cfg["columns"] = list(cfg["mapping"].values())
                    if cfg["watermark_column"]:
                        if cfg["strategy"] != "upsert" or cfg["watermark_column"] not in cfg["mapping"]: raise HTTPException(400, "增量水位必须映射且使用主键 upsert")
                        watermark_field = next(c for c in origin.columns if c["name"] == cfg["watermark_column"])
                        if watermark_field["nullable"]: raise HTTPException(400, "增量水位不允许 NULL")
                else:
                    if not cfg["sql"] or not cfg["columns"]: raise HTTPException(400, "SQL 任务需要 SELECT 和目标字段契约")
                    tree = read_query(cfg["sql"])
                    if any(p.name != "business_date" for p in tree.find_all(exp.Placeholder)): raise HTTPException(400, "仅支持 :business_date")
                    if cfg["watermark_column"]: raise HTTPException(400, "SQL 任务不支持自动推断增量水位")
                if not cfg["columns"] or not set(cfg["columns"]) <= names or len(set(cfg["columns"])) != len(cfg["columns"]): raise HTTPException(400, "目标字段契约不存在或重复")
                if cfg["strategy"] == "upsert":
                    actual_keys = [c["name"] for c in sorted(target.columns, key=lambda c: c.get("primary_key_position") or 0) if c.get("primary_key")]
                    if not cfg["keys"] or cfg["keys"] != actual_keys or not set(cfg["keys"]) <= set(cfg["columns"]): raise HTTPException(400, "upsert 键必须与目标物理主键一致并包含在输出中")
            for rule in cfg["rules"]:
                if rule["kind"] != "row_count" and rule["column"] not in names: raise HTTPException(400, "质量规则字段不属于目标")
                if rule["kind"] == "enum" and not rule["values"]: raise HTTPException(400, "枚举规则值不能为空")
                if rule["kind"] == "range" and rule["minimum"] is None and rule["maximum"] is None: raise HTTPException(400, "范围规则必须指定边界")
                if rule["kind"] == "reference":
                    reference = owned(db, Asset, wid, rule["reference_asset_id"])
                    if reference.datasource_id != target.datasource_id or rule["reference_column"] not in {c["name"] for c in reference.columns}: raise HTTPException(400, "引用规则需要同连接中的有效字段")
            if cfg["data_as_of_column"] and cfg["data_as_of_column"] not in names: raise HTTPException(400, "数据截止字段不存在")
        if cfg["schedule"]:
            cron_match(cfg["schedule"]["cron"], now())  # also validates unsupported cron syntax
    if kind in {"sql", "service"}:
        for ns, name in table_refs(tree):
            if ns not in source.schemas: raise HTTPException(403, "SQL 引用范围外 Schema")
            input_asset = db.scalar(select(Asset).where(Asset.workspace_id == wid, Asset.datasource_id == source.id, Asset.namespace == ns, Asset.table_name == name, Asset.state == "active"))
            if not input_asset: raise HTTPException(409, "SQL 输入表尚未采集")
            if kind=="sql":
                target=owned(db,Asset,wid,cfg["target_asset_id"]);target_source=db.get(Datasource,target.datasource_id)
                if (source.host,source.port,source.database,ns,name)==(target_source.host,target_source.port,target_source.database,target.namespace,target.table_name):
                    raise HTTPException(409,"SQL 加工不支持将目标表同时作为输入，请使用独立分层表")
            pins[str(input_asset.id)] = input_asset.fingerprint
    cfg["catalog_fingerprints"] = pins
    from .pipeline_catalog import enrollment_identity
    source_ids={source.id}|{owned(db,Asset,wid,int(aid)).datasource_id for aid in pins}
    cfg["connection_identities"]={str(sid):enrollment_identity(source_for(db,wid,sid)) for sid in source_ids}
    if kind != "service": check_standards(db, wid, cfg.get("standard_refs", []))
    return cfg


def check_standards(db, wid, references):
    for ref in references:
        if set(ref) != {"id", "revision", "content_hash"}: raise HTTPException(400, "标准引用必须固定 id/revision/content_hash")
        item = owned(db, GovernanceItem, wid, ref["id"])
        if item.kind != "standard" or item.deleted or item.status != "APPROVED" or item.revision != ref["revision"] or digest(item.content) != ref["content_hash"]:
            raise HTTPException(409, "引用标准已变化、撤回或尚未批准，需重新评审任务")
        if item.content.get("valid_until") and date.fromisoformat(item.content["valid_until"]) < now().date(): raise HTTPException(409, "引用标准已过期")


def revision(db, row, user):
    content = {"kind": row.kind, "name": row.name, "config": row.config, "enabled": row.enabled}
    db.add(DefinitionRevision(definition_id=row.id, revision=row.revision, content=content, content_hash=digest(content), actor_id=user.id))


@router.get("/overview")
def overview(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    rows = db.execute(select(PipelineRun.status, func.count()).where(PipelineRun.workspace_id == wid).group_by(PipelineRun.status)).all()
    return {"version": "1.1.0", "contract": "pipeline/1", "schema": 5, "runs": dict(rows),
        "definitions": db.scalar(select(func.count()).select_from(Definition).where(Definition.workspace_id == wid, Definition.deleted == False)),
        "open_issues": db.scalar(select(func.count()).select_from(GovernanceItem).where(GovernanceItem.workspace_id == wid, GovernanceItem.kind == "issue", GovernanceItem.status != "CLOSED", GovernanceItem.deleted == False)),
        "connectors": {"postgresql": {"collect": True, "read": True, "create_table": True, "add_nullable_column": True, "sync_full": True, "sync_watermark": True,
            "cdc": False, "drop_column": False, "cancel": "before_commit_or_queued", "cache": False}},
        "incremental_policy": "水位边界含等值并按主键覆盖；更早迟到数据需回补，源删除不自动传播"}


@router.get("/sources")
def sources(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    from .pipeline_catalog import enrollment_current
    return [{"id": s.id, "name": s.name, "purpose": (db.get(SourceUse, s.id).purpose if db.get(SourceUse, s.id) else "read"),
        "local_semantic_validation": enrollment_current(db,s)} for s in db.scalars(select(Datasource).where(Datasource.workspace_id == wid))]


@router.put("/sources/{sid}/local-validation")
def source_enrollment(wid: int, sid: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db,user,wid,True)
    if user.role != "admin": raise HTTPException(403,"仅管理员可登记本地语义验证源")
    if type(body.get("enabled")) is not bool: raise HTTPException(422,"enabled 必须为布尔值")
    from .pipeline_catalog import enrollment_identity, verify_readonly_source
    from .warehouse import connect
    source=source_for(db,wid,sid);usage=db.get(SourceUse,sid)
    if body["enabled"]:
        if usage and usage.purpose!="read": raise HTTPException(409,"语义验证源必须是独立读取连接")
        with connect(source) as conn:
            conn.execute("SET TRANSACTION READ ONLY"); verify_readonly_source(conn,source)
    row=db.get(SourceEnrollment,sid)
    if not row: row=SourceEnrollment(datasource_id=sid,workspace_id=wid);db.add(row)
    row.enabled=body["enabled"];row.identity_hash=enrollment_identity(source);row.actor_id=user.id;row.checked_at=now()
    audit(db,wid,user,"source.local_validation",f"source:{sid} enabled:{row.enabled}");db.commit()
    return {"datasource_id":sid,"enabled":row.enabled,"scope":"local_validation","production_approved":False}


@router.get("/actors")
def actors(wid: int, user=Depends(identity), db=Depends(db_session)):
    from .db import Membership
    require_actor(db, user, wid)
    members = set(db.scalars(select(Membership.user_id).where(Membership.workspace_id == wid)))
    return [{"id": u.id, "display_name": u.display_name, "role": u.role} for u in db.scalars(select(User).where(User.enabled == 1)) if u.role == "admin" or u.id in members]


@router.put("/sources/{sid}/purpose")
def source_purpose(wid: int, sid: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    if user.role != "admin": raise HTTPException(403, "仅管理员可登记连接用途")
    source = owned(db, Datasource, wid, sid); purpose = body.get("purpose")
    if purpose not in {"read", "write", "ddl"}: raise HTTPException(400, "用途只能为 read/write/ddl")
    if purpose != "read":
        from .warehouse import connect
        with connect(source) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            privileges = conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone()
        if not privileges or any(privileges): raise HTTPException(403, "写入和 DDL 连接必须使用无超级管理权限的专用账号")
    row = db.get(SourceUse, sid)
    if row: row.purpose = purpose; row.actor_id = user.id
    else: row = SourceUse(datasource_id=sid, workspace_id=wid, purpose=purpose, actor_id=user.id); db.add(row)
    audit(db, wid, user, "source.purpose", f"source:{sid} {purpose}"); db.commit(); return out(row)


@router.get("/definitions")
def definitions(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    return [out(d) for d in db.scalars(select(Definition).where(Definition.workspace_id == wid, Definition.deleted == False).order_by(Definition.id.desc()))]


@router.post("/definitions")
def create_definition(wid: int, body: DefinitionBody, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    cfg = validate_config(db, wid, body.kind, body.config)
    if body.kind == "service" and body.enabled: raise HTTPException(409, "服务须先测试再发布")
    row = Definition(workspace_id=wid, kind=body.kind, name=body.name, config=cfg, enabled=body.enabled, actor_id=user.id)
    db.add(row); db.flush(); revision(db, row, user); audit(db, wid, user, "definition.create", f"{row.id}:{row.kind}"); db.commit(); return out(row)


@router.put("/definitions/{did}")
def update_definition(wid: int, did: int, body: DefinitionBody, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); row = owned(db, Definition, wid, did)
    db.refresh(row, with_for_update=True)
    if row.deleted or row.kind != body.kind: raise HTTPException(409, "任务类型不可变或任务已删除")
    if row.revision != body.revision: raise HTTPException(409, "任务已更新，请刷新")
    if row.kind == "service" and (row.enabled or body.enabled): raise HTTPException(409, "已发布服务先下线；修改后重新测试发布")
    cfg = validate_config(db, wid, row.kind, body.config, did)
    row.name = body.name; row.config = cfg; row.enabled = body.enabled; row.revision += 1; row.updated_at = now()
    revision(db, row, user); audit(db, wid, user, "definition.update", f"{did} v{row.revision}"); db.commit(); return out(row)


@router.get("/definitions/{did}/revisions")
def revisions(wid: int, did: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid); owned(db, Definition, wid, did)
    return [out(r) for r in db.scalars(select(DefinitionRevision).where(DefinitionRevision.definition_id == did).order_by(DefinitionRevision.revision.desc()))]


@router.delete("/definitions/{did}")
def delete_definition(wid: int, did: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); row = owned(db, Definition, wid, did)
    if row.enabled: raise HTTPException(409, "先停用任务或下线服务")
    if db.scalar(select(PipelineRun.id).where(PipelineRun.definition_id == did, PipelineRun.status.in_(["QUEUED", "RUNNING", "UNKNOWN"]))): raise HTTPException(409, "任务存在待运行或待对账实例")
    for dep in db.scalars(select(Definition).where(Definition.workspace_id == wid, Definition.deleted == False)):
        if did in dep.config.get("dependencies", []) + dep.config.get("quality_definition_ids", []): raise HTTPException(409, "任务仍被下游引用")
    if row.kind == "service" and any(did in c.service_ids for c in db.scalars(select(ServiceConsumer).where(ServiceConsumer.workspace_id == wid, ServiceConsumer.enabled == True))): raise HTTPException(409, "服务仍有有效消费应用")
    row.deleted = True; audit(db, wid, user, "definition.delete", str(did)); db.commit(); return {"ok": True, "history_retained": True}


@router.post("/definitions/{did}/runs")
def create_run(wid: int, did: int, body: RunBody, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); row = owned(db, Definition, wid, did)
    run = enqueue(db, wid, user, row, body); db.commit(); return out(run)


@router.post("/definitions/{did}/backfill")
def backfill(wid: int, did: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); definition = owned(db, Definition, wid, did)
    try: start = date.fromisoformat(body["start"]); end = date.fromisoformat(body["end_exclusive"])
    except (KeyError, ValueError): raise HTTPException(400, "需指定开始日和结束日（不含）")
    if not 0 < (end-start).days <= 31: raise HTTPException(400, "每次回补限定 1 至 31 天")
    rows = []
    for offset in range((end-start).days):
        day = start+timedelta(days=offset); batch = "day:"+day.isoformat()
        run = enqueue(db, wid, user, definition, RunBody(request_key=f"backfill:{did}:{definition.revision}:{day}", batch_key=batch, business_date=day)); rows.append(out(run))
    db.commit(); return rows


@router.get("/runs")
def runs(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    visible=[]
    for r in db.scalars(select(PipelineRun).where(PipelineRun.workspace_id == wid).order_by(PipelineRun.created_at.desc()).limit(200)):
        try:
            for aid in r.snapshot.get("fingerprints",{}):guard_asset(db,wid,user,owned(db,Asset,wid,int(aid)),raw=True)
        except HTTPException as exc:
            if exc.status_code==403:continue
            raise
        visible.append(out(r))
    return visible


@router.get("/runs/{rid}")
def run_detail(wid: int, rid: str, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid); row = owned(db, PipelineRun, wid, rid)
    for aid in row.snapshot.get("fingerprints", {}): guard_asset(db, wid, user, owned(db, Asset, wid, int(aid)), raw=True)
    return {**out(row), "events": [out(e) for e in db.scalars(select(RunEvent).where(RunEvent.run_id == rid).order_by(RunEvent.id))]}


@router.post("/runs/{rid}/cancel")
def cancel_run(wid: int, rid: str, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); row = owned(db, PipelineRun, wid, rid); db.refresh(row, with_for_update=True)
    if row.status not in {"RUNNING", "QUEUED"}: raise HTTPException(409, "终态或未知运行不可取消")
    row.cancel_requested = True
    if row.status == "QUEUED": row.status = "CANCELLED"; row.completed_at = now()
    emit(db, row, "run.cancel_requested"); db.commit(); return out(row)


@router.post("/runs/{rid}/reconcile")
def reconcile_run(wid: int, rid: str, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); row = owned(db, PipelineRun, wid, rid)
    if row.status != "UNKNOWN": raise HTTPException(409, "仅 UNKNOWN 实例可对账")
    reconcile(db, row); audit(db, wid, user, "run.reconciled", rid); db.commit(); return out(row)


@router.post("/runs/{rid}/retry")
def retry_run(wid: int, rid: str, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); row = owned(db, PipelineRun, wid, rid); db.refresh(row, with_for_update=True)
    if row.status != "FAILED" or row.attempt >= 3: raise HTTPException(409, "仅确定失败且少于三次的实例可重试；未知结果先对账")
    row.status = "QUEUED"; row.error = ""; row.cancel_requested = False; emit(db, row, "run.retry"); db.commit(); return out(row)


@router.post("/definitions/{did}/debug")
def debug(wid: int, did: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); definition = owned(db, Definition, wid, did)
    if definition.kind != "sql": raise HTTPException(400, "调试仅支持 SQL 加工任务")
    from .pipeline_execution import bound_tree
    try: business_date = date.fromisoformat(body["business_date"]).isoformat()
    except (KeyError, ValueError): raise HTTPException(400, "调试需明确业务日期")
    result = execute_read(db, wid, user, definition.config["datasource_id"], bound_tree(definition.config["sql"], business_date).sql(dialect="postgres"), limit=100)
    db.commit(); return {**result, "debug_only": True, "target_written": False, "expected_columns": definition.config["columns"], "shape_matches": result["columns"] == definition.config["columns"]}


@router.get("/observations")
def observations(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    return [out(o) for o in db.scalars(select(CatalogObservation).where(CatalogObservation.workspace_id == wid).order_by(CatalogObservation.id.desc()).limit(50))]


@router.get("/batches")
def batches(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    return [out(o) for o in db.scalars(select(DataBatch).where(DataBatch.workspace_id == wid).order_by(DataBatch.committed_at.desc()).limit(100))]


@router.post("/publications/preview")
def preview_publication(wid: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    row = make_plan(db, wid, user, body.get("model_id"), body.get("datasource_id")); db.commit(); return out(row)


@router.post("/publications/{pid}/execute")
def execute_publication(wid: int, pid: str, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    if user.role != "admin": raise HTTPException(403, "物理发布需要管理员发布能力")
    row = owned(db, Publication, wid, pid); db.refresh(row, with_for_update=True)
    result = publish(db, wid, row); audit(db, wid, user, "publication.execute", f"{pid}:{result.status}"); db.commit(); return out(result)


@router.get("/publications")
def publications(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    return [out(r) for r in db.scalars(select(Publication).where(Publication.workspace_id == wid).order_by(Publication.created_at.desc()).limit(100))]


@router.post("/publications/batch")
def batch_publication(wid: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    if user.role != "admin": raise HTTPException(403, "物理发布需要管理员")
    ids = body.get("plan_ids", [])
    if not isinstance(ids, list) or not 0 < len(ids) <= 30: raise HTTPException(400, "批量发布 1 至 30 项")
    rows = []
    for pid in ids:
        try: rows.append(out(publish(db, wid, owned(db, Publication, wid, pid))))
        except HTTPException as exc: db.rollback(); rows.append({"id": pid, "status": "REJECTED", "error": exc.detail})
    return {"items": rows, "all_succeeded": all(r["status"] == "SUCCEEDED" for r in rows)}


def validate_item(db, wid, body, user, old=None):
    classes = {"issue": IssueContent, "standard": StandardContent, "analysis": AnalysisContent, "access_policy": AccessContent}
    raw_content = body.content
    if body.kind == "issue" and "category" not in raw_content:
        from .pipeline_issues import issue_category
        raw_content = {**raw_content, "category": issue_category(db, wid, old.content if old else raw_content)}
    try: content = classes[body.kind].model_validate(raw_content).model_dump(mode="json")
    except ValidationError as exc: raise HTTPException(422, str(exc)[:1000])
    if content.get("asset_id"): owned(db, Asset, wid, content["asset_id"])
    if content.get("owner_id"):
        owner = db.get(User, content["owner_id"])
        require_actor(db, owner, wid)
    if body.kind == "issue":
        from .pipeline_issues import validate_issue
        content = validate_issue(db, wid, body, content, user, old)
    elif body.kind == "analysis":
        source_for(db, wid, content["datasource_id"]); read_query(content["sql"])
    elif body.kind == "access_policy":
        if user.role != "admin": raise HTTPException(403, "只有管理员可以维护对象、行列政策")
        asset = owned(db, Asset, wid, content["asset_id"]); names = {c["name"] for c in asset.columns}
        if content["allowed_columns"] is not None and not set(content["allowed_columns"]) <= names: raise HTTPException(400, "政策字段无效")
        if not set(content["row_equals"]) <= names: raise HTTPException(400, "行过滤字段无效")
        for uid in content["user_ids"]: require_actor(db, db.get(User, uid), wid)
    elif body.kind == "standard":
        for binding in content["bindings"]:
            if set(binding) != {"asset_id", "column"}: raise HTTPException(400, "标准绑定需包含 asset_id,column")
            asset = owned(db, Asset, wid, binding["asset_id"])
            if binding["column"] not in {c["name"] for c in asset.columns}: raise HTTPException(400, "标准绑定字段不存在")
        if body.status == "APPROVED":
            if not old or old.status != "REVIEW" or content != old.content: raise HTTPException(409, "先提交固定内容评审，批准时不得修改口径")
            if user.id != content["owner_id"] or user.id == old.actor_id: raise HTTPException(403, "标准需由不同于提交人的真实 Owner 批准")
            if not content["source_ref"]: raise HTTPException(409, "批准标准需要可回源的语义证据引用")
            from .semantic import resolve_asset, can_read_asset, validity_active, verify_archived_source, valid_approvals
            evidence = resolve_asset(db, wid, content["source_ref"])
            if evidence["asset_type"] not in {"source", "claim"} or not can_read_asset(db, wid, user, evidence) or not validity_active(evidence["validity"]): raise HTTPException(409, "标准证据不可用")
            candidates=[evidence] if evidence["asset_type"]=="source" else [resolve_asset(db,wid,r) for r in evidence.get("provenance_refs",[]) if r.get("asset_type")=="source"]
            if not any(s["spec"].get("source_kind")=="BUSINESS_SPEC" and s["spec"].get("authority")=="BUSINESS_APPROVED" and
                can_read_asset(db,wid,user,s) and validity_active(s["validity"]) and verify_archived_source(db,wid,s) and
                any(a["reviewer_role"]=="BUSINESS_OWNER" for a in valid_approvals(db,wid,s)) for s in candidates):
                raise HTTPException(409,"标准需要已归档且真实业务审核有效的规范，技术QA不能作为业务批准依据")
            if content["valid_until"] and date.fromisoformat(content["valid_until"]) < now().date(): raise HTTPException(409, "标准已过期")
    return content


@router.get("/governance")
def governance(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    from .pipeline_issues import issue_category
    items = []
    for row in db.scalars(select(GovernanceItem).where(GovernanceItem.workspace_id == wid, GovernanceItem.deleted == False).order_by(GovernanceItem.id.desc())):
        item = out(row)
        if row.kind == "issue": item["content"] = {**row.content, "category": issue_category(db, wid, row.content)}
        items.append(item)
    return items


@router.post("/governance")
def create_item(wid: int, body: GovernanceBody, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); content = validate_item(db, wid, body, user)
    row = GovernanceItem(workspace_id=wid, kind=body.kind, name=body.name, status=body.status, content=content, actor_id=user.id)
    db.add(row); db.flush(); row.history = [{"revision": 1, "status": row.status, "content": content, "actor_id": user.id, "at": now().isoformat()}]
    audit(db, wid, user, "governance.create", f"{row.id}:{row.kind}"); db.commit(); return out(row)


@router.put("/governance/{iid}")
def update_item(wid: int, iid: int, body: GovernanceBody, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); row = owned(db, GovernanceItem, wid, iid); db.refresh(row, with_for_update=True)
    if row.deleted or row.kind != body.kind or row.revision != body.revision: raise HTTPException(409, "对象已更新或不可修改")
    if row.kind == "standard" and row.status == "APPROVED" and body.status == "APPROVED": raise HTTPException(409, "批准标准变更必须重新评审")
    content = validate_item(db, wid, body, user, row)
    row.revision += 1; row.content = content; row.name = body.name; row.status = body.status; row.updated_at = now()
    row.history = row.history + [{"revision": row.revision, "status": row.status, "content": content, "actor_id": user.id, "at": now().isoformat()}]
    audit(db, wid, user, "governance.update", f"{iid} v{row.revision}"); db.commit(); return out(row)


@router.delete("/governance/{iid}")
def delete_item(wid: int, iid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); row = owned(db, GovernanceItem, wid, iid)
    if row.kind == "access_policy" and user.role != "admin": raise HTTPException(403, "仅管理员可删除政策")
    if row.kind == "issue" and row.status != "CLOSED": raise HTTPException(409, "未关闭问题应保留处理记录")
    if row.kind == "standard" and row.status == "APPROVED": raise HTTPException(409, "先撤回标准并复核引用")
    row.deleted = True; audit(db, wid, user, "governance.delete", str(iid)); db.commit(); return {"ok": True, "history_retained": True}


@router.post("/governance/{iid}/rerun")
def rerun_analysis(wid: int, iid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid); row = owned(db, GovernanceItem, wid, iid)
    if row.kind != "analysis" or row.deleted: raise HTTPException(404, "分析不存在")
    result = execute_read(db, wid, user, row.content["datasource_id"], row.content["sql"]); db.commit(); return result


@router.post("/export")
def export_csv(wid: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    result = execute_read(db, wid, user, body.get("datasource_id"), body.get("sql", ""))
    if body.get("format") == "xlsx":
        from .pipeline_export import workbook
        content = workbook(result)
        audit(db, wid, user, "result.export.xlsx", f"query:{result['id']}"); db.commit()
        return Response(content,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":'attachment; filename="analysis.xlsx"',"X-Query-Id":str(result["id"])})
    buffer = io.StringIO(); writer = csv.writer(buffer)
    def cell(value):
        if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}: return "'"+value
        return value
    writer.writerow(result["columns"]); writer.writerows([[cell(v) for v in row] for row in result["rows"]])
    audit(db, wid, user, "result.export", f"query:{result['id']}"); db.commit()
    return Response("\ufeff"+buffer.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="analysis.csv"', "X-Query-Id": str(result["id"])})


@router.get("/impact/{aid}")
def impact(wid: int, aid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid); asset = owned(db, Asset, wid, aid); guard_asset(db, wid, user, asset)
    from .pipeline_catalog import physical_aliases
    affected = set(physical_aliases(db, wid, asset)); definitions = []; changed = True
    rows = list(db.scalars(select(Definition).where(Definition.workspace_id == wid, Definition.deleted == False)))
    while changed:
        changed = False
        for d in rows:
            if d.id in [r["id"] for r in definitions]: continue
            inputs = {d.config.get("source_asset_id"), d.config.get("target_asset_id")}
            if d.config.get("sql"):
                for ns, name in table_refs(read_query(d.config["sql"])):
                    a = db.scalar(select(Asset).where(Asset.workspace_id == wid, Asset.datasource_id == d.config["datasource_id"], Asset.namespace == ns, Asset.table_name == name))
                    if a: inputs.add(a.id)
            if inputs & affected or set(d.config.get("dependencies", [])) & {r["id"] for r in definitions}:
                definitions.append({"id": d.id, "name": d.name, "kind": d.kind, "revision": d.revision})
                if d.config.get("target_asset_id"): affected.update(physical_aliases(db, wid, db.get(Asset,d.config["target_asset_id"])))
                changed = True
    consumers = [{"id": c.id, "name": c.name, "service_ids": c.service_ids} for c in db.scalars(select(ServiceConsumer).where(ServiceConsumer.workspace_id == wid)) if set(c.service_ids) & {d["id"] for d in definitions}]
    from .pipeline_impact import semantic_impact
    semantic = semantic_impact(db, wid, user, affected)
    models = [{"model_id": p.model_id, "model_revision": p.model_revision, "publication_id": p.id} for p in db.scalars(select(Publication).where(Publication.workspace_id == wid)) if p.evidence.get("asset_id") in affected]
    return {"asset_id": aid, "affected_asset_ids": sorted(affected), "definitions": definitions, "consumers": consumers,
        "semantic_revisions": semantic, "models": models}


@router.post("/definitions/{did}/test")
def test_service(wid: int, did: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); service = owned(db, Definition, wid, did)
    if service.kind != "service" or service.deleted: raise HTTPException(400, "请选择数据服务")
    service_contract_current(db,service)
    params = service_parameters(service.config, body.get("parameters", {}))
    result = execute_read(db, wid, user, service.config["datasource_id"], service.config["sql"], params, service.config["max_rows"])
    db.add(ServiceCall(id=uuid4().hex, workspace_id=wid, service_id=did, revision=service.revision, consumer_id=None, status="TESTED", duration_ms=result["duration_ms"], evidence={"query_id": result["id"], "contract_hash": digest(service.config)}))
    db.commit(); return result


@router.post("/definitions/{did}/release")
def release_service(wid: int, did: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    if user.role != "admin": raise HTTPException(403, "服务发布需管理员")
    service = owned(db, Definition, wid, did); db.refresh(service, with_for_update=True)
    if service.kind != "service" or service.deleted: raise HTTPException(400, "请选择数据服务")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool): raise HTTPException(400, "enabled 必须是布尔值")
    if enabled and not db.scalar(select(ServiceCall.id).where(ServiceCall.service_id == did, ServiceCall.revision == service.revision, ServiceCall.status == "TESTED")): raise HTTPException(409, "当前服务版本尚未测试成功")
    service.enabled = enabled; audit(db, wid, user, "service.release", f"{did} v{service.revision} {enabled}"); db.commit(); return out(service)


def service_parameters(cfg, values):
    if not isinstance(values, dict) or set(values) != set(cfg["parameters"]): raise HTTPException(400, "请求参数与契约不匹配")
    for key, definition in cfg["parameters"].items():
        v = values[key]; kind = definition["type"]
        valid = (kind == "integer" and type(v) is int) or (kind == "number" and type(v) in {int, float}) or (kind == "boolean" and type(v) is bool) or (kind in {"date", "string"} and isinstance(v, str))
        if not valid: raise HTTPException(400, f"{key} 参数类型不正确")
        if kind == "date":
            try: date.fromisoformat(v)
            except ValueError: raise HTTPException(400, f"{key} 日期无效")
        if isinstance(v, str) and len(v) > definition["max_length"]: raise HTTPException(400, "参数过长")
        if type(v) in {int, float} and ((definition["minimum"] is not None and v < definition["minimum"]) or (definition["maximum"] is not None and v > definition["maximum"])): raise HTTPException(400, "数值参数超出范围")
        if definition["allowed_values"] and v not in definition["allowed_values"]: raise HTTPException(400, "参数不在允许值内")
    return values


def service_contract_current(db, service, config=None):
    from .pipeline_catalog import check_connections
    check_connections(db,service.workspace_id,config or service.config)
    for aid, fingerprint in (config or service.config)["catalog_fingerprints"].items():
        asset = owned(db, Asset, service.workspace_id, int(aid))
        if asset.state != "active" or asset.fingerprint != fingerprint:
            raise HTTPException(409, "服务固定的输入结构已变化，请新建契约修订并测试")


@router.get("/consumers")
def consumers(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    return [{**out(c),"revisions":{str(g.service_id):g.revision for g in db.scalars(select(ServiceGrant).where(ServiceGrant.consumer_id==c.id))}} for c in db.scalars(select(ServiceConsumer).where(ServiceConsumer.workspace_id == wid))]


@router.post("/consumers")
def create_consumer(wid: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    if user.role != "admin": raise HTTPException(403, "消费应用需管理员授权")
    principal = db.get(User, body.get("principal_id")); require_actor(db, principal, wid)
    if principal.role != "viewer": raise HTTPException(403, "消费应用必须绑定只读服务身份")
    ids = body.get("service_ids", [])
    if not isinstance(ids, list) or not 0 < len(ids) <= 30: raise HTTPException(400, "需指定服务范围")
    for did in ids:
        service = owned(db, Definition, wid, did)
        if service.kind != "service" or service.deleted: raise HTTPException(400, "授权服务无效")
    name = body.get("name", "")
    if not isinstance(name, str) or not 0 < len(name) <= 150: raise HTTPException(400, "应用名称无效")
    token = secrets.token_urlsafe(40)
    row = ServiceConsumer(workspace_id=wid, name=name, principal_id=principal.id, service_ids=ids, token_hash=hashlib.sha256(token.encode()).hexdigest())
    db.add(row); db.flush()
    for did in ids: db.add(ServiceGrant(consumer_id=row.id,service_id=did,revision=db.get(Definition,did).revision))
    audit(db, wid, user, "consumer.create", name); db.commit(); return {**out(row), "token": token, "token_shown_once": True}


@router.post("/consumers/{cid}/rotate")
def rotate_consumer(wid: int, cid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    if user.role != "admin": raise HTTPException(403, "轮换凭据需管理员")
    row = owned(db, ServiceConsumer, wid, cid); token = secrets.token_urlsafe(40); row.token_hash = hashlib.sha256(token.encode()).hexdigest()
    audit(db, wid, user, "consumer.rotate", str(cid)); db.commit(); return {"id": cid, "token": token, "token_shown_once": True}


@router.put("/consumers/{cid}")
def update_consumer(wid: int, cid: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    if user.role != "admin": raise HTTPException(403, "消费授权需管理员维护")
    row = owned(db, ServiceConsumer, wid, cid); db.refresh(row, with_for_update=True)
    name = body.get("name"); ids = body.get("service_ids")
    if not isinstance(name, str) or not 0 < len(name) <= 150 or not isinstance(ids, list) or len(ids) > 30:
        raise HTTPException(400, "应用名称或服务范围无效")
    for did in ids:
        service = owned(db, Definition, wid, did)
        if service.kind != "service" or service.deleted: raise HTTPException(400, "授权服务无效")
        pinned = body.get("revisions", {}).get(str(did), service.revision)
        revision = db.get(DefinitionRevision,(did,pinned))
        if not revision or revision.content["kind"] != "service": raise HTTPException(400, "固定服务版本无效")
        grant = db.get(ServiceGrant,(cid,did))
        if grant:
            if str(did) in body.get("revisions",{}): grant.revision = pinned
        else: db.add(ServiceGrant(consumer_id=cid,service_id=did,revision=pinned))
    row.name = name; row.service_ids = ids
    audit(db, wid, user, "consumer.update", f"{cid}: services={ids}"); db.commit(); return out(row)


@router.delete("/consumers/{cid}")
def revoke_consumer(wid: int, cid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True)
    if user.role != "admin": raise HTTPException(403, "撤销应用需管理员")
    row = owned(db, ServiceConsumer, wid, cid); row.enabled = False
    audit(db, wid, user, "consumer.revoke", str(cid)); db.commit(); return {"ok": True}


@router.get("/service-calls")
def calls(wid: int, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid)
    return [out(c) for c in db.scalars(select(ServiceCall).where(ServiceCall.workspace_id == wid).order_by(ServiceCall.created_at.desc()).limit(200))]


@service_router.post("/api/data-services/{did}/invoke")
def invoke_service(did: int, body: dict, request: Request, db=Depends(db_session)):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    consumer = db.scalar(select(ServiceConsumer).where(ServiceConsumer.token_hash == hashlib.sha256(token.encode()).hexdigest()).with_for_update())
    if not consumer or not consumer.enabled or did not in consumer.service_ids: raise HTTPException(401, "消费凭据无效或未授权此服务")
    service = owned(db, Definition, consumer.workspace_id, did); user = db.get(User, consumer.principal_id)
    require_actor(db, user, consumer.workspace_id)
    if user.role != "viewer" or not service.enabled or service.deleted or service.kind != "service": raise HTTPException(403, "服务或消费身份已停用")
    started = time.monotonic()
    grant = db.get(ServiceGrant,(consumer.id,did))
    if not grant: raise HTTPException(403, "消费授权尚未固定服务修订，请重新登记")
    pinned = db.get(DefinitionRevision,(did,grant.revision))
    if not pinned or pinned.content["kind"] != "service": raise HTTPException(409, "消费契约修订不可用")
    cfg = pinned.content["config"]
    service_contract_current(db, service, cfg)
    count = db.scalar(select(func.count()).select_from(ServiceCall).where(ServiceCall.consumer_id == consumer.id, ServiceCall.service_id == did, ServiceCall.created_at > now()-timedelta(minutes=1)))
    if count >= cfg["calls_per_minute"]: raise HTTPException(429, "调用频率超过预算")
    call = ServiceCall(id=uuid4().hex, workspace_id=consumer.workspace_id, service_id=did, revision=grant.revision, consumer_id=consumer.id, status="RUNNING")
    db.add(call); db.flush()
    try:
        result = execute_read(db, service.workspace_id, user, cfg["datasource_id"], cfg["sql"], service_parameters(cfg, body.get("parameters", {})), cfg["max_rows"])
        call.status = "SUCCEEDED"; call.evidence = {"query_id": result["id"], "contract_hash": digest(cfg), "data_batches": result["data_batches"]}
        call.duration_ms = int((time.monotonic()-started)*1000); db.commit()
        return {"call_id": call.id, "service_id": did, "revision": grant.revision, "contract_version": cfg["contract_version"], "cache": "disabled", **result}
    except HTTPException as exc:
        call.status = "FAILED"; call.evidence = {"http_status": exc.status_code}; call.duration_ms = int((time.monotonic()-started)*1000); db.commit(); raise


def cron_match(expression, current):
    parts = expression.split()
    if len(parts) != 5: raise HTTPException(400, "cron 需要五段：分 时 日 月 周")
    values = [current.minute, current.hour, current.day, current.month, (current.weekday()+1) % 7]
    bounds = [(0,59),(0,23),(1,31),(1,12),(0,6)]
    matched = []
    for part, value, (lo, hi) in zip(parts, values, bounds):
        allowed = set()
        for item in part.split(","):
            try:
                base, slash, step = item.partition("/"); step = int(step) if slash else 1
                if not 1 <= step <= hi-lo+1: raise ValueError()
                if base == "*": start, end = lo, hi
                elif "-" in base: start, end = map(int, base.split("-"))
                else: start = end = int(base)
                if not lo <= start <= end <= hi: raise ValueError()
                allowed.update(range(start, end+1, step))
            except ValueError: raise HTTPException(400, "不支持的 cron 数值或范围")
        matched.append(value in allowed)
    # Explicit AND semantics for day-of-month/day-of-week; shown in the UI.
    return all(matched)


def schedule_tick():
    from zoneinfo import ZoneInfo
    with SessionLocal() as db:
        for definition in db.scalars(select(Definition).where(Definition.enabled == True, Definition.deleted == False, Definition.kind != "service").with_for_update(skip_locked=True)):
            schedule = definition.config.get("schedule")
            if not schedule: continue
            current = now().astimezone(ZoneInfo(schedule["timezone"]))
            if not cron_match(schedule["cron"], current): continue
            slot = current.strftime("%Y%m%dT%H%M%z")
            user = db.get(User, definition.actor_id)
            try:
                enqueue(db, definition.workspace_id, user, definition, RunBody(request_key=f"cron:{definition.id}:{definition.revision}:{slot}", batch_key="day:"+current.date().isoformat(), business_date=current.date()))
            except HTTPException: continue
        db.commit()
