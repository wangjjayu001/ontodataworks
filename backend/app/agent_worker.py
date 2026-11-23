"""Durable local worker. Run with python -m app.agent_worker.
Expired query leases become UNKNOWN, never blindly replayed.
"""
import copy
import json
import logging
import re
import time
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select, or_, text
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from .db import SessionLocal, User, Datasource, QueryRun, Lineage, now
from .security import workspace_access
from .warehouse import connect
from .agent_models import AgentJob, AgentRun, AgentExecution
from .agent import event, save_run, _audit, check_run_access
from .agent_planner import canonical_hash, new_id, PlanningBlocked, source_check, observe_measure_rows, verify_physical_grain, apply_result_policies

log = logging.getLogger("agent-worker")


def pinned_model_vocabulary(db, wid, user, deployment):
    """Only names from the exact deployed, currently readable dependency graph.

    Model context is a hint, never an execution grant. Draft heads and stable
    asset IDs are deliberately absent from this vocabulary.
    """
    from .semantic_models import SemanticRelease, SemanticIndex, SemanticGovernance
    from .semantic import resolve_asset, can_read_asset, can_read_physical, sha, walk_refs, validity_active, semantic_access
    if not user or not user.enabled:
        raise PlanningBlocked("BLOCKED_PERMISSION","模型调用身份已停用")
    semantic_access(db,wid,user)
    release=db.get(SemanticRelease,deployment["release_id"])
    index=db.get(SemanticIndex,deployment["index_snapshot_id"])
    if not release or not index or release.workspace_id!=wid or index.workspace_id!=wid or release.content_hash!=deployment["release_hash"] or index.content_hash!=deployment["index_snapshot_hash"] or sha(release.content)!=release.content_hash or sha(index.content)!=index.content_hash or index.release_id!=release.id or index.release_hash!=release.content_hash:
        raise PlanningBlocked("INDEX_RELEASE_MISMATCH","模型上下文的固定发布和索引不可验证")
    if release.scope=="local_validation" and user.role!="admin":
        raise PlanningBlocked("BLOCKED_PERMISSION","本地技术问数身份权限已变化")
    manifest={(i["asset_ref"]["asset_id"],i["asset_ref"]["revision"]):i["content_hash"] for i in release.content["items"]}
    snapshots={s["id"] for s in release.content["catalog_snapshots"]}
    cache={};snapshot_cache={}
    def readable(target,ancestors=frozenset()):
        if "asset_id" not in target:
            return target["catalog_snapshot_id"] in snapshots and can_read_physical(db,wid,user,target,release.scope,snapshot_cache)
        identity=(target["asset_id"],target["revision"])
        if identity in cache:return cache[identity]
        if identity in ancestors:return False
        if target["workspace_id"]!=wid or identity not in manifest:return False
        asset=resolve_asset(db,wid,target)
        gov=db.get(SemanticGovernance,(wid,target["asset_id"]))
        if sha(asset)!=manifest[identity] or not can_read_asset(db,wid,user,asset) or not validity_active(asset["validity"]) or (gov and gov.revision==target["revision"] and gov.workflow_state in {"WITHDRAWN","REJECTED"}):
            cache[identity]=False;return False
        body={k:v for k,v in asset.items() if k not in {"workspace_id","asset_id","asset_type","revision"}}
        cache[identity]=all(readable(dependency,ancestors|{identity}) for _,_,dependency in walk_refs(body))
        return cache[identity]
    result={"metric_vocabulary":[],"dimension_vocabulary":[]};remaining=4000
    for item in release.content["items"]:
        target=item["asset_ref"]
        if target["asset_type"] not in {"metric","dimension"} or not readable(target):continue
        asset=resolve_asset(db,wid,target)
        words=result[target["asset_type"]+"_vocabulary"]
        for value in [asset["name"],*[alias["text"] for alias in asset["aliases"]]]:
            if isinstance(value,str) and value.strip() and len(value)<=160 and value not in words and len(words)<120 and len(value)<=remaining:
                words.append(value);remaining-=len(value)
    return result


