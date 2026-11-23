"""Bounded PostgreSQL pipeline execution with atomic receipt + target commit.

The workbench database is not in the target transaction. A target-side receipt
is the authority after a lost acknowledgement; workers reconcile before replay.
"""
import copy
import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
import psycopg
from psycopg import sql
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy import text
from .db import SessionLocal, Asset, Datasource, User, now
from .security import workspace_access
from .warehouse import connect, inspect_catalog, read_query, table_refs
from .pipeline_models import Definition, DefinitionRevision, PipelineRun, DataBatch, RunEvent, GovernanceItem
from .pipeline_catalog import owned, source_for, require_actor, observe_catalog, digest, guard_asset, batch_gate
from .observability import trace_id


def emit(db, run, kind, detail=None):
    db.add(RunEvent(run_id=run.id, kind=kind, detail=detail or {}))


def verified_asset(db, wid, aid):
    asset = owned(db, Asset, wid, aid)
    source = owned(db, Datasource, wid, asset.datasource_id)
    current = next((a for a in inspect_catalog(source) if a["namespace"] == asset.namespace and a["table_name"] == asset.table_name), None)
    if asset.state != "active" or not current or current["fingerprint"] != asset.fingerprint:
        raise HTTPException(409, "目录结构已变化，请采集并复核任务版本")
    return asset


def relation(asset):
    return sql.Identifier(asset.namespace, asset.table_name)


def receipt_table(asset):
    return sql.Identifier(asset.namespace, "_wb_batch_receipt")


def create_receipt_table(conn, namespace):
    conn.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} (run_id text PRIMARY KEY, content_hash text NOT NULL, evidence jsonb NOT NULL, committed_at timestamptz NOT NULL DEFAULT now())").format(sql.Identifier(namespace, "_wb_batch_receipt")))


def evaluate_rules(conn, table, rules, db, wid, source_id):
    results = []
    for index, rule in enumerate(rules):
        kind = rule["kind"]; col = sql.Identifier(rule.get("column") or "unused")
        where = sql.SQL("FALSE"); params = []
        if kind == "not_null": where = sql.SQL("{} IS NULL").format(col)
        elif kind == "unique":
            count = conn.execute(sql.SQL("SELECT COALESCE(sum(n-1),0) FROM (SELECT count(*) n FROM {} GROUP BY {} HAVING count(*)>1) d").format(table, col)).fetchone()[0]
            results.append({"index": index, "rule": rule, "status": "PASS" if count == 0 else "FAIL", "violations": int(count)}); continue
        elif kind == "range":
            parts = [sql.SQL("{} IS NULL").format(col)]
            for key, op in (("minimum", "<"), ("maximum", ">")):
                if rule.get(key) is not None:
                    parts.append(sql.SQL("{} " + op + " %s").format(col)); params.append(rule[key])
            where = sql.SQL(" OR ").join(parts)
        elif kind == "enum": where = sql.SQL("{} IS NULL OR NOT ({} = ANY(%s))").format(col, col); params = [rule["values"]]
        elif kind == "row_count":
            count = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(table)).fetchone()[0]
            ok = (rule.get("minimum") is None or count >= rule["minimum"]) and (rule.get("maximum") is None or count <= rule["maximum"])
            results.append({"index": index, "rule": rule, "status": "PASS" if ok else "FAIL", "observed_rows": count}); continue
        elif kind == "reference":
            ref = owned(db, Asset, wid, rule["reference_asset_id"])
            if ref.datasource_id != source_id: raise HTTPException(400, "引用完整性规则仅支持同一连接内已采集资产")
            where = sql.SQL("{} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {} ref WHERE ref.{} = checked.{})").format(col, relation(ref), sql.Identifier(rule["reference_column"]), col)
        else: raise HTTPException(400, "不支持的规则类型")
        count = conn.execute(sql.SQL("SELECT count(*) FROM {} checked WHERE ").format(table) + where, params).fetchone()[0]
        results.append({"index": index, "rule": rule, "status": "PASS" if count == 0 else "FAIL", "violations": count})
    return results


