"""Backend question-answering state machine; UI and model cannot set state."""
import copy
import hashlib
import json
import time
from datetime import timedelta, datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, text
from .db import SessionLocal, User, Audit, QueryRun, Lineage, Datasource, LoginSession, now
from .security import identity, db_session, workspace_access
from .agent_models import AgentConversation, AgentRun, AgentCommand, AgentEvent, AgentJob, AgentExecution, AgentFeedback
from .agent_planner import canonical_hash, new_id, PlanningBlocked, compile_preview, compile_metadata_preview, normalize_metadata_candidate, source_check

router = APIRouter(prefix="/api/workspaces/{wid}/agent", tags=["agent-v1"])
TERMINAL = {"DELIVERED", "CANCELLED", "EXECUTION_FAILED", "MODEL_FAILED"}


def fail(code, message, status=409):
    raise HTTPException(status, {"code": code, "message": message})


def model_failure(exc, default_message):
    """Keep bridge contract errors visible to the caller instead of hiding them."""
    from .model_runtime import ModelRuntimeError
    if isinstance(exc, ModelRuntimeError):
        fail(exc.code, exc.message, 503 if exc.retryable else 422)
    fail("MODEL_DIRECTORY_UNAVAILABLE", default_message, 503)


def envelope(wid, data, request_id=None):
    return {"api_version": "agent-api/1.0.0", "request_id": request_id or new_id("req"),
        "trace_id": new_id("trace"), "workspace_id": wid, "data": data}


def _audit(db, user, wid, action, detail):
    db.add(Audit(workspace_id=wid, actor=user.username, action="agent." + action, detail=detail[:2000]))


def run_row(db, wid, run_id, user, lock=False):
    workspace_access(db, user, wid)
    stmt = select(AgentRun).where(AgentRun.id == run_id, AgentRun.workspace_id == wid, AgentRun.actor_id == user.id)
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if not row:
        fail("RUN_UNAVAILABLE", "运行不存在或当前身份无权访问", 404)
    return row


def check_run_access(db, row, user):
    from .semantic import check_bundle_access, can_read_physical, can_read_asset, resolve_asset
    from .semantic_models import SemanticRelease
    from .pipeline_catalog import guard_asset
    from .db import Asset
    snapshot_cache={}
    for req in row.payload.get("requirements", []):
        if req.get("bundle"):
            check_bundle_access(db, row.workspace_id, user, req["bundle"])
        discovery=req.get("metadata_discovery") or {}
        for table in discovery.get("tables",[]):
            dataset_ref=table.get("dataset_ref") or {}
            release=db.get(SemanticRelease,discovery.get("release_id"))
            scope=release.scope if release else "local_validation"
            targets=[dataset_ref]+[{**dataset_ref,"field_id":field.get("field_id")} for field in table.get("fields",[]) if field.get("field_id")]
            if any(not can_read_physical(db,row.workspace_id,user,target,scope,snapshot_cache) for target in targets):
                fail("BLOCKED_PERMISSION","历史目录候选的现时读取政策已失效",403)
        candidate=req.get("metadata_candidate") or {}
        candidate_ref=candidate.get("dataset_ref")
        if candidate_ref:
            release=db.get(SemanticRelease,candidate.get("release_id"))
            scope=release.scope if release else "local_validation"
            targets=[candidate_ref]+[{**candidate_ref,"field_id":field.get("field_id")} for field in
                [candidate.get("metric_field"),candidate.get('denominator_field'),candidate.get("time_field"),*candidate.get("dimension_fields",[]),
                 *[item.get("field") for item in candidate.get("filters",[])]] if field and field.get("field_id")]
            if any(not can_read_physical(db,row.workspace_id,user,target,scope,snapshot_cache) for target in targets):
                fail("BLOCKED_PERMISSION","临时口径所引用的目录字段当前不可读取",403)
        for source in req.get("result", {}).get("verified_sources", []):
            asset = db.get(Asset, source.get("asset_id"))
            if not asset or asset.workspace_id != row.workspace_id:
                fail("BLOCKED_PERMISSION", "历史结果来源不可用", 403)
            guard_asset(db, row.workspace_id, user, asset, raw=True)
        provenance=req.get("result",{}).get("provenance") or {}
        for source in provenance.get("tables",[]):
            dataset_ref=source.get("dataset_ref")
            if dataset_ref and not can_read_physical(db,row.workspace_id,user,dataset_ref,"local_validation",snapshot_cache):
                fail("BLOCKED_PERMISSION","历史结果目录来源的现时读取政策已失效",403)
            for field in source.get("fields",[]):
                field_ref=field.get("field_ref")
                if field_ref and not can_read_physical(db,row.workspace_id,user,field_ref,"local_validation",snapshot_cache):
                    fail("BLOCKED_PERMISSION","历史结果字段来源的现时读取政策已失效",403)
        semantic_refs=[item.get("asset_ref") for item in provenance.get("semantic_assets",[])]
        semantic_refs += [item.get("evidence_ref") or item.get("claim_ref") for item in provenance.get("evidence",[])]
        for target in semantic_refs:
            if target and not can_read_asset(db,row.workspace_id,user,resolve_asset(db,row.workspace_id,target)):
                fail("BLOCKED_PERMISSION","历史结果语义证据的现时读取政策已失效",403)