def normalize_intent(item, original):
    question = item.get("question") or original
    result = {"question": question, "metric_terms": item.get("metric_terms", []),
        "dimension_terms": item.get("dimension_terms", []), "dimension_ids": [],
        "start": item.get("start"), "end_exclusive": item.get("end_exclusive"),
        "timezone": item.get("timezone"), "time_event": item.get("time_event"),
        "filters": item.get("filters", []), "negative_terms": item.get("negative_terms", []),
        "analysis": copy.deepcopy(item.get("analysis") or {"kind": "NONE", "base_period": {"start": None, "end_exclusive": None}, "comparison_period": {"start": None, "end_exclusive": None}, "measures": []}),
        "depends_on_requirement_ids": [], "original_question": original}
    terms = " ".join(map(str, result["dimension_terms"]))
    explicit_event = re.search(r"支付时间|付款时间|下单时间|订单创建时间|发货时间|评价时间|会话开始时间", question)
    if explicit_event:
        result["requested_time_event"] = explicit_event.group()
    if re.search(r"(?:订单|支付|发货).{0,3}状态|已(?:支付|付款|完成|取消|退款)|未(?:支付|付款|发货)|退款订单|取消订单", question):
        return result, [], {"code": "STATUS_SEMANTICS_UNSUPPORTED", "message": "当前映射尚未实现该业务状态筛选或分组，不能忽略条件后返回总计"}
    if re.search(r"只(?:统计|计算|看|要)|仅(?:统计|计算|看)|排除|不包括|不包含|筛选|过滤|where\b", question, re.I) and not result["filters"] and not result["negative_terms"]:
        return result, [], {"code": "FILTER_EXTRACTION_INCOMPLETE", "message": "原问题含筛选或排除条件，但尚未得到完整结构化条件，请重新明确后查询"}
    if re.search(r"买家状态|客户状态|用户状态", question):
        return result, [], {"code": "BUYER_STATUS_ASSET_MISSING", "message": "当前没有已治理的买家业务状态维度，请说明活跃、认证或生命周期状态；地区代码不能替代买家状态"}
    for term in result["dimension_terms"]:
        if re.search(r"地区|省|州|customer_state|地域", term) and not re.search(r"不按|不要|排除|不使用", term):
            result["dimension_ids"].append("customer_state")
        elif re.search(r"类目|类别|品类|product_category", term) and not re.search(r"不按|不要|排除|不使用", term):
            result["dimension_ids"].append("product_category")
        else:
            return result, [], {"code": "DIMENSION_ASSET_MISSING", "message": "请求的维度尚无已核验实现，不能忽略该维度后返回总计"}
    result["dimension_ids"] = list(dict.fromkeys(result["dimension_ids"]))
    if re.search(r"(?:不要|不按|别按|不使用).{0,8}(?:地区|地域|省|州|类目|类别)", question):
        return result, [], {"code": "NEGATIVE_DIMENSION_REVIEW", "message": "问题含排除维度约束，需要明确目标分组后再查询"}
    if re.search(r"按.{0,8}(?:地区|地域|省|州|类目|类别|状态).{0,8}(?:统计|分组|看|计算|订单)", question) and not result["dimension_ids"]:
        return result, [], {"code": "DIMENSION_EXTRACTION_INCOMPLETE", "message": "请求包含分组要求，模型未得到完整维度，不能省略后查询"}
    clarifications = []
    if not result["start"] or not result["end_exclusive"]:
        clarifications.append({"id": new_id("clarify"), "slot": "time", "question": "请明确查询起止日期（结束日不含）", "options": []})
    if re.search(r"源库时间|源时间|不做时区转换|SOURCE_TIMESTAMP", original, re.I):
        result["timezone"] = "SOURCE"
        result["negative_terms"] = [term for term in result["negative_terms"] if not ("时区" in term and not re.search(r"订单|支付|金额|地区|类别|过滤|排除记录", term))]
    if result["timezone"] not in {"SOURCE", "source", "SOURCE_TIMESTAMP"}:
        result["timezone"] = None
        clarifications.append({"id": new_id("clarify"), "slot": "timezone", "question": "本地源库没有经过批准的时区定义，是否按源库原始时间查询且不做时区转换？", "options": [{"value": "SOURCE", "label": "使用源库时间（不转换）"}]})
    if re.search(r"销售额", question) and not re.search(r"商品销售额|商品金额|含运费|订单总额", question):
        clarifications.append({"id": new_id("clarify"), "slot": "metric", "question": "销售额是指含运费订单总额，还是不含运费商品金额？", "options": [{"value": "gross_order_value", "label": "订单总额（含运费）"}, {"value": "item_revenue", "label": "商品金额（不含运费）"}]})
    for c in item.get("clarifications", []):
        if c.get("slot") in {"timezone", "time", "metric"} or any(c.get("question") == x["question"] for x in clarifications): continue
        clarifications.append({"id": new_id("clarify"), "slot": c.get("slot", "semantic"), "question": c.get("question", "请明确业务口径"), "options": c.get("options", [])})
    return result, clarifications, None