def bound_tree(query, business_date):
    from sqlglot import exp
    tree = read_query(query)
    for placeholder in list(tree.find_all(exp.Placeholder)):
        if placeholder.name != "business_date": raise HTTPException(400, "加工仅支持 :business_date 参数")
        placeholder.replace(exp.Cast(this=exp.Literal.string(business_date), to=exp.DataType.build("DATE")))
    return tree


def fixed_rules(db, wid, config):
    rules = copy.deepcopy(config["rules"]); versions = []
    for did in config.get("quality_definition_ids", []):
        definition = owned(db, Definition, wid, did)
        if definition.kind != "quality" or definition.deleted or definition.config["target_asset_id"] != config["target_asset_id"]:
            raise HTTPException(409, "质量任务与加工目标不匹配")
        rules.extend(definition.config["rules"]); versions.append({"definition_id": did, "revision": definition.revision})
    return rules, versions


def enqueue(db, wid, user, definition, body):
    require_actor(db, user, wid, True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest([wid, body.request_key])[:15], 16)})
    if definition.deleted or definition.kind == "service": raise HTTPException(409, "任务不可执行")
    old = db.scalar(select(PipelineRun).where(PipelineRun.workspace_id == wid, PipelineRun.request_key == body.request_key))
    if old:
        if (old.definition_id, old.revision, old.batch_key, old.business_date) != (definition.id, definition.revision, body.batch_key, body.business_date.isoformat()):
            raise HTTPException(409, "幂等键已经用于不同任务、版本或批次")
        return old
    active = list(db.scalars(select(PipelineRun).where(PipelineRun.workspace_id == wid, PipelineRun.status.in_(["QUEUED", "RUNNING"])) ))
    if len(active) >= 100 or sum(r.actor_id == user.id for r in active) >= 50:
        raise HTTPException(429, "工作空间或身份的待执行预算已满")
    config = copy.deepcopy(definition.config)
    from .pipeline_catalog import check_connections
    check_connections(db,wid,config)
    from .pipeline import check_standards
    check_standards(db, wid, config.get("standard_refs", []))
    rules, versions = fixed_rules(db, wid, config)
    pins = copy.deepcopy(config.get("catalog_fingerprints", {}))
    for key in ("source_asset_id", "target_asset_id"):
        if config.get(key):
            asset = owned(db, Asset, wid, config[key])
            if pins.get(str(asset.id)) != asset.fingerprint: raise HTTPException(409, "任务固定目录已变化，需保存新修订后运行")
    if definition.kind == "sql":
        for ns, name in table_refs(read_query(config["sql"])):
            asset = db.scalar(select(Asset).where(Asset.workspace_id == wid, Asset.datasource_id == config["datasource_id"], Asset.namespace == ns, Asset.table_name == name))
            if not asset: raise HTTPException(409, "输入表尚未采集")
            if pins.get(str(asset.id)) != asset.fingerprint: raise HTTPException(409, "SQL 输入固定目录已变化，需修订任务")
    dependencies = []
    for did in config["dependencies"]:
        dep = owned(db, Definition, wid, did)
        if dep.deleted: raise HTTPException(409, "前置任务已删除")
        dependencies.append({"id": did, "revision": dep.revision})
    run = PipelineRun(id=uuid4().hex, workspace_id=wid, definition_id=definition.id, revision=definition.revision,
        actor_id=user.id, batch_key=body.batch_key, request_key=body.request_key, business_date=body.business_date.isoformat(),
        snapshot={"trace_id": trace_id.get(), "kind": definition.kind, "name": definition.name, "config": config, "rules": rules,
            "quality_versions": versions, "fingerprints": pins, "dependencies": dependencies})
    db.add(run); db.flush(); emit(db, run, "run.queued", {"revision": definition.revision, "batch_key": run.batch_key})
    return run


