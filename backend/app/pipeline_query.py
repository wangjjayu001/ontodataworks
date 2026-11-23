"""Shared SQL/dataset service execution and v1.1 consumption gates."""
import time
from decimal import Decimal
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope
from .db import Asset, QueryRun, Lineage, now
from .warehouse import read_query, table_refs, inspect_catalog, connect
from .pipeline_catalog import source_for, require_actor, guard_asset, batch_gate, policies, digest
from .observability import trace_id


def execute_read(db, wid, user, sid, query, parameters=None, limit=1000):
    require_actor(db, user, wid)
    source = source_for(db, wid, sid)
    tree = read_query(query)
    parameters = parameters or {}
    placeholders = list(tree.find_all(exp.Placeholder))
    if set(p.name for p in placeholders) != set(parameters): raise HTTPException(400, "参数与查询模板不匹配")
    for p in placeholders: p.replace(exp.convert(parameters[p.name]))
    refs = table_refs(tree)
    current = {(a["namespace"], a["table_name"]): a for a in inspect_catalog(source)}
    verified = []; batches = []; restrictions = {}
    for ns, name in refs:
        if ns not in source.schemas: raise HTTPException(403, "Schema 超出连接范围")
        asset = db.scalar(select(Asset).where(Asset.workspace_id == wid, Asset.datasource_id == sid, Asset.namespace == ns, Asset.table_name == name))
        if not asset or asset.state != "active" or (ns, name) not in current or current[(ns, name)]["fingerprint"] != asset.fingerprint:
            raise HTTPException(409, "真实结构与目录不一致，请采集并复核")
        guard_asset(db, wid, user, asset); batches.append(batch_gate(db, wid, asset)); verified.append(asset)
        rules = policies(db, wid, asset.id)
        allowed = {c["name"] for c in asset.columns}; row_conditions = []
        for policy in rules:
            content = policy.content
            if content.get("allowed_columns") is not None: allowed &= set(content["allowed_columns"])
            for col, value in content.get("row_equals", {}).items():
                row_conditions.append(exp.EQ(this=exp.column(col), expression=exp.convert(value)))
        if rules:
            if not allowed: raise HTTPException(403, "当前政策没有可读取字段")
            wrapped = exp.select(*[exp.column(n, quoted=True) for n in sorted(allowed)]).from_(exp.Table(this=exp.to_identifier(name, quoted=True), db=exp.to_identifier(ns, quoted=True)))
            for condition in row_conditions: wrapped = wrapped.where(condition)
            restrictions[(ns, name)] = wrapped
    # Rewrite each physical reference, including nested SQL, before sending to DB.
    physical_tables = {id(table):table for scope in traverse_scope(tree) for table in scope.sources.values() if isinstance(table,exp.Table)}
    for table in physical_tables.values():
        key = (table.db or "public", table.name)
        if key in restrictions:
            if table.catalog: raise HTTPException(403, "不允许跨数据库限定名")
            table.replace(exp.Subquery(this=restrictions[key].copy(), alias=exp.TableAlias(this=exp.to_identifier(table.alias or table.name, quoted=True))))
    started = time.monotonic()
    run = QueryRun(workspace_id=wid, datasource_id=sid, actor=user.username, sql=query, status="running")
    db.add(run); db.flush()
    try:
        with connect(source) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
            for asset in sorted(verified,key=lambda a:(a.namespace,a.table_name)):
                conn.execute("SELECT pg_advisory_xact_lock_shared(%s)",(int(digest([source.database,asset.namespace,asset.table_name])[:15],16),))
            batches=[batch_gate(db,wid,asset) for asset in verified]
            cursor = conn.execute(tree.sql(dialect="postgres")); values = cursor.fetchmany(limit + 1); columns = [d.name for d in cursor.description]
        # Recheck policy and identity at result delivery.
        db.expire_all(); require_actor(db, user, wid)
        for asset in verified: guard_asset(db, wid, user, asset)
        result = {"id": run.id, "query_id": run.id, "columns": columns, "rows": jsonable_encoder(values[:limit], custom_encoder={Decimal: str}),
            "trace_id": trace_id.get(),
            "row_count": min(len(values), limit), "truncated": len(values) > limit, "limit": limit,
            "duration_ms": int((time.monotonic() - started) * 1000), "verified_sources": len(verified),
            "data_batches": batches, "executed_at": now().isoformat(), "no_data": len(values) == 0}
        run.status = "success"; run.row_count = result["row_count"]; run.duration_ms = result["duration_ms"]
        for asset in verified:
            db.add(Lineage(workspace_id=wid, query_id=run.id, asset_id=asset.id, source=asset.namespace + "." + asset.table_name,
                target=f"query:{run.id}", evidence={"origin": "shared_controlled_executor", "level": "table_to_query_result", "sql_sha256": digest(query),
                    "metadata_fingerprint": asset.fingerprint, "metadata_collected_at": asset.collected_at.isoformat(),
                    "physical_schema_verified_at": now().isoformat(), "query_status": "success", "data_batches": batches}))
        return result
    except HTTPException:
        run.status = "failed"; run.error = "交付时授权校验未通过"; raise
    except Exception:
        run.status = "failed"; run.error = "只读查询执行失败，请检查字段、参数和数据库权限"
        raise HTTPException(400, run.error)