def parse_job(job_id):
    with SessionLocal() as db:
        job = db.get(AgentJob, job_id); row = db.get(AgentRun, job.run_id); user = db.get(User, row.actor_id)
        payload = copy.deepcopy(row.payload)
        if payload.get("cancel_requested"):
            payload["state"] = "CANCELLED"; save_run(db, row, payload); job.status = "CANCELLED"; db.commit(); return
        workspace_access(db, user, row.workspace_id)
        check_run_access(db, row, user)
        question, selection = payload["message"]["text"], payload["model_selection"]
        vocabulary=pinned_model_vocabulary(db,row.workspace_id,user,payload["pinned_deployment"])
    from .model_runtime import parse_intent
    output = parse_intent(question, {"data_classification": "synthetic", "source_scope": "local_validation", "current_date": now().date().isoformat(),
        **vocabulary,
        "warnings": ["买家状态与地区不同", "源库timestamp时区未知，不默默指定时区", "不允许生成SQL或编造资产ID"]}, selection, request_id=payload["request_id"])
    draft = output.get("output", output)
    if "requirements" not in draft and isinstance(draft.get("intent"), dict): draft = draft["intent"]
    requirements = draft.get("requirements")
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 8:
        raise PlanningBlocked("MODEL_OUTPUT_INVALID", "模型没有返回可验证的需求项")
    normalized = []
    for item in requirements:
        intent, clarifications, error = normalize_intent(item, question)
        rid = new_id("requirement")
        normalized.append({"id": rid, "requirement_id": rid, "requirement_version": 1, "question": intent["question"],
            "intent_frame": intent, "intent_frame_hash": canonical_hash(intent), "clarifications": clarifications,
            "state": "BLOCKED_EVIDENCE" if error else "NEED_CLARIFICATION" if clarifications else "TARGET_CONFIRMATION", "error": error})
    for index, item in enumerate(requirements):
        dependencies = item.get("depends_on_requirement_indexes", [])
        if any(not isinstance(d, int) or d < 0 or d >= index for d in dependencies):
            raise PlanningBlocked("REQUIREMENT_DEPENDENCY_INVALID", "复合需求的依赖关系无效")
        normalized[index]["intent_frame"]["depends_on_requirement_ids"] = [normalized[d]["id"] for d in dependencies]
        normalized[index]["intent_frame_hash"] = canonical_hash(normalized[index]["intent_frame"])
        if dependencies or ("其中" in normalized[index]["question"]):
            normalized[index]["state"] = "BLOCKED_EVIDENCE"
            normalized[index]["error"] = {"code": "COMPOSITE_SET_UNSUPPORTED", "message": "该需求依赖上游集合或结果；集合传递规划尚未完成验收，不能拆成独立查询代替"}
    with SessionLocal() as db:
        job = db.get(AgentJob, job_id); row = db.scalar(select(AgentRun).where(AgentRun.id == job.run_id).with_for_update())
        payload = copy.deepcopy(row.payload)
        if payload.get("cancel_requested"):
            payload["state"] = "CANCELLED"; job.status = "CANCELLED"
        else:
            payload["requirements"] = normalized
            payload["model_call"] = {k: v for k, v in output.items() if k not in {"output", "intent", "requirements"}}
            payload["model_call"]["real_inference"] = True
            event(db, row, payload, "requirements.updated", detail={"requirement_ids": [r["id"] for r in normalized]})
            job.status = "DONE"
        save_run(db, row, payload); db.commit()


