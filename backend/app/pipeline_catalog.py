"""Observed catalog, immutable history and committed batch query gates."""
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import select
from .db import Asset, Datasource, User, now
from .pipeline_models import CatalogObservation, GovernanceItem, DataBatch, Definition, SourceUse, PipelineRun, SourceEnrollment
from .security import workspace_access
from .warehouse import inspect_catalog


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()


def owned(db, cls, wid, key):
    row = db.get(cls, key)
    if not row or row.workspace_id != wid:
        raise HTTPException(404, "资源不存在")
    return row


def require_actor(db, user, wid, write=False):
    if not user or not user.enabled:
        raise HTTPException(403, "执行身份不可用")
    workspace_access(db, user, wid, write)


def source_for(db, wid, sid, purpose="read"):
    source = owned(db, Datasource, wid, sid)
    usage = db.get(SourceUse, sid)
    if purpose != "read" and (not usage or usage.purpose != purpose):
        raise HTTPException(403, f"数据源必须登记为 {purpose} 专用连接")
    return source


def enrollment_identity(source):
    return digest([source.workspace_id, source.host, source.port, source.database, source.username, source.schemas, source.secret])


def enrollment_current(db, source):
    row = db.get(SourceEnrollment, source.id)
    usage = db.get(SourceUse, source.id)
    return bool(row and row.enabled and row.workspace_id == source.workspace_id and
        row.identity_hash == enrollment_identity(source) and (not usage or usage.purpose == "read"))


def check_connections(db,wid,cfg):
    pins=cfg.get("connection_identities",{})
    if not pins: raise HTTPException(409,"任务尚未固定连接身份，请保存新修订")
    for sid,fingerprint in pins.items():
        if enrollment_identity(owned(db,Datasource,wid,int(sid)))!=fingerprint:
            raise HTTPException(409,"连接身份或凭据已变化，需复核并保存新任务修订")