def _actions(req, user):
    state = req["state"]
    actions = {"NEED_CLARIFICATION": ["clarifications"], "TARGET_CONFIRMATION": ["confirm-intent"],
        "NEED_CONFIRMATION": ["confirm-metric"], "VERIFIED": ["preview"], "PREVIEW_READY": ["preview", "gate"],
        "GATE_PASSED": ["authorize"], "AUTHORIZED": ["execute"]}.get(state, [])
    if user.role != "admin":
        actions = [a for a in actions if a not in {"gate", "authorize", "execute"}]
    if state in {"BLOCKED_EVIDENCE", "NEED_CLARIFICATION", "TARGET_CONFIRMATION"} or (state == 'MODEL_FAILED' and req.get('metadata_route')):
        actions = actions + ["retry-retrieval"]
    if state in {"NEED_CLARIFICATION", "TARGET_CONFIRMATION"}:
        actions = actions + ["time-periods"]
    if state not in TERMINAL and state != "EXECUTION_UNKNOWN":
        actions = actions + ["cancel"]
    return actions


def view(row, user):
    data = copy.deepcopy(row.payload)
    data.update({"id": row.id, "run_id": row.id, "state": row.state, "run_version": row.run_version,
        "conversation_id": row.conversation_id, "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()})
    for req in data.get("requirements", []):
        if req['state'] in {'NEED_CLARIFICATION', 'TARGET_CONFIRMATION'}:
            from .agent_time import prepare_periods
            proposed = copy.deepcopy(req['intent_frame'])
            req['clarifications'] = prepare_periods(proposed, req.get('clarifications', []))
            req['suggested_time_periods'] = proposed.get('time_periods', [])
        req['clarifications'] = [c for c in req.get('clarifications', []) if c.get('slot') != 'timezone']
        if req['state'] == 'NEED_CLARIFICATION' and not req['clarifications']:
            req['state'] = 'TARGET_CONFIRMATION'
        req["allowed_actions"] = _actions(req, user)
    data["allowed_actions"] = [] if row.state in TERMINAL else ["cancel"]
    data["last_event_id"] = row.payload.get("event_seq", 0)
    data["state_counts"] = {state: sum(r["state"] == state for r in data.get("requirements", [])) for state in {r["state"] for r in data.get("requirements", [])}}
    return data


def aggregate(payload):
    states = [r["state"] for r in payload.get("requirements", [])]
    if not states:
        return payload.get("state", "DRAFT")
    if all(s == "DELIVERED" for s in states):
        return "DELIVERED"
    if all(s == "CANCELLED" for s in states):
        return "CANCELLED"
    for state in ["CANCEL_REQUESTED", "NEED_CLARIFICATION", "TARGET_CONFIRMATION", "NEED_CONFIRMATION"]:
        if state in states:
            return state
    return next((s for s in states if s not in TERMINAL), states[0])


def event(db, row, payload, name, requirement_id=None, detail=None):
    persisted = db.scalar(select(func.max(AgentEvent.event_id)).where(AgentEvent.run_id == row.id)) or 0
    payload["event_seq"] = max(payload.get("event_seq", 0), persisted) + 1
    req = next((r for r in payload.get("requirements", []) if r["id"] == requirement_id), None)
    db.add(AgentEvent(workspace_id=row.workspace_id, run_id=row.id, event_id=payload["event_seq"], event_type=name,
        payload={"event_id": payload["event_seq"], "event_type": name, "run_id": row.id,
            "requirement_id": requirement_id, "run_version": row.run_version + 1,
            "requirement_version": req["requirement_version"] if req else None,
            "request_id": payload.get("request_id"), "trace_id": payload.get("trace_id"),
            "occurred_at": now().isoformat(), "data": detail or {}}))


def save_run(db, row, payload):
    row.payload = copy.deepcopy(payload)
    row.run_version += 1
    row.state = aggregate(payload)
    row.updated_at = now()


def command_start(db, request, wid, user, body):
    key = request.headers.get("Idempotency-Key", "")
    if not key or len(key) > 180:
        fail("IDEMPOTENCY_KEY_REQUIRED", "写请求必须包含有效幂等键", 400)
    route = request.url.path
    input_hash = canonical_hash(body)
    # Serialize identical commands even before a row exists.
    digest = hashlib.sha256(f"{wid}:{user.id}:{route}:{key}".encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:n)"), {"n": int.from_bytes(digest[:8], "big", signed=True)})
    existing = db.scalar(select(AgentCommand).where(AgentCommand.workspace_id == wid, AgentCommand.actor_id == user.id,
        AgentCommand.route == route, AgentCommand.idempotency_key == key))
    if existing:
        if existing.input_hash != input_hash:
            fail("IDEMPOTENCY_CONFLICT", "同一幂等键已用于不同请求")
        return existing.result, None
    return None, {"workspace_id": wid, "actor_id": user.id, "route": route, "idempotency_key": key, "input_hash": input_hash}


def command_done(db, meta, response):
    db.add(AgentCommand(**meta, result=response))
    db.commit()
    return response


def _requirement(row, payload, rid, body):
    if body.get("expected_run_version") != row.run_version:
        fail("RUN_VERSION_CONFLICT", "运行已更新，请刷新后重试")
    req = next((r for r in payload.get("requirements", []) if r["id"] == rid), None)
    if not req:
        fail("REQUIREMENT_UNAVAILABLE", "需求项不存在", 404)
    if body.get("expected_requirement_version") != req["requirement_version"]:
        fail("REQUIREMENT_VERSION_CONFLICT", "需求项已更新，请刷新后重试")
    req['clarifications'] = [c for c in req.get('clarifications', []) if c.get('slot') != 'timezone']
    if req['state'] == 'NEED_CLARIFICATION' and not req['clarifications']:
        req['state'] = 'TARGET_CONFIRMATION'
    return req


@router.get("/models")
def models(wid: int, user=Depends(identity), db=Depends(db_session)):
    workspace_access(db, user, wid)
    from .model_runtime import list_models
    try:
        return envelope(wid, list_models())
    except Exception as exc:
        model_failure(exc, "模型服务暂不可用，请检查本地模型适配服务")


@router.get("/conversations")
def conversations(wid: int, user=Depends(identity), db=Depends(db_session)):
    workspace_access(db, user, wid)
    items = []
    for conv in db.scalars(select(AgentConversation).where(AgentConversation.workspace_id == wid,
            AgentConversation.actor_id == user.id).order_by(AgentConversation.updated_at.desc()).limit(50)):
        runs = db.scalars(select(AgentRun).where(AgentRun.conversation_id == conv.id,
            AgentRun.actor_id == user.id).order_by(AgentRun.created_at)).all()
        items.append({"id": conv.id, "title": conv.title, "updated_at": conv.updated_at.isoformat(),
            "runs": [{"run_id": r.id, "state": r.state, "message": r.payload.get("message"), "created_at": r.created_at.isoformat()} for r in runs]})
    return envelope(wid, {"conversations": items})


@router.post("/conversations", status_code=201)
def conversation_create(wid: int, body: dict, request: Request, user=Depends(identity), db=Depends(db_session)):
    workspace_access(db, user, wid)
    old, meta = command_start(db, request, wid, user, body)
    if old: return old
    row = AgentConversation(id=new_id("conversation"), workspace_id=wid, actor_id=user.id, title=str(body.get("title") or "新对话")[:200])
    db.add(row); db.flush()
    return command_done(db, meta, envelope(wid, {"conversation": {"id": row.id, "title": row.title}}))


@router.post("/runs", status_code=202)
def create_run(wid: int, body: dict, request: Request, user=Depends(identity), db=Depends(db_session)):
    workspace_access(db, user, wid)
    old, meta = command_start(db, request, wid, user, body)
    if old: return old
    if set(body) - {"api_version", "request_id", "conversation_id", "message", "interaction_mode", "model_selection", "execution_mode"}:
        fail("COMMAND_FIELDS_INVALID", "请求包含不支持的字段", 422)
    message = body.get("message", {})
    question = message.get("text") if isinstance(message, dict) else None
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
        fail("QUESTION_INVALID", "请输入1至4000字的问题", 422)
    if message.get("attachment_refs"):
        fail("ATTACHMENTS_UNSUPPORTED", "附件处理尚未开放", 422)
    if body.get("interaction_mode", "CONVERSATION") != "CONVERSATION":
        fail("INTERACTION_MODE_UNSUPPORTED", "当前后端提供对话问数；指标资产与查询预览使用各自的真实操作入口", 422)
    mode = body.get("execution_mode", "local_validation")
    if mode not in {"local_validation", "production"}:
        fail("EXECUTION_MODE_INVALID", "不支持的运行范围", 422)
    from .semantic import get_deployment
    deployment = get_deployment(db, wid, user, environment=mode)
    if not deployment:
        fail("SEMANTIC_DEPLOYMENT_UNAVAILABLE", "此运行范围尚无完整语义部署，请先完成语义资产治理")
    if mode == "production":
        fail("MODEL_PRODUCTION_DISABLED", "当前模型运行环境仅验收本地合成材料，生产问数尚未开放")
    from .model_runtime import list_models
    try: directory = list_models()
    except Exception as exc: model_failure(exc, "模型目录不可用")
    selection = body.get("model_selection") or {}
    available = directory.get("models", directory.get("data", []))
    chosen = next((m for m in available if selection.get("model") in {m.get("id"), m.get("model")}), None)
    if not chosen:
        fail("MODEL_NOT_AVAILABLE", "请选择当前目录中的可用模型", 422)
    if selection.get("catalog_version") != directory.get("catalog_version"):
        fail("MODEL_CATALOG_STALE", "模型目录已变化，请重新选择")
    efforts = [e.get("reasoningEffort", e.get("effort")) if isinstance(e, dict) else e for e in chosen.get("supportedReasoningEfforts", chosen.get("supported_reasoning_efforts", []))]
    if selection.get("reasoningEffort") not in efforts:
        fail("MODEL_EFFORT_INVALID", "该模型不支持所选推理强度", 422)
    tiers = [t.get("id", t.get("serviceTier")) if isinstance(t, dict) else t for t in chosen.get("serviceTiers", [])]
    if selection.get("serviceTier") is not None and selection.get("serviceTier") not in tiers:
        fail("MODEL_TIER_INVALID", "该模型不支持所选服务档位", 422)
    conv_id = body.get("conversation_id")
    conv = db.get(AgentConversation, conv_id) if conv_id else None
    if conv_id and (not conv or conv.workspace_id != wid or conv.actor_id != user.id):
        fail("CONVERSATION_UNAVAILABLE", "对话不可用", 404)
    if not conv:
        conv = AgentConversation(id=new_id("conversation"), workspace_id=wid, actor_id=user.id, title=question.strip()[:100])
        db.add(conv); db.flush()
    active = db.scalar(select(AgentRun).where(AgentRun.conversation_id == conv.id, AgentRun.state.in_(["DRAFT", "EXECUTING", "CANCEL_REQUESTED"])))
    if active:
        fail("CONVERSATION_BUSY", "当前对话仍有运行中的操作，请等待或取消")
    payload = {"message": {"text": question.strip()}, "execution_mode": mode,
        "scope_notice": "本地合成数据技术验证，未经生产业务审批", "model_selection": selection,
        "interaction_mode": body.get("interaction_mode", "CONVERSATION"), "pinned_deployment": deployment,
        "request_id": body.get("request_id") or new_id("req"), "trace_id": new_id("trace"),
        "requirements": [], "event_seq": 0, "state": "DRAFT", "attempt": 1,
        "model_progress": {"phase": "QUEUED", "label": "问题已受理，等待识别", "started_at": now().isoformat(), "updated_at": now().isoformat()}}
    row = AgentRun(id=new_id("run"), workspace_id=wid, actor_id=user.id, conversation_id=conv.id, payload=payload)
    db.add(row); db.flush()
    event(db, row, payload, "run.accepted", detail={"model_selection": selection, "execution_mode": mode})
    row.payload = copy.deepcopy(payload)
    db.add(AgentJob(id=new_id("job"), workspace_id=wid, run_id=row.id, kind="PARSE", status="QUEUED"))
    conv.updated_at = now(); _audit(db, user, wid, "run.create", row.id)
    return command_done(db, meta, envelope(wid, {"run": view(row, user), "events_url": f"/api/workspaces/{wid}/agent/runs/{row.id}/events"}, payload["request_id"]))


@router.get("/runs/{run_id}")
def get_run(wid: int, run_id: str, user=Depends(identity), db=Depends(db_session)):
    row = run_row(db, wid, run_id, user)
    check_run_access(db, row, user)
    return envelope(wid, {"run": view(row, user)})


def _selection_ref_match(body, key, expected):
    if body.get(key) != expected:
        fail("CONFIRMATION_STALE", "确认对象已变化，请重新检查当前内容")


def refresh_run_model_selection(payload):
    """A restart may renew the directory without changing the user's model choice."""
    selection=payload.get('model_selection') or {}
    if not selection.get('catalog_version'):
        return
    from .model_runtime import list_models
    catalog=list_models()
    chosen=next((m for m in catalog['models'] if selection.get('model') in {m.get('id'),m.get('model')}),None)
    if not chosen:
        fail('MODEL_NOT_AVAILABLE','原运行选择的模型已不可用，请选择当前模型新建问数',422)
    efforts=[e.get('reasoningEffort') if isinstance(e,dict) else e for e in chosen.get('supportedReasoningEfforts',[])]
    tiers=[t.get('id') if isinstance(t,dict) else t for t in chosen.get('serviceTiers',[])]
    if selection.get('reasoningEffort') not in efforts or (selection.get('serviceTier') and selection['serviceTier'] not in tiers):
        fail('MODEL_SELECTION_UNAVAILABLE','原运行的推理配置已不可用，请重新选择模型新建问数',422)
    payload['model_selection']={**selection,'catalog_version':catalog['catalog_version']}


def _retrieve(db, row, payload, req, user):
    from .semantic import retrieve, metadata_candidates_for_bundle
    req["intent_frame_hash"] = canonical_hash(req["intent_frame"])
    bundle = retrieve(db, row.workspace_id, user, row.id, req["id"], req["intent_frame"], payload["pinned_deployment"],
        request_id=payload["request_id"], trace_id=payload["trace_id"])
    req["bundle"] = bundle; req["bundle_content_hash"] = canonical_hash(bundle)
    discovery=metadata_candidates_for_bundle(db,row.workspace_id,user,bundle,max_tables=50,max_fields=80,allow_selected=True)
    req["metadata_discovery"]=discovery
    candidate=req.get("metadata_candidate")
    candidate_valid=bool(candidate and discovery and candidate.get("intent_frame_hash")==req.get("intent_frame_hash")
        and candidate.get("metadata_context_hash")==discovery.get("content_hash")
        and candidate.get("release_hash")==bundle.get("release_hash")
        and candidate.get("index_snapshot_hash")==bundle.get("index_snapshot_hash")
        and candidate.get("content_hash")==canonical_hash({k:v for k,v in candidate.items() if k!="content_hash"})
        and bundle.get("next_action")!="BLOCKED_PERMISSION"
        and not any(c.get('status') == 'CONFLICT' for c in bundle.get('coverage', [])))
    if candidate_valid:
        start=candidate["time_range"]["start"];end=candidate["time_range"]["end_exclusive"]
        dims=candidate.get("dimension_fields",[])
        metric_card={"name":candidate["name"],"metric_id":"metadata:"+candidate["candidate_id"],
            "definition":candidate["definition"],"unit":candidate["unit"],"time_expression":candidate["time_field"]["name_zh"],
            "start":start,"end_exclusive":end,"dimensions":[field["name_zh"] for field in dims],
            "scope":"local_validation","business_approved":False,"candidate_only":True,
            "logical_expression":candidate["definition"],"physical_expression":candidate["operation"]+
                ("（"+candidate["metric_field"]["name_zh"]+"）" if candidate.get("metric_field") else "（表记录数）"),
            "physical_source":candidate["physical_source"],"time_field":candidate["time_field"],
            "time_periods":candidate.get("time_periods",[]),"data_availability":req.get("data_availability"),
            "notice":("现有语义路径不能直接完成本次计算。" if bundle.get('selected_metric_ref') else "未命中已发布业务指标。")+"本次口径由固定目录元数据形成，仅用于本地技术验证；不会自动写入或发布语义资产。"}
        req.update(metric_card=metric_card,metric_card_hash=canonical_hash(metric_card),state="NEED_CONFIRMATION",
            error=None,clarifications=[],effective_next_action="NEED_CONFIRMATION")
        event(db,row,payload,"metadata_candidate.confirmation.required",req["id"],
            {"candidate_id":candidate["candidate_id"],"candidate_hash":candidate["content_hash"],"bundle_id":bundle["bundle_id"]})
        return
    from .agent_time import is_comparison
    conflicts = any(c.get('status') == 'CONFLICT' for c in bundle.get('coverage', []))
    fallback = (not bundle.get('selected_metric_ref') or not bundle.get('selected_binding_ref')
                or req['intent_frame'].get('unresolved_dimension_terms')
                or (req['intent_frame'].get('dimension_terms') and not req['intent_frame'].get('dimension_ids'))
                or is_comparison(req['intent_frame']) or bundle['next_action'] not in {'PREVIEW_ONLY','NEED_CONFIRMATION'})
    if fallback and bundle['next_action'] != 'BLOCKED_PERMISSION' and not conflicts:
        from .agent_metadata import queue_metadata
        queue_metadata(db, row, payload, req, discovery)
        return
    if bundle["next_action"] not in {"PREVIEW_ONLY", "NEED_CONFIRMATION"}:
        req["state"] = "NEED_CLARIFICATION" if bundle["next_action"] == "NEED_CLARIFICATION" else "BLOCKED_PERMISSION" if bundle["next_action"] == "BLOCKED_PERMISSION" else "BLOCKED_EVIDENCE"
        req["error"] = {"code": bundle["next_action"], "message": "；".join(c["reason"] for c in bundle["coverage"] if c["status"] != "COMPLETE") or "当前语义路径尚未具备完整证据"}
        if req["state"] == "NEED_CLARIFICATION":
            if not bundle.get("selected_metric_ref"):
                if discovery:
                    labels="、".join(table["table_label"]+"（"+", ".join(field["name_zh"] for field in table["fields"][:8])+"）" for table in discovery["tables"])
                    question="尚未匹配到已发布业务指标。已从固定目录找到以下可访问表/字段候选："+labels+"。请说明统计对象、记录数/去重计数/求和/平均值、时间字段及分组；候选口径须经你确认后才会生成查询。"
                    req["metadata_discovery"]=discovery
                else:
                    question="尚未找到匹配的已发布业务指标或目录字段。请补充具体业务对象、表名或字段名；系统会在当前权限内先查目录，不会猜测物理字段。"
                req["clarifications"] = [{"id": new_id("clarify"), "slot": "semantic_definition", "question": question, "options": []}]
            else:
                req["clarifications"] = [{"id": new_id("clarify"), "slot": "semantic", "question": req["error"]["message"], "options": []}]
        event(db, row, payload, "retrieval.completed", req["id"], {"bundle_id": bundle["bundle_id"], "next_action": bundle["next_action"]})
        return
    from .semantic import get_execution_contract
    try:
        contract = get_execution_contract(db, row.workspace_id, bundle["selected_binding_ref"])
    except HTTPException as exc:
        if not isinstance(exc.detail, dict) or exc.detail.get("code") != "BINDING_NOT_EXECUTABLE":
            raise
        req.update(state="BLOCKED_EVIDENCE", error=exc.detail)
        event(db, row, payload, "binding.blocked", req["id"], exc.detail)
        return
    req["metric_card"] = {"name": contract.get("name", contract["metric_id"]), "metric_id": contract["metric_id"],
        "definition": contract["definition"], "unit": contract.get("unit"), "time_expression": contract["time_expression"],
        "start": req["intent_frame"].get("start"), "end_exclusive": req["intent_frame"].get("end_exclusive"),
        "dimensions": req["intent_frame"].get("dimension_ids", []), "scope": "local_validation", "business_approved": False,
        "logical_expression": contract.get("metric_expression"), "physical_expression": contract.get("expression"),
        "execution_authority": contract.get("authority"), "metric_ref": contract.get("metric_ref"),
        "binding_ref": bundle["selected_binding_ref"],
        "notice": "仅验证本地合成快照的计算结果；不代表业务资产已批准"}
    req["metric_card_hash"] = canonical_hash(req["metric_card"])
    req["state"] = "NEED_CONFIRMATION"
    event(db, row, payload, "metric_confirmation.required", req["id"], {"metric_card_hash": req["metric_card_hash"]})


@router.post("/runs/{run_id}/requirements/{rid}/{action}")
def requirement_command(wid: int, run_id: str, rid: str, action: str, body: dict, request: Request,
        user=Depends(identity), db=Depends(db_session)):
    row = run_row(db, wid, run_id, user, True)
    check_run_access(db, row, user)
    old, meta = command_start(db, request, wid, user, body)
    if old: return old
    payload = copy.deepcopy(row.payload); req = _requirement(row, payload, rid, body)
    if action not in _actions(req, user):
        fail("ACTION_NOT_ALLOWED", "当前状态或身份不允许此操作", 403)
    if action != "cancel": check_run_access(db, row, user)
    try:
        if action == "retry-retrieval":
            req["error"] = None
            req.pop("metadata_candidate", None)
            req.pop("data_availability", None)
            req.pop("data_gaps", None)
            _retrieve(db, row, payload, req, user)
        elif action == "time-periods":
            from .agent_time import confirm_periods
            try:
                queued = confirm_periods(req, body.get('periods'))
            except PlanningBlocked as exc:
                fail(exc.code, exc.message, 422)
            req['answers'][-1].update(actor_id=user.id, at=now().isoformat())
            req['intent_frame_hash'] = canonical_hash(req['intent_frame'])
            if queued:
                req['model_progress'] = {'phase': 'QUEUED', 'label': '补充说明已受理，等待识别', 'started_at': now().isoformat(), 'updated_at': now().isoformat()}
                db.add(AgentJob(id=new_id('job'), workspace_id=wid, run_id=row.id,
                    requirement_id=rid, kind='CLARIFY', status='QUEUED'))
        elif action == "clarifications":
            clarification = next((c for c in req.get("clarifications", []) if c["id"] == body.get("clarification_id")), None)
            if not clarification: fail("CLARIFICATION_STALE", "澄清项已变化")
            from .agent_clarification import apply_answer
            queued = apply_answer(req, clarification, body.get("answer"))
            req["answers"][-1].update(actor_id=user.id, at=now().isoformat())
            req["intent_frame_hash"] = canonical_hash(req["intent_frame"])
            if queued:
                req['model_progress'] = {'phase': 'QUEUED', 'label': '补充说明已受理，等待识别', 'started_at': now().isoformat(), 'updated_at': now().isoformat()}
                db.add(AgentJob(id=new_id("job"), workspace_id=wid, run_id=row.id,
                    requirement_id=rid, kind="CLARIFY", status="QUEUED"))
        elif action == "confirm-intent":
            _selection_ref_match(body, "intent_frame_hash", req["intent_frame_hash"])
            req["target_confirmation"] = {"id": new_id("confirm"), "intent_frame_hash": req["intent_frame_hash"], "actor_id": user.id, "at": now().isoformat()}
            _retrieve(db, row, payload, req, user)
        elif action == "confirm-metric":
            _selection_ref_match(body, "bundle_content_hash", req["bundle_content_hash"])
            _selection_ref_match(body, "metric_card_hash", req["metric_card_hash"])
            confirmation = {"id": new_id("confirm"), "actor_id": user.id, "at": now().isoformat(), "bundle_hash": req["bundle_content_hash"], "metric_card_hash": req["metric_card_hash"]}
            req["metric_confirmation"] = confirmation
            if req.get("metadata_candidate"):
                frame, preview = compile_metadata_preview(req["metadata_candidate"],req["intent_frame"],req["bundle"],row.id,rid,confirmation)
            else:
                from .semantic import get_execution_contract
                contract = get_execution_contract(db, wid, req["bundle"]["selected_binding_ref"])
                frame, preview = compile_preview(contract, req["intent_frame"], req["bundle"], row.id, rid, confirmation)
            req.update(query_frame=frame, query_frame_ref={"id": frame["frame_id"], "revision": frame["revision"], "content_hash": frame["content_hash"]}, preview=preview, sql_hash=preview["sql_hash"], state="PREVIEW_READY")
            event(db, row, payload, "preview.ready", rid, {"query_frame_ref": req["query_frame_ref"], "sql_hash": preview["sql_hash"]})
        elif action == "preview":
            if "preview" not in req: fail("PREVIEW_UNAVAILABLE", "尚无可用预览")
        elif action == "gate":
            _selection_ref_match(body, "query_frame_ref", req["query_frame_ref"])
            _selection_ref_match(body, "sql_hash", req["preview"]["sql_hash"])
            checks = source_check(db, wid, req["query_frame"], req["preview"])
            gate = {"id": new_id("gate"), "status": "PASS", "query_frame_hash": req["query_frame"]["content_hash"],
                "sql_hash": req["preview"]["sql_hash"], "parameters_hash": req["preview"]["parameters_hash"],
                "actor_id": user.id, "checked_at": now().isoformat(), "expires_at": (now() + timedelta(minutes=5)).isoformat(),
                "checks": checks, "scope": "local_validation", "business_approved": False}
            gate["content_hash"] = canonical_hash(gate)
            req.update(gate=gate, gate_result_ref={"id": gate["id"], "content_hash": gate["content_hash"]}, state="GATE_PASSED")
            event(db, row, payload, "gate.completed", rid, {"status": "PASS", "gate_result_ref": req["gate_result_ref"]})
        elif action == "authorize":
            _selection_ref_match(body, "query_frame_ref", req["query_frame_ref"])
            _selection_ref_match(body, "gate_result_ref", req["gate_result_ref"])
            if not body.get("acknowledgement"): fail("ACKNOWLEDGEMENT_REQUIRED", "请明确批准本次查询", 422)
            _selection_ref_match(body, "sql_hash", req["preview"]["sql_hash"])
            if datetime.fromisoformat(req["gate"]["expires_at"]) < now(): fail("GATE_EXPIRED", "安全检查已过期，请重新预览检查")
            source_check(db, wid, req["query_frame"], req["preview"], explain=False)
            auth = {"id": new_id("authorization"), "actor_id": user.id, "workspace_id": wid, "run_id": run_id, "requirement_id": rid,
                "intent_frame_hash": req["intent_frame_hash"], "query_frame_hash": req["query_frame"]["content_hash"],
                "sql_hash": req["preview"]["sql_hash"], "parameters_hash": req["preview"]["parameters_hash"],
                "gate_result_ref": req["gate_result_ref"], "release_hash": req["bundle"]["release_hash"], "index_snapshot_hash": req["bundle"]["index_snapshot_hash"],
                "expires_at": (now() + timedelta(minutes=2)).isoformat(), "state": "AVAILABLE", "scope": "local_validation"}
            req.update(authorization=auth, state="AUTHORIZED")
            event(db, row, payload, "authorization.changed", rid, {"authorization_id": auth["id"], "state": "AVAILABLE"})
        elif action == "execute":
            _selection_ref_match(body, "query_frame_ref", req["query_frame_ref"])
            auth = req["authorization"]
            _selection_ref_match(body, "authorization_id", auth["id"])
            _selection_ref_match(body, "sql_hash", req["preview"]["sql_hash"])
            if auth["state"] != "AVAILABLE" or datetime.fromisoformat(auth["expires_at"]) < now():
                fail("AUTHORIZATION_EXPIRED", "本次授权已使用或过期")
            if auth["query_frame_hash"] != req["query_frame"]["content_hash"] or auth["parameters_hash"] != canonical_hash(req["preview"]["parameters"]):
                fail("AUTHORIZATION_SCOPE_MISMATCH", "授权与当前查询不符")
            source_check(db, wid, req["query_frame"], req["preview"], explain=False)
            execution = AgentExecution(id=new_id("execution"), workspace_id=wid, run_id=run_id, requirement_id=rid,
                authorization_id=auth["id"], frame_hash=auth["query_frame_hash"], sql_hash=auth["sql_hash"], status="QUEUED")
            db.add(execution); auth["state"] = "CONSUMED"; req.update(execution_operation_id=execution.id, state="EXECUTING")
            db.add(AgentJob(id=new_id("job"), workspace_id=wid, run_id=run_id, requirement_id=rid, kind="EXECUTE", status="QUEUED"))
        elif action == "cancel":
            if req['state'] == 'DRAFT':
                _cancel_model_jobs(db, row.id, rid)
            if req["state"] == "EXECUTING":
                req["state"] = "CANCEL_REQUESTED"
                _cancel_execution(db, wid, req)
            else:
                req["state"] = "CANCELLED"
            if req.get("authorization"): req["authorization"]["state"] = "REVOKED"
    except PlanningBlocked as exc:
        req.update(state="BLOCKED_POLICY" if action in {"gate", "authorize", "execute"} else "BLOCKED_EVIDENCE", error={"code": exc.code, "message": exc.message})
        if req.get("authorization"): req["authorization"]["state"] = "REVOKED"
    req["requirement_version"] += 1
    event(db, row, payload, "requirement.state_changed", rid, {"state": req["state"], "reason": req.get("error")})
    save_run(db, row, payload); _audit(db, user, wid, action, row.id + "/" + rid)
    return command_done(db, meta, envelope(wid, {"run": view(row, user)}))


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(wid: int, run_id: str, body: dict, request: Request, user=Depends(identity), db=Depends(db_session)):
    row = run_row(db, wid, run_id, user, True)
    check_run_access(db, row, user)
    old, meta = command_start(db, request, wid, user, body)
    if old: return old
    if body.get("expected_run_version") != row.run_version: fail("RUN_VERSION_CONFLICT", "运行已变化，请刷新")
    payload = copy.deepcopy(row.payload)
    for req in payload.get("requirements", []):
        if req["state"] not in TERMINAL and req["state"] != "EXECUTION_UNKNOWN":
            if req['state'] == 'DRAFT': _cancel_model_jobs(db, row.id, req['id'])
            if req["state"] == "EXECUTING": _cancel_execution(db, wid, req)
            req["state"] = "CANCEL_REQUESTED" if req["state"] in {"EXECUTING", "CANCEL_REQUESTED"} else "CANCELLED"
            req["requirement_version"] += 1
            if req.get("authorization"): req["authorization"]["state"] = "REVOKED"
    if not payload.get("requirements"): payload["state"] = "CANCEL_REQUESTED"
    payload["cancel_requested"] = True
    if not payload.get("requirements"):
        from .model_runtime import cancel
        try: cancel(payload["request_id"])
        except Exception: pass
    event(db, row, payload, "cancellation.updated", detail={"requested": True})
    save_run(db, row, payload)
    return command_done(db, meta, envelope(wid, {"run": view(row, user)}))


def _cancel_model_jobs(db, run_id, requirement_id):
    from .model_runtime import cancel
    jobs=db.scalars(select(AgentJob).where(AgentJob.run_id==run_id,
        AgentJob.requirement_id==requirement_id,AgentJob.kind.in_(['CLARIFY','METADATA']),
        AgentJob.status.in_(['QUEUED','RUNNING']))).all()
    for job in jobs:
        if job.status=='RUNNING':
            try: cancel(job.id+':metadata' if job.kind=='METADATA' else job.id)
            except Exception: pass
        job.status='CANCELLED'


def _cancel_execution(db, wid, req):
    execution = db.get(AgentExecution, req.get("execution_operation_id"))
    if not execution or execution.workspace_id != wid or not execution.backend_pid:
        return
    source = db.get(Datasource, req["query_frame"]["datasource_id"])
    if not source or source.workspace_id != wid: return
    from .warehouse import connect
    try:
        with connect(source) as conn:
            # Guard against pid reuse and cancellation of unrelated SQL sessions.
            conn.execute("SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE pid=%s AND usename=current_user AND application_name=%s",
                (execution.backend_pid, "agent:" + execution.id))
    except Exception:
        # The state remains CANCEL_REQUESTED until the worker confirms a fact.
        pass


@router.post("/runs/{run_id}/feedback", status_code=201)
def feedback(wid: int, run_id: str, body: dict, request: Request, user=Depends(identity), db=Depends(db_session)):
    row = run_row(db, wid, run_id, user)
    old, meta = command_start(db, request, wid, user, body)
    if old: return old
    if len(json.dumps(body, ensure_ascii=False)) > 8000: fail("FEEDBACK_TOO_LARGE", "反馈过长", 422)
    item = AgentFeedback(id=new_id("feedback"), workspace_id=wid, run_id=run_id, actor_id=user.id, payload=body)
    db.add(item); _audit(db, user, wid, "feedback.create", item.id)
    from .pipeline_models import GovernanceItem
    db.add(GovernanceItem(workspace_id=wid,kind="issue",name="问数反馈："+run_id[:60],actor_id=user.id,
        content={"category":"feedback","description":json.dumps(body,ensure_ascii=False)[:4000],"event_key":"agent-feedback:"+item.id},history=[]))
    return command_done(db, meta, envelope(wid, {"feedback_id": item.id, "state": "OPEN", "asset_changed": False}))


@router.get("/runs/{run_id}/requirements/{rid}/result")
def result(wid: int, run_id: str, rid: str, user=Depends(identity), db=Depends(db_session)):
    row = run_row(db, wid, run_id, user)
    check_run_access(db, row, user)
    req = next((r for r in row.payload.get("requirements", []) if r["id"] == rid), None)
    if not req or req["state"] != "DELIVERED": fail("RESULT_NOT_READY", "结果尚未交付")
    return envelope(wid, {"result": req["result"]})


@router.get("/runs/{run_id}/events")
def events(wid: int, run_id: str, request: Request, user=Depends(identity), db=Depends(db_session)):
    row = run_row(db, wid, run_id, user)
    check_run_access(db, row, user)
    session_hash = request.state.session.token_hash
    try: after = int(request.headers.get("Last-Event-ID", request.query_params.get("after_event_id", "0")))
    except ValueError: fail("EVENT_CURSOR_INVALID", "事件游标无效", 422)
    # Bounded streams reconnect; each reconnect revalidates the session and workspace.
    def stream():
        last, deadline = after, time.monotonic() + 20
        while time.monotonic() < deadline:
            with SessionLocal() as current:
                active_user = current.get(User, user.id)
                if not active_user or not active_user.enabled: return
                session = current.get(LoginSession, session_hash)
                if not session or session.expires_at < now(): return
                try: workspace_access(current, active_user, wid)
                except HTTPException: return
                active_run = current.get(AgentRun, run_id)
                try: check_run_access(current, active_run, active_user)
                except HTTPException: return
                rows = current.scalars(select(AgentEvent).where(AgentEvent.run_id == run_id,
                    AgentEvent.workspace_id == wid, AgentEvent.event_id > last).order_by(AgentEvent.event_id)).all()
                for item in rows:
                    last = item.event_id
                    yield f"id: {item.event_id}\nevent: {item.event_type}\ndata: {json.dumps(item.payload, ensure_ascii=False)}\n\n"
                active = current.get(AgentRun, run_id)
                if active and active.state in TERMINAL: return
            yield ": heartbeat\n\n"
            time.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