def clarification_job(job_id):
    from .agent_clarification import clarified_question, merge_clarified_intent, confirmed_choices
    from .model_runtime import refine_intent
    with SessionLocal() as db:
        job = db.get(AgentJob, job_id); row = db.get(AgentRun, job.run_id)
        if job.status not in {'QUEUED', 'RUNNING'}:
            return
        payload = copy.deepcopy(row.payload)
        previous = next(r for r in payload['requirements'] if r['id'] == job.requirement_id)
        if payload.get('cancel_requested') or previous['state'] != 'DRAFT' or previous.get('clarification_parse_version') != previous['requirement_version']:
            job.status = 'CANCELLED'; db.commit(); return
        user = db.get(User, row.actor_id)
        workspace_access(db, user, row.workspace_id); check_run_access(db, row, user)
        vocabulary = pinned_model_vocabulary(db, row.workspace_id, user, payload['pinned_deployment'])
        question = clarified_question(previous)
    output = refine_intent(question, {'data_classification': 'synthetic', 'source_scope': 'local_validation',
        'confirmed_intent': confirmed_choices(previous),
        'current_date': now().date().isoformat(), **vocabulary,
        'warnings': ['只重新解析这一个需求项，保留原问题的筛选、分组和排除条件',
            '用户补充的答案是业务约束；已经明确回答的口径不要重复询问；不能推断用户未回答的选择',
            '同比或增长分析不能改写为两期合计，不允许生成SQL或资产ID']},
        payload['model_selection'], request_id=job_id)
    error = None
    try:
        intent, clarifications, error = normalize_intent(output['output']['intent'], question)
        intent, clarifications = merge_clarified_intent(previous, intent, clarifications)
    except PlanningBlocked as exc:
        intent, clarifications = previous['intent_frame'], []
        error = {'code': exc.code, 'message': exc.message}
    with SessionLocal() as db:
        job = db.get(AgentJob, job_id)
        row = db.scalar(select(AgentRun).where(AgentRun.id == job.run_id).with_for_update())
        payload = copy.deepcopy(row.payload)
        req = next(r for r in payload['requirements'] if r['id'] == job.requirement_id)
        # Cancelling or updating the requirement while the model runs wins.
        if payload.get('cancel_requested') or req['state'] != 'DRAFT' or req['requirement_version'] != previous['requirement_version']:
            job.status = 'CANCELLED'; db.commit(); return
        user = db.get(User, row.actor_id)
        workspace_access(db, user, row.workspace_id); check_run_access(db, row, user)
        pinned_model_vocabulary(db, row.workspace_id, user, payload['pinned_deployment'])
        req.update(intent_frame=intent, intent_frame_hash=canonical_hash(intent), clarifications=clarifications,
            state='BLOCKED_EVIDENCE' if error else 'NEED_CLARIFICATION' if clarifications else 'TARGET_CONFIRMATION', error=error)
        req['clarification_requires_reparse'] = False
        req['clarification_model_call'] = {k: v for k, v in output.items() if k != 'output'}
        req['clarification_model_call']['real_inference'] = True
        req['requirement_version'] += 1
        event(db, row, payload, 'clarification.parsed', req['id'], {'state': req['state']})
        save_run(db, row, payload); job.status = 'DONE'; db.commit()