def execute(run_id):
    with SessionLocal() as db:
        run = db.get(PipelineRun, run_id); cfg = run.snapshot["config"]; wid = run.workspace_id
        user = db.get(User, run.actor_id); require_actor(db, user, wid, True)
        from .pipeline_catalog import check_connections
        check_connections(db,wid,cfg)
        from .pipeline import check_standards
        check_standards(db, wid, cfg.get("standard_refs", []))
        if run.cancel_requested:
            finish(db, run, "CANCELLED", {}); return
        for key, fingerprint in run.snapshot["fingerprints"].items():
            asset = verified_asset(db, wid, int(key))
            if asset.fingerprint != fingerprint: raise HTTPException(409, "固定任务结构已过期，不得使用新目录解释旧任务")
            guard_asset(db, wid, user, asset, raw=True)
        kind = run.snapshot["kind"]
        source = source_for(db, wid, cfg["datasource_id"])
        if kind == "collect":
            result = observe_catalog(db, source, run.id, user.id); finish(db, run, "SUCCEEDED", result); return
        target = owned(db, Asset, wid, cfg["target_asset_id"])
        if kind == "quality":
            with connect(source) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
                conn.execute("SELECT pg_advisory_xact_lock_shared(%s)", (int(digest([source.database,target.namespace,target.table_name])[:15],16),))
                observed_batch = batch_gate(db, wid, target, enforce_quality=False)
                checks = evaluate_rules(conn, relation(target), run.snapshot["rules"], db, wid, source.id)
            status = "FAILED" if any(c["status"] != "PASS" and c["rule"]["severity"] == "error" for c in checks) else "SUCCEEDED"
            finish(db, run, status, {"quality": checks, "asset_id": target.id, "checked_batch_run_id":observed_batch.get("run_id")}); return
        writer = source_for(db, wid, target.datasource_id, "write")
        # Every writer locks the target before reading the previous committed watermark.
        with connect(writer) as conn:
            conn.execute(sql.SQL("SELECT pg_advisory_xact_lock(%s)"), (int(digest([writer.database, target.namespace, target.table_name])[:15], 16),))
            prior = conn.execute(sql.SQL("SELECT content_hash,evidence FROM {} WHERE run_id=%s").format(receipt_table(target)), (run.id,)).fetchone()
            if prior:
                if prior[0] != digest(run.snapshot): raise HTTPException(409, "目标回执内容不匹配")
                finish(db, run, "SUCCEEDED", prior[1], target); return
            watermark = None
            if cfg.get("watermark_column"):
                prior_batch = db.scalar(select(DataBatch).where(DataBatch.asset_id == target.id).order_by(DataBatch.committed_at.desc()).limit(1))
                if prior_batch:
                    if prior_batch.evidence.get("definition_id") != run.definition_id:
                        raise HTTPException(409, "目标有其他写入任务，水位不可混用")
                    watermark = prior_batch.evidence.get("watermark")
            input_assets = []
            with connect(source) as reader:
                reader.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
                if kind == "sync":
                    origin = owned(db, Asset, wid, cfg["source_asset_id"]); input_assets.append(origin)
                    reader.execute("SELECT pg_advisory_xact_lock_shared(%s)",(int(digest([source.database,origin.namespace,origin.table_name])[:15],16),))
                    input_batches={origin.id:batch_gate(db,wid,origin)}
                    names = list(cfg["mapping"])
                    statement = sql.SQL("SELECT {} FROM {}").format(sql.SQL(",").join(map(sql.Identifier, names)), relation(origin))
                    params = []
                    if watermark is not None:
                        statement += sql.SQL(" WHERE {} >= %s").format(sql.Identifier(cfg["watermark_column"])); params.append(watermark)
                    if cfg.get("watermark_column"): statement += sql.SQL(" ORDER BY {}").format(sql.Identifier(cfg["watermark_column"]))
                    statement += sql.SQL(" LIMIT %s"); params.append(cfg["max_rows"] + 1)
                    result = reader.execute(statement, params); columns = [cfg["mapping"][n] for n in names]
                else:
                    tree = bound_tree(cfg["sql"], run.business_date)
                    for ns, name in table_refs(tree):
                        asset = db.scalar(select(Asset).where(Asset.workspace_id == wid, Asset.datasource_id == source.id, Asset.namespace == ns, Asset.table_name == name))
                        if not asset: raise HTTPException(409, "输入资产不存在")
                        reader.execute("SELECT pg_advisory_xact_lock_shared(%s)",(int(digest([source.database,ns,name])[:15],16),))
                        input_assets.append(asset)
                    input_batches={a.id:batch_gate(db,wid,a) for a in input_assets}
                    result = reader.execute(tree.sql(dialect="postgres")); columns = [d.name for d in result.description]
                values = result.fetchmany(cfg["max_rows"] + 1)
            if len(values) > cfg["max_rows"]: raise HTTPException(409, "抽取超出批次行数上限；批次未提交，水位未推进")
            if kind == "sql" and columns != cfg["columns"]: raise HTTPException(409, "SELECT 输出字段与目标契约不一致")
            stage = sql.Identifier("_wb_stage_" + run.id[:16])
            conn.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP").format(stage, relation(target)))
            if values:
                with conn.cursor() as cursor:
                    cursor.executemany(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(stage, sql.SQL(",").join(map(sql.Identifier, columns)), sql.SQL(",").join(sql.Placeholder() for _ in columns)), values)
            checks = evaluate_rules(conn, stage, run.snapshot["rules"], db, wid, writer.id)
            if any(c["status"] != "PASS" and c["rule"]["severity"] == "error" for c in checks):
                conn.rollback(); finish(db, run, "FAILED", {"quality": checks, "committed": False, "watermark_advanced": False}); return
            check_cancel(run.id)
            if cfg["strategy"] == "replace": conn.execute(sql.SQL("DELETE FROM {}").format(relation(target)))
            statement = sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {}").format(relation(target), sql.SQL(",").join(map(sql.Identifier, columns)), sql.SQL(",").join(map(sql.Identifier, columns)), stage)
            if cfg["strategy"] == "upsert":
                updates = [n for n in columns if n not in cfg["keys"]]
                action = sql.SQL("DO UPDATE SET ") + sql.SQL(",").join(sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(n), sql.Identifier(n)) for n in updates) if updates else sql.SQL("DO NOTHING")
                statement += sql.SQL(" ON CONFLICT ({}) ").format(sql.SQL(",").join(map(sql.Identifier, cfg["keys"]))) + action
            conn.execute(statement)
            actual = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(relation(target))).fetchone()[0]
            projected = sql.SQL(",").join(map(sql.Identifier, columns))
            mismatches = conn.execute(sql.SQL("SELECT count(*) FROM (SELECT {} FROM {} EXCEPT SELECT {} FROM {}) delta").format(projected, stage, projected, relation(target))).fetchone()[0]
            if mismatches or (cfg["strategy"] == "replace" and actual != len(values)):
                raise HTTPException(409, "落地对账不一致，事务已回滚且水位不推进")
            # Recheck constraints on the complete target, including incremental data.
            final_checks = evaluate_rules(conn, relation(target), run.snapshot["rules"], db, wid, writer.id)
            if any(c["status"] != "PASS" and c["rule"]["severity"] == "error" for c in final_checks):
                conn.rollback(); finish(db, run, "FAILED", {"quality": final_checks, "committed": False, "watermark_advanced": False}); return
            data_as_of = None
            if cfg.get("data_as_of_column"):
                value = conn.execute(sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(cfg["data_as_of_column"]), relation(target))).fetchone()[0]
                data_as_of = str(value) if value is not None else None
            new_watermark = watermark
            if cfg.get("watermark_column") and values:
                position = list(cfg["mapping"]).index(cfg["watermark_column"])
                new_watermark = str(max(row[position] for row in values))
            evidence = {"definition_id": run.definition_id, "revision": run.revision, "input_rows": len(values), "target_rows": actual,
                "quality": final_checks, "quality_versions": run.snapshot["quality_versions"], "watermark": new_watermark,
                "reconciliation": {"stage_rows": len(values), "unmatched_stage_rows": mismatches, "target_rows": actual},
                "watermark_before": watermark, "data_as_of": data_as_of, "committed": True,
                "inputs": [{"asset_id": a.id, "fingerprint": a.fingerprint, "batch": input_batches[a.id]} for a in input_assets],
                "output_asset_id": target.id, "target_fingerprint": target.fingerprint, "business_date": run.business_date,
                "batch_key": run.batch_key, "strategy": cfg["strategy"], "deletion_policy": "replace_snapshot" if cfg["strategy"] == "replace" else "source_deletes_not_propagated",
                "column_lineage": column_lineage(cfg["sql"]) if kind == "sql" else {"coverage": "EXACT_MAPPING", "fields": [{"source_column": a, "target_column": b} for a, b in cfg["mapping"].items()]},
                "incremental_boundary": "inclusive_watermark_upsert; older late rows require full backfill" if cfg.get("watermark_column") else None}
            evidence = jsonable_encoder(evidence, custom_encoder={Decimal: str})
            conn.execute(sql.SQL("INSERT INTO {} (run_id,content_hash,evidence) VALUES (%s,%s,%s::jsonb)").format(receipt_table(target)), (run.id, digest(run.snapshot), json.dumps(evidence)))
        # Source commit acknowledged. Persist batch + success + outbox in one local transaction.
        finish(db, run, "SUCCEEDED", evidence, target)