def verify_readonly_source(conn, source):
    privileges = conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
    writable = conn.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema=ANY(%s) AND table_type='BASE TABLE' AND
        (has_table_privilege(quote_ident(table_schema)||'.'||quote_ident(table_name),'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER'))""", (source.schemas,)).fetchone()[0]
    ddl = conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname=ANY(%s) AND has_schema_privilege(oid,'CREATE')", (source.schemas,)).fetchone()[0]
    if not privileges or any(privileges) or writable or ddl:
        raise HTTPException(403, "语义验证源须使用无管理、建表和业务表写权限的独立只读账号")


def physical_aliases(db, wid, asset):
    source = owned(db, Datasource, wid, asset.datasource_id)
    ids = [s.id for s in db.scalars(select(Datasource).where(Datasource.workspace_id == wid))
        if (s.host, s.port, s.database) == (source.host, source.port, source.database)]
    return list(db.scalars(select(Asset.id).where(Asset.workspace_id == wid, Asset.datasource_id.in_(ids),
        Asset.namespace == asset.namespace, Asset.table_name == asset.table_name)))


def policies(db, wid, aid):
    aliases = physical_aliases(db, wid, owned(db, Asset, wid, aid))
    return [p for p in db.scalars(select(GovernanceItem).where(GovernanceItem.workspace_id == wid,
        GovernanceItem.kind == "access_policy", GovernanceItem.deleted == False)) if p.content.get("asset_id") in aliases]


def guard_asset(db, wid, user, asset, raw=False):
    require_actor(db, user, wid)
    for policy in policies(db, wid, asset.id):
        if user.id not in policy.content["user_ids"] or policy.content.get("deny"):
            raise HTTPException(403, "物理资产的当前对象政策不允许访问")
        if raw and (policy.content.get("allowed_columns") is not None or policy.content.get("row_equals")):
            raise HTTPException(403, "当前路径不能安全应用此行列政策，请使用受控 SQL 查询")
    # Existing semantic physical policies stay authoritative after catalog refresh.
    from .semantic_models import SemanticSnapshot
    from .semantic import can_read_physical
    latest = db.scalar(select(SemanticSnapshot).where(SemanticSnapshot.workspace_id == wid).order_by(SemanticSnapshot.created_at.desc()).limit(1))
    if latest:
        for ds in latest.content.get("datasets", []):
            if str(ds.get("datasource_id")) == str(asset.datasource_id) and ds.get("namespace") == asset.namespace and ds.get("physical_name") == asset.table_name:
                ref = {"workspace_id": wid, "catalog_snapshot_id": latest.id, "dataset_id": ds["dataset_id"], "datasource_id": str(asset.datasource_id)}
                if not can_read_physical(db, wid, user, ref):
                    raise HTTPException(403, "当前语义目录政策不允许访问")
                for field in ds.get("fields", []):
                    if not can_read_physical(db, wid, user, {**ref, "field_id": field["field_id"]}):
                        raise HTTPException(403, "存在不可读取的语义字段政策，当前 SQL 入口不能扩大访问")


def batch_gate(db, wid, asset, enforce_quality=True):
    aliases = physical_aliases(db, wid, asset)
    for run in db.scalars(select(PipelineRun).where(PipelineRun.workspace_id==wid,PipelineRun.status.in_(["RUNNING","UNKNOWN"]))):
        if run.snapshot["kind"] in {"sync","sql"} and run.snapshot["config"].get("target_asset_id") in aliases:
            raise HTTPException(409,"目标正在提交或存在待对账写入，暂不交付可能错配批次的结果")
    managed = [d for d in db.scalars(select(Definition).where(Definition.workspace_id == wid,
        Definition.kind.in_(["sync", "sql"]))) if d.config.get("target_asset_id") in aliases]
    if not managed:
        return {"asset_id": asset.id, "batch": None, "data_as_of": None, "status": "UNMANAGED"}
    batch = db.scalar(select(DataBatch).where(DataBatch.workspace_id == wid, DataBatch.asset_id.in_(aliases)).order_by(DataBatch.committed_at.desc()).limit(1))
    if not batch:
        raise HTTPException(409, "受管目标尚无通过质量门禁的已提交批次")
    ttl = min([d.config["freshness_minutes"] for d in managed if d.config.get("freshness_minutes")] or [525600])
    if (now() - batch.committed_at).total_seconds() > ttl * 60:
        raise HTTPException(409, "数据批次超过更新 SLA，已阻断消费")
    seen = set()
    for check in db.scalars(select(PipelineRun).where(PipelineRun.workspace_id==wid,PipelineRun.status.in_(["SUCCEEDED","FAILED"])).order_by(PipelineRun.completed_at.desc())):
        if check.snapshot["kind"]=="quality" and check.evidence.get("checked_batch_run_id")==batch.run_id:
            if check.definition_id in seen: continue
            seen.add(check.definition_id)
            if enforce_quality and check.status=="FAILED": raise HTTPException(409,"当前已提交批次的独立质量检查失败，需修复并复测")
    return {"asset_id": asset.id, "batch": batch.batch_key, "run_id": batch.run_id, "data_as_of": batch.data_as_of,
        "committed_at": batch.committed_at.isoformat(), "freshness_basis":"committed_at", "status": "PASS"}


def observe_catalog(db, source, run_id=None, actor_id=None):
    items = inspect_catalog(source)
    existing = {(a.namespace, a.table_name): a for a in db.scalars(select(Asset).where(Asset.datasource_id == source.id))}
    changes = []
    for item in items:
        key = (item["namespace"], item["table_name"])
        row = existing.pop(key, None)
        if row:
            if row.fingerprint != item["fingerprint"] or row.state != "active":
                old = {c["name"]: c for c in row.columns}; new = {c["name"]: c for c in item["columns"]}
                removed = sorted(set(old) - set(new)); altered = sorted(n for n in set(old) & set(new) if old[n] != new[n])
                changes.append({"asset_id": row.id, "object": ".".join(key), "kind": "STRUCTURE_CHANGED",
                    "severity": "breaking" if removed or altered else "additive", "removed": removed, "altered": altered,
                    "added": sorted(set(new)-set(old)), "before": row.fingerprint, "after": item["fingerprint"]})
            for name, value in item.items():
                if name not in {"name", "description"}: setattr(row, name, value)
            row.state = "active"; row.collected_at = now()
        else:
            row = Asset(workspace_id=source.workspace_id, datasource_id=source.id, **item)
            db.add(row); db.flush()
            changes.append({"asset_id": row.id, "object": ".".join(key), "kind": "ADDED", "severity": "additive"})
    for row in existing.values():
        if row.state != "missing":
            changes.append({"asset_id": row.id, "object": row.namespace + "." + row.table_name, "kind": "MISSING", "severity": "breaking"})
        row.state = "missing"
    observation = CatalogObservation(workspace_id=source.workspace_id, datasource_id=source.id, content=items,
        content_hash=digest(items), changes=changes, run_id=run_id)
    db.add(observation); source.last_collected_at = now(); db.flush()
    # Local governance inbox is transactional and idempotent per observation.
    for change in changes:
        if change["severity"] != "breaking": continue
        db.add(GovernanceItem(workspace_id=source.workspace_id, kind="issue", name="结构变化：" + change["object"],
            actor_id=actor_id, content={"category": "schema", "description": json.dumps(change, ensure_ascii=False), "asset_id": change["asset_id"],
                "event_key": f"catalog:{observation.id}:{change['asset_id']}"}, history=[]))
    return {"observation_id": observation.id, "tables": len(items), "columns": sum(len(x["columns"]) for x in items), "changes": changes}
