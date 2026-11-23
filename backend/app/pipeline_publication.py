"""Physical publication plans pin the reviewed model, connection and structure."""
from uuid import uuid4
from sqlalchemy import select
from fastapi import HTTPException
from psycopg import sql
from sqlglot import exp
from .db import Resource, Asset
from .warehouse import model_ddl, inspect_catalog, connect
from .pipeline_models import Publication
from .pipeline_catalog import owned, source_for, digest, observe_catalog
from .pipeline_execution import create_receipt_table


def normalized_type(value):
    return exp.DataType.build(value, dialect="postgres").sql(dialect="postgres")


def source_identity(source):
    return digest([source.host, source.port, source.database, source.username, source.schemas])


def matches_plan(plan, actual):
    if not actual: return False
    expected={f["name"]:(normalized_type(f["type"]),False if f["name"] in plan["primary_keys"] else f.get("nullable",True)) for f in plan["expected_fields"]}
    observed={f["name"]:(normalized_type(f["type"]),f["nullable"]) for f in actual["columns"]}
    keys=[f["name"] for f in sorted(actual["columns"],key=lambda f:f.get("primary_key_position") or 0) if f.get("primary_key")]
    return expected==observed and keys==plan["primary_keys"] and observed_foreign_keys(actual)==expected_foreign_keys(plan.get("foreign_keys",[]))


def observed_foreign_keys(actual):
    return {(f["name"],ref["namespace"],ref["table"],ref["column"]) for f in actual["columns"] for ref in f.get("foreign_keys",[])}


def expected_foreign_keys(foreign_keys):
    return {(local,fk["reference_schema"],fk["reference_table"],remote) for fk in foreign_keys for local,remote in zip(fk["columns"],fk["reference_columns"])}


def make_plan(db, wid, user, model_id, sid):
    model = owned(db, Resource, wid, model_id); source = source_for(db, wid, sid, "ddl")
    if model.kind != "model": raise HTTPException(400, "只支持模型发布")
    namespace = model.spec.get("namespace", "public")
    if namespace not in source.schemas: raise HTTPException(403, "目标 Schema 不在连接范围")
    existing = next((a for a in inspect_catalog(source) if a["namespace"] == namespace and a["table_name"] == model.code), None)
    expected = model.spec["fields"]; commands = []; mode = "CREATE"
    if existing:
        mode = "ALTER"; old = {c["name"]: c for c in existing["columns"]}; new = {c["name"]: c for c in expected}
        if set(old)-set(new): raise HTTPException(409, "删除字段属于破坏性变更，请先处理依赖和独立重建策略")
        for name in set(old) & set(new):
            nullable = False if name in model.spec.get("primary_keys", []) else new[name].get("nullable", True)
            if normalized_type(old[name]["type"]) != normalized_type(new[name]["type"]) or old[name]["nullable"] != nullable:
                raise HTTPException(409, "改类型或可空约束需独立迁移，禁止自动变更")
        keys = [c["name"] for c in sorted(existing["columns"], key=lambda c: c.get("primary_key_position") or 0) if c.get("primary_key")]
        if keys != model.spec.get("primary_keys", []): raise HTTPException(409, "主键变更需独立迁移")
        if observed_foreign_keys(existing)!=expected_foreign_keys(model.spec.get("foreign_keys",[])):raise HTTPException(409,"外键变更需独立迁移")
        for name in set(new)-set(old):
            field = new[name]
            if field.get("nullable", True) is False: raise HTTPException(409, "已有表仅支持新增可空字段")
            commands.append(sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(sql.Identifier(namespace, model.code), sql.Identifier(name), sql.SQL(normalized_type(field["type"])) ).as_string())
    else: commands = [model_ddl(model)]
    plan = {"model_id": model.id, "model_revision": model.revision, "model_hash": digest([model.code, model.spec]), "namespace": namespace,
        "table_name": model.code, "mode": mode, "commands": commands, "before_fingerprint": existing["fingerprint"] if existing else None,
        "source_identity": source_identity(source), "expected_fields": expected, "primary_keys": model.spec.get("primary_keys", []),"foreign_keys":model.spec.get("foreign_keys",[])}
    row = Publication(id=uuid4().hex, workspace_id=wid, model_id=model.id, model_revision=model.revision, datasource_id=sid, actor_id=user.id, plan=plan)
    db.add(row); db.flush(); return row


def publish(db, wid, row):
    if row.status == "SUCCEEDED": return row
    if row.status != "PREVIEW": raise HTTPException(409, "本计划已执行或需对账，请重新预检")
    model = owned(db, Resource, wid, row.model_id); source = source_for(db, wid, row.datasource_id, "ddl"); plan = row.plan
    if model.revision != row.model_revision or digest([model.code, model.spec]) != plan["model_hash"] or source_identity(source) != plan["source_identity"]:
        raise HTTPException(409, "模型或连接已变化，请重新预检")
    current = next((a for a in inspect_catalog(source) if a["namespace"] == plan["namespace"] and a["table_name"] == plan["table_name"]), None)
    if (current["fingerprint"] if current else None) != plan["before_fingerprint"]: raise HTTPException(409, "物理目标已变化，请重新预检")
    row.status = "RUNNING"; db.commit()
    try:
        with connect(source) as conn:
            for command in plan["commands"]: conn.execute(command)
            create_receipt_table(conn, plan["namespace"])
        actual = next((a for a in inspect_catalog(source) if a["namespace"] == plan["namespace"] and a["table_name"] == plan["table_name"]), None)
        if not matches_plan(plan, actual):
            row.status = "UNKNOWN"; row.evidence = {"error": "DDL 已返回，实际结构与计划不符"}
        else:
            observation = observe_catalog(db, source, actor_id=row.actor_id)
            asset = db.scalar(select(Asset).where(Asset.datasource_id == source.id, Asset.namespace == plan["namespace"], Asset.table_name == plan["table_name"]))
            row.status = "SUCCEEDED"; row.evidence = {"asset_id": asset.id, "fingerprint": asset.fingerprint, **observation}
    except Exception as exc:
        import psycopg
        row.status = "FAILED" if isinstance(exc, psycopg.Error) and not isinstance(exc, psycopg.OperationalError) else "UNKNOWN"
        row.evidence = {"error": "物理发布失败，需核对实际表结构", "exception_type": type(exc).__name__}
    db.commit(); return row


def reconcile_publication(db,wid,row):
    if row.status not in {"UNKNOWN","RUNNING"}: raise HTTPException(409,"仅运行中或待对账计划可核对")
    source=source_for(db,wid,row.datasource_id,"ddl");plan=row.plan
    if source_identity(source)!=plan["source_identity"]: raise HTTPException(409,"连接身份已变化，不能核对原发布")
    actual=next((a for a in inspect_catalog(source) if a["namespace"]==plan["namespace"] and a["table_name"]==plan["table_name"]),None)
    if not matches_plan(plan,actual): row.status="UNKNOWN";row.evidence={"error":"实际结构仍不匹配，保留未知，不重复执行 DDL"}
    else:
        observation=observe_catalog(db,source,actor_id=row.actor_id)
        asset=db.scalar(select(Asset).where(Asset.datasource_id==source.id,Asset.namespace==plan["namespace"],Asset.table_name==plan["table_name"]))
        row.status="SUCCEEDED";row.evidence={**observation,"asset_id":asset.id,"fingerprint":asset.fingerprint,"reconciled":True}
    db.commit();return row