def check_cancel(run_id):
    with SessionLocal() as db:
        run = db.get(PipelineRun, run_id)
        require_actor(db, db.get(User, run.actor_id), run.workspace_id, True)
        from .pipeline_catalog import check_connections
        check_connections(db,run.workspace_id,run.snapshot["config"])
        for aid in run.snapshot.get("fingerprints",{}):guard_asset(db,run.workspace_id,db.get(User,run.actor_id),owned(db,Asset,run.workspace_id,int(aid)),raw=True)
        target=owned(db,Asset,run.workspace_id,run.snapshot["config"]["target_asset_id"])
        source_for(db,run.workspace_id,target.datasource_id,"write")
        if run.cancel_requested: raise HTTPException(409, "CANCELLED_BEFORE_COMMIT")
        if run.status != "RUNNING" or not run.lease_until or run.lease_until < now():
            raise HTTPException(409, "执行租约已失效，禁止旧进程提交")


def finish(db, run, status, evidence, target=None):
    run.status = status; run.evidence = evidence; run.completed_at = now(); run.lease_until = None
    if target and not db.get(DataBatch, run.id):
        db.add(DataBatch(run_id=run.id, workspace_id=run.workspace_id, asset_id=target.id, batch_key=run.batch_key,
            data_as_of=evidence.get("data_as_of"), evidence=evidence))
    emit(db, run, "run." + status.lower(), {"batch_key": run.batch_key, "output_asset_id": evidence.get("output_asset_id")})
    db.commit()