def execution_job(job_id):
    from .semantic import get_deployment
    with SessionLocal() as db:
        job = db.get(AgentJob, job_id); row = db.get(AgentRun, job.run_id); user = db.get(User, row.actor_id)
        payload = copy.deepcopy(row.payload); req = next(r for r in payload["requirements"] if r["id"] == job.requirement_id)
        execution = db.get(AgentExecution, req["execution_operation_id"])
        if req["state"] == "CANCEL_REQUESTED" or payload.get("cancel_requested"):
            req["state"] = "CANCELLED"; req["requirement_version"] += 1; execution.status = "CANCELLED"; job.status = "CANCELLED"
            save_run(db, row, payload); db.commit(); return
        if execution.status != "QUEUED":
            raise PlanningBlocked("EXECUTION_ALREADY_CLAIMED", "查询操作已被认领，禁止重放")
        if not user or not user.enabled or user.role != "admin":
            raise PlanningBlocked("BLOCKED_PERMISSION", "本地验证执行要求有效管理员身份")
        workspace_access(db, user, row.workspace_id)
        # Revalidates current policy; pinned snapshot is not an access grant.
        check_run_access(db, row, user)
        get_deployment(db, row.workspace_id, user, "local_validation")
        from datetime import datetime
        if datetime.fromisoformat(req["authorization"]["expires_at"]) < now():
            raise PlanningBlocked("AUTHORIZATION_EXPIRED", "队列中的单次授权已过期，查询未执行")
        checks = source_check(db, row.workspace_id, req["query_frame"], req["preview"])
        from .pipeline_catalog import guard_asset
        from .db import Asset
        for checked in checks["verified_sources"]:
            guard_asset(db,row.workspace_id,user,db.get(Asset,checked["asset_id"]),raw=True)
        frame, preview, execution_id, wid = copy.deepcopy(req["query_frame"]), copy.deepcopy(req["preview"]), execution.id, row.workspace_id
        source = db.get(Datasource, frame["datasource_id"])
        execution.status = "EXECUTING"; db.commit()
    started = time.monotonic()
    with connect(source) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
        from .pipeline_catalog import batch_gate, digest, enrollment_current, enrollment_identity, verify_readonly_source
        from .db import Asset
        for checked in sorted(checks["verified_sources"],key=lambda a:a["physical_object"]):
            ns, name = checked["physical_object"].split(".")
            conn.execute("SELECT pg_advisory_xact_lock_shared(%s)",(int(digest([source.database,ns,name])[:15],16),))
        with SessionLocal() as db:
            live_source=db.get(Datasource,source.id)
            if source.username!="ecommerce_reader" or source.database!="ecommerce_validation":
                if not enrollment_current(db,live_source) or enrollment_identity(live_source)!=enrollment_identity(source): raise PlanningBlocked("LOCAL_SCOPE_REQUIRED","本地验证源登记已撤销或连接已变化")
            checks["data_batches"]=[batch_gate(db,wid,db.get(Asset,a["asset_id"])) for a in checks["verified_sources"]]
        verify_readonly_source(conn,source)
        conn.execute("SELECT set_config('application_name', %s, true)", ("agent:" + execution_id,))
        pid = conn.info.backend_pid
        with SessionLocal() as db:
            execution = db.get(AgentExecution, execution_id); execution.backend_pid = pid; db.commit()
        if frame.get("execution_authority") == "AssetRevision+CatalogSnapshot":
            verify_physical_grain(conn, frame)
        measure_counts = observe_measure_rows(conn, frame, preview["parameters"])
        result = conn.execute(preview["sql"], preview["parameters"])
        values = result.fetchmany(1001); columns = [d.name for d in result.description]
        values, no_data, warnings = apply_result_policies(frame, measure_counts, values)
    truncated = len(values) > 1000; values = values[:1000]
    if columns != frame["result_shape"]:
        raise PlanningBlocked("RESULT_SHAPE_MISMATCH", "结果列与已确认方案不一致")
    if frame.get("result_kind") == "COUNT" or frame["metric_id"] in {"order_count", "session_count", "shipment_count", "promotion_orders"}:
        if any(r[-1] is None or int(r[-1]) != r[-1] or r[-1] < 0 for r in values):
            raise PlanningBlocked("RESULT_INVARIANT_FAILED", "计数结果未通过整数与非负校验")
    if (frame.get("result_kind") == "SCORE" or frame["metric_id"] == "review_score") and any(r[-1] is not None and not 1 <= r[-1] <= 5 for r in values):
        raise PlanningBlocked("RESULT_INVARIANT_FAILED", "评分结果超出1至5范围")
    with SessionLocal() as db:
        job = db.get(AgentJob, job_id); row = db.scalar(select(AgentRun).where(AgentRun.id == job.run_id).with_for_update())
        user = db.get(User, row.actor_id); workspace_access(db, user, wid)
        if not user.enabled or user.role != "admin": raise PlanningBlocked("BLOCKED_PERMISSION", "结果读取权限已撤销")
        check_run_access(db, row, user)
        from .pipeline_catalog import guard_asset
        from .db import Asset
        for checked in checks["verified_sources"]:
            guard_asset(db,wid,user,db.get(Asset,checked["asset_id"]),raw=True)
        payload = copy.deepcopy(row.payload); req = next(r for r in payload["requirements"] if r["id"] == job.requirement_id)
        execution = db.get(AgentExecution, execution_id)
        query = QueryRun(workspace_id=wid, datasource_id=frame["datasource_id"], actor=user.username,
            sql=preview["sql"], status="success", row_count=len(values), duration_ms=int((time.monotonic()-started)*1000))
        db.add(query); db.flush()
        output = {"query_id": query.id, "execution_operation_id": execution_id, "columns": columns,
            "column_metadata": frame.get("result_column_metadata", []),
            "rows": jsonable_encoder(values, custom_encoder={Decimal: str}), "row_count": len(values), "truncated": truncated,
            "duration_ms": query.duration_ms, "data_as_of": checks["data_as_of"], "data_coverage": checks["data_coverage"],
            "verified_sources": checks["verified_sources"], "validation": {"status": "PASS", "checks": ["result_shape", "metric_invariants", "physical_grain", "null_and_empty_policy"], "business_approved": False,
                "measure_row_counts": measure_counts, "warnings": warnings},
            "no_data": no_data, "warnings": warnings, "data_batches":checks["data_batches"],
            "scope": "local_validation", "sql_hash": preview["sql_hash"], "query_frame_hash": frame["content_hash"],
            "release_id": req["bundle"]["release_id"], "index_snapshot_id": req["bundle"]["index_snapshot_id"], "executed_at": now().isoformat()}
        for asset in checks["verified_sources"]:
            db.add(Lineage(workspace_id=wid, query_id=query.id, asset_id=asset["asset_id"], source=asset["physical_object"],
                target="query:"+str(query.id), evidence={"origin": "agent_verified_query", "run_id": row.id,
                    "requirement_id": req["id"], "execution_id": execution_id, "frame_hash": frame["content_hash"],
                    "sql_hash": preview["sql_hash"], "metadata_fingerprint": asset["fingerprint"], "scope": "local_validation"}))
        req.update(result=output, state="DELIVERED"); req["requirement_version"] += 1
        execution.status = "DELIVERED"; execution.result = output; execution.completed_at = now()
        event(db, row, payload, "result.ready", req["id"], {"query_id": query.id, "execution_operation_id": execution_id})
        save_run(db, row, payload); job.status = "DONE"; _audit(db, user, wid, "execute.complete", execution_id); db.commit()


