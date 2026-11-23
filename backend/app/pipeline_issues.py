"""Close issues using evidence appropriate to their original problem type."""
from fastapi import HTTPException
from sqlalchemy import select
from .db import Asset, now
from .pipeline_models import PipelineRun, CatalogObservation
from .pipeline_catalog import owned


def issue_category(db, wid, content):
    if content.get("category"):
        return content["category"]
    event = content.get("event_key") or ""
    if event.startswith("agent-feedback:"):
        return "feedback"
    if event.startswith("catalog:"):
        return "schema"
    if content.get("run_id"):
        run = owned(db, PipelineRun, wid, content["run_id"])
        quality_failed = any(c["status"] != "PASS" and c["rule"]["severity"] == "error" for c in run.evidence.get("quality", []))
        return "quality" if run.snapshot["kind"] == "quality" or quality_failed else "task"
    return "quality"


def validate_issue(db, wid, body, content, user, old=None):
    category = content["category"]
    if body.status not in {"OPEN", "ASSIGNED", "FIXED", "CLOSED"}:
        raise HTTPException(422, "问题状态无效")
    if old:
        if category != issue_category(db, wid, old.content):
            raise HTTPException(409, "不能通过改变问题类型绕过复核要求")
        for field in ("run_id", "asset_id"):
            if old.content.get(field) and content.get(field) != old.content[field]:
                raise HTTPException(409, "问题来源和关联资产不能替换")
    runs = {key: owned(db, PipelineRun, wid, content[key]) for key in ("run_id", "repair_run_id", "retest_run_id") if content.get(key)}
    origin = runs.get("run_id")
    asset_id = content.get("asset_id") or (origin.snapshot["config"].get("target_asset_id") if origin else None)
    if asset_id:
        owned(db, Asset, wid, asset_id)
        content["asset_id"] = asset_id
    if category != "quality" and body.status in {"FIXED", "CLOSED"} and not content["resolution"].strip():
        raise HTTPException(409, "请记录修复说明")
    # Verification identity is always stamped by the server at closure.
    content["verification_actor_id"] = None
    content["verified_at"] = None
    if body.status != "CLOSED":
        return content
    if not old or old.status != "FIXED":
        raise HTTPException(409, "先保存已修复记录，再验证并关闭问题")
    if category in {"quality", "task"}:
        retest = runs.get("retest_run_id")
        if not retest or retest.status != "SUCCEEDED":
            raise HTTPException(409, "关闭问题需要成功的修复后验证运行")
        if category == "quality":
            if retest.snapshot["kind"] != "quality" or not asset_id or retest.snapshot["config"].get("target_asset_id") != asset_id:
                raise HTTPException(409, "质量复测必须对应原问题资产")
            if retest.created_at <= old.updated_at:
                raise HTTPException(409, "质量复测应在本次修复记录之后独立执行")
        else:
            if not origin or retest.definition_id != origin.definition_id:
                raise HTTPException(409, "执行异常需同一任务的成功验证，不能用其他任务替代")
            if not retest.completed_at or retest.completed_at <= old.updated_at:
                raise HTTPException(409, "任务验证须在本次修复记录之后完成")
    if category in {"schema", "semantic", "feedback"}:
        repair_actor = old.history[-1].get("actor_id", old.actor_id) if old.history else old.actor_id
        if user.id in {repair_actor, content.get("owner_id"), old.content.get("owner_id")}:
            raise HTTPException(403, "请由修复人和责任人以外的成员独立复核")
    if category != "quality" and not content["verification_note"].strip():
        raise HTTPException(409, "请填写验证结果和关闭依据")
    if category == "schema":
        if not asset_id or not content.get("verification_observation_id"):
            raise HTTPException(409, "结构问题需要关联资产和修复后目录观察")
        asset = owned(db, Asset, wid, asset_id)
        observation = owned(db, CatalogObservation, wid, content["verification_observation_id"])
        latest = db.scalar(select(CatalogObservation).where(CatalogObservation.workspace_id == wid, CatalogObservation.datasource_id == asset.datasource_id).order_by(CatalogObservation.id.desc()).limit(1))
        actual = next((a for a in observation.content if a["namespace"] == asset.namespace and a["table_name"] == asset.table_name), None)
        if observation.datasource_id != asset.datasource_id or not latest or latest.id != observation.id or observation.created_at <= old.updated_at:
            raise HTTPException(409, "需使用同一连接最新且在修复之后采集的目录观察")
        if asset.state != "active" or not actual or actual["fingerprint"] != asset.fingerprint:
            raise HTTPException(409, "实际目录与当前资产尚未一致")
    if category == "semantic":
        from .semantic import resolve_asset, can_read_asset, validity_active
        from .semantic_models import SemanticGovernance, SemanticVerification
        if not content.get("semantic_ref"):
            raise HTTPException(409, "语义问题需引用修复后的确切资产版本")
        asset = resolve_asset(db, wid, content["semantic_ref"])
        gov = db.get(SemanticGovernance, (wid, asset["asset_id"]))
        receipt = db.scalar(select(SemanticVerification).where(SemanticVerification.workspace_id == wid, SemanticVerification.asset_id == asset["asset_id"], SemanticVerification.revision == asset["revision"], SemanticVerification.created_at > old.updated_at).limit(1))
        if not can_read_asset(db, wid, user, asset) or not validity_active(asset["validity"]) or not gov or gov.revision != asset["revision"] or gov.validation_state != "PASS" or not receipt:
            raise HTTPException(409, "语义资产需当前有效版本，并在修复后完成核验；待办关闭不代替业务批准")
    content["verification_actor_id"] = user.id
    content["verified_at"] = now().isoformat()
    return content