def reconcile(db, run):
    cfg = run.snapshot["config"]
    if run.snapshot["kind"] not in {"sync", "sql"}:
        raise HTTPException(409, "非写入运行没有目标回执；请确认后新建运行")
    target = owned(db, Asset, run.workspace_id, cfg["target_asset_id"])
    writer = source_for(db, run.workspace_id, target.datasource_id, "write")
    with connect(writer) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (int(digest([writer.database, target.namespace, target.table_name])[:15], 16),))
        receipt = conn.execute(sql.SQL("SELECT content_hash,evidence FROM {} WHERE run_id=%s").format(receipt_table(target)), (run.id,)).fetchone()
        if not receipt:
            run.error = "持有目标互斥锁核对未发现提交回执，确认没有已提交批次；旧租约禁止提交"
            finish(db, run, "FAILED", {"committed": False, "reconciliation": "NO_RECEIPT_UNDER_TARGET_LOCK"})
            return
    if receipt[0] != digest(run.snapshot): raise HTTPException(409, "回执内容不匹配")
    finish(db, run, "SUCCEEDED", receipt[1], target)


def claim():
    with SessionLocal() as db:
        for expired in db.scalars(select(PipelineRun).where(PipelineRun.status == "RUNNING", PipelineRun.lease_until < now()).with_for_update(skip_locked=True)):
            expired.status = "UNKNOWN"; expired.error = "工作租约过期，先核对目标回执"; emit(db, expired, "run.unknown")
        jobs = db.scalars(select(PipelineRun).where(PipelineRun.status == "QUEUED").order_by(PipelineRun.created_at).with_for_update(skip_locked=True).limit(50)).all()
        selected = None
        for run in jobs:
            if run.cancel_requested: finish(db, run, "CANCELLED", {}); continue
            dependencies = run.snapshot["dependencies"]
            ready = True
            for dep in dependencies:
                upstream = db.scalar(select(PipelineRun).where(PipelineRun.workspace_id == run.workspace_id, PipelineRun.definition_id == dep["id"],
                    PipelineRun.revision == dep["revision"], PipelineRun.batch_key == run.batch_key, PipelineRun.business_date == run.business_date,
                    PipelineRun.status == "SUCCEEDED").limit(1))
                if not upstream: ready = False; break
            if ready:
                run.status = "RUNNING"; run.attempt += 1; run.lease_until = now() + timedelta(minutes=5)
                emit(db, run, "run.started", {"attempt": run.attempt}); selected = run.id; break
        db.commit(); return selected