def claim():
    with SessionLocal() as db:
        # Expired jobs may have issued an external query. Never replay EXECUTE.
        expired = db.scalars(select(AgentJob).where(AgentJob.status == "RUNNING", AgentJob.lease_until < now()).with_for_update(skip_locked=True)).all()
        for job in expired:
            if job.kind == "CLARIFY":
                row = db.get(AgentRun, job.run_id)
                req = next((r for r in row.payload.get('requirements', []) if r['id'] == job.requirement_id), None)
                if row.payload.get('cancel_requested') or not req or req['state'] != 'DRAFT':
                    job.status = 'CANCELLED'; continue
            if job.kind in {"PARSE", "CLARIFY"} and job.attempts < 2:
                job.status = "QUEUED"
            else:
                job.status = "UNKNOWN"; job.error = "租约过期，需先对账，禁止自动重跑"
                row = db.get(AgentRun, job.run_id); payload = copy.deepcopy(row.payload)
                req = next((r for r in payload.get("requirements", []) if r["id"] == job.requirement_id), None)
                if req:
                    req["state"] = "MODEL_FAILED" if job.kind == "CLARIFY" else "EXECUTION_UNKNOWN"
                    req["error"] = {"code": req["state"], "message": job.error}; req["requirement_version"] += 1
                    if req.get("execution_operation_id"):
                        execution = db.get(AgentExecution, req["execution_operation_id"]); execution.status = "UNKNOWN"
                else: payload["state"] = "MODEL_FAILED"
                save_run(db, row, payload)
        job = db.scalar(select(AgentJob).where(AgentJob.status == "QUEUED").order_by(AgentJob.created_at).with_for_update(skip_locked=True).limit(1))
        if job:
            job.status = "RUNNING"; job.attempts += 1; job.lease_until = now() + timedelta(minutes=6)
            job_id = job.id
        else: job_id = None
        db.commit(); return job_id