def consume_outbox():
    with SessionLocal() as db:
        events = db.scalars(select(RunEvent).where(RunEvent.delivered == False).order_by(RunEvent.id).with_for_update(skip_locked=True).limit(50)).all()
        for event in events:
            run = db.get(PipelineRun, event.run_id)
            if event.kind == "run.failed":
                db.add(GovernanceItem(workspace_id=run.workspace_id, kind="issue", name="运行失败：" + run.snapshot["name"], actor_id=run.actor_id,
                    content={"category": "quality" if run.snapshot["kind"] == "quality" or any(c["status"] != "PASS" and c["rule"]["severity"] == "error" for c in run.evidence.get("quality", [])) else "task", "asset_id": run.snapshot["config"].get("target_asset_id"), "description": run.error or "质量检查未通过", "run_id": run.id, "event_key": f"run-event:{event.id}"}, history=[]))
            event.delivered = True
        db.commit()


def tick():
    job_id = claim()
    if job_id:
        try: execute(job_id)
        except Exception as exc:
            with SessionLocal() as db:
                from sqlalchemy.exc import SQLAlchemyError
                run = db.get(PipelineRun, job_id)
                unknown = isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError, SQLAlchemyError))
                run.error = str(exc.detail) if isinstance(exc, HTTPException) else "数据库执行失败：" + type(exc).__name__
                status = "UNKNOWN" if unknown else "CANCELLED" if run.error == "CANCELLED_BEFORE_COMMIT" else "FAILED"
                finish(db, run, status, {"committed": None if unknown else False})
    consume_outbox()
    return bool(job_id)


def column_lineage(query):
    from sqlglot import exp
    tree = read_query(query)
    # This intentionally exposes only supported direct projections. CTE/UNION
    # resolution is left UNKNOWN instead of attributing a column to a guess.
    if not isinstance(tree, exp.Select) or tree.args.get("with") or any(isinstance(n, (exp.Subquery, exp.Star)) for n in tree.walk()):
        return {"coverage": "UNKNOWN", "fields": [], "reason": "复杂作用域或通配符需要单独解析"}
    tables = {t.alias_or_name: (t.db or "public")+"."+t.name for t in tree.find_all(exp.Table)}
    fields = []
    for projection in tree.expressions:
        sources = []
        for col in projection.find_all(exp.Column):
            origin = tables.get(col.table) if col.table else next(iter(tables.values())) if len(tables) == 1 else None
            sources.append({"table": origin, "column": col.name})
        fields.append({"target_column": projection.alias_or_name, "expression": projection.sql(dialect="postgres"), "sources": sources,
            "coverage": "RESOLVED" if all(s["table"] for s in sources) else "UNKNOWN"})
    return {"coverage": "SUPPORTED_PROJECTION", "fields": fields,
        "filters": tree.args["where"].sql(dialect="postgres") if tree.args.get("where") else None,
        "joins": [j.sql(dialect="postgres") for j in tree.args.get("joins", [])]}