def record_failure(job_id, exc):
    with SessionLocal() as db:
        job = db.get(AgentJob, job_id); row = db.scalar(select(AgentRun).where(AgentRun.id == job.run_id).with_for_update())
        payload = copy.deepcopy(row.payload)
        is_model = job.kind in {"PARSE", "CLARIFY"}
        code = getattr(exc, "code", "MODEL_SERVICE_FAILED" if is_model else "EXECUTION_FAILED")
        message = getattr(exc, "message", "模型解析失败，请检查模型服务后重试" if is_model else "查询失败，请检查本次操作状态；不会自动重跑")
        if isinstance(exc,HTTPException):
            code="BLOCKED_PERMISSION" if exc.status_code==403 else "DATA_GATE_BLOCKED"
            message=exc.detail.get("message",message) if isinstance(exc.detail,dict) else str(exc.detail)
        job.status = "FAILED"; job.error = str(code)
        req = next((r for r in payload.get("requirements", []) if r["id"] == job.requirement_id), None)
        if req:
            if job.kind == "CLARIFY":
                if req['state'] == 'DRAFT' and not payload.get('cancel_requested'):
                    req.update(state='MODEL_FAILED', error={'code': code, 'message': message})
                    req['requirement_version'] += 1
                    event(db, row, payload, 'clarification.failed', req['id'], {'code': code, 'message': message})
                    save_run(db, row, payload)
                db.commit(); return
            import psycopg
            cancelled = isinstance(exc, psycopg.errors.QueryCanceled) and req["state"] == "CANCEL_REQUESTED"
            unknown = isinstance(exc, psycopg.OperationalError) and not isinstance(exc, psycopg.errors.QueryCanceled)
            req["state"] = "CANCELLED" if cancelled else "EXECUTION_UNKNOWN" if unknown else "EXECUTION_FAILED"
            if unknown: code, message = "EXECUTION_UNKNOWN", "查询连接中断，原操作需对账；禁止重新授权重跑"
            if cancelled: code, message = "QUERY_CANCELLED", "已确认查询被取消"
            req["error"] = {"code": code, "message": message}; req["requirement_version"] += 1
            if req.get("execution_operation_id"):
                execution = db.get(AgentExecution, req["execution_operation_id"]); execution.status = "CANCELLED" if cancelled else "UNKNOWN" if unknown else "FAILED"; execution.completed_at = now()
        else:
            payload["state"] = "CANCELLED" if payload.get("cancel_requested") else "MODEL_FAILED"
            payload["error"] = {"code": code, "message": message}
        event(db, row, payload, "run.failed", job.requirement_id, {"code": code, "message": message})
        save_run(db, row, payload); db.commit()
    log.warning("Agent job failed: %s %s", job_id, type(exc).__name__)


def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            job_id = claim()
            if not job_id:
                time.sleep(.5); continue
            with SessionLocal() as db: kind = db.get(AgentJob, job_id).kind
            try:
                {"PARSE": parse_job, "CLARIFY": clarification_job, "EXECUTE": execution_job}[kind](job_id)
            except Exception as exc:
                record_failure(job_id, exc)
        except Exception:
            log.exception("Agent worker loop error")
            time.sleep(2)


if __name__ == "__main__":
    main()
