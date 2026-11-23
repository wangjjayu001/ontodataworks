"""Standards-derived quality, scheduler gap diagnostics and bounded maintenance."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import Depends, HTTPException
from sqlalchemy import select, func, delete
from .db import User, LoginSession, now
from .pipeline import router, identity, db_session, owned, require_actor, audit, out, cron_match, create_definition
from .pipeline_models import GovernanceItem, Definition, DefinitionRevision, PipelineRun, ServiceCall
from .pipeline_contracts import DefinitionBody
from .pipeline_catalog import digest


@router.post("/publications/{pid}/reconcile")
def reconcile_ddl(wid:int,pid:str,user=Depends(identity),db=Depends(db_session)):
    from .pipeline_models import Publication
    from .pipeline_publication import reconcile_publication
    require_actor(db,user,wid,True)
    if user.role!="admin":raise HTTPException(403,"发布对账需管理员")
    row=owned(db,Publication,wid,pid)
    return out(reconcile_publication(db,wid,row))


@router.post("/governance/{iid}/quality-template")
def quality_template(wid: int, iid: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db, user, wid, True); standard = owned(db, GovernanceItem, wid, iid)
    if standard.kind != "standard" or standard.deleted or standard.status != "APPROVED": raise HTTPException(409, "质量模板只从已批准且未撤回的标准派生")
    target = body.get("asset_id"); bindings = [b for b in standard.content["bindings"] if b["asset_id"] == target]
    if not bindings: raise HTTPException(409, "目标没有引用本标准的字段绑定")
    from .db import Asset
    asset = owned(db, Asset, wid, target); rules = []
    for binding in bindings:
        if standard.content["category"] == "codebook" and standard.content["values"]:
            rules.append({"kind": "enum", "column": binding["column"], "values": list(standard.content["values"]), "severity": "error"})
        elif standard.content["category"] == "null" and standard.content["value"] == "not_null":
            rules.append({"kind": "not_null", "column": binding["column"], "severity": "error"})
    rules.extend(standard.content["rules"])
    if not rules: raise HTTPException(409, "该标准没有可执行规则；单位、币种和时区说明不能自动变成检查结果")
    return create_definition(wid, DefinitionBody(kind="quality", name="标准校验："+standard.name[:120], config={"datasource_id":asset.datasource_id,"target_asset_id":target,"rules":rules,
        "standard_refs":[{"id":iid,"revision":standard.revision,"content_hash":digest(standard.content)}]}), user, db)


@router.get("/definitions/{did}/schedule-gaps")
def schedule_gaps(wid: int, did: int, start: datetime, end_exclusive: datetime, user=Depends(identity), db=Depends(db_session)):
    require_actor(db,user,wid); definition=owned(db,Definition,wid,did)
    if start.tzinfo is None or end_exclusive.tzinfo is None or not timedelta(0)<end_exclusive-start<=timedelta(days=7): raise HTTPException(400,"诊断需指定带时区的起止时间，最多七天")
    schedule=definition.config.get("schedule")
    if not schedule: raise HTTPException(409,"当前修订未配置周期")
    current=start.replace(second=0,microsecond=0); expected=[]
    actual={r.request_key for r in db.scalars(select(PipelineRun).where(PipelineRun.definition_id==did,PipelineRun.revision==definition.revision))}
    while current<end_exclusive:
        local=current.astimezone(ZoneInfo(schedule["timezone"]))
        if cron_match(schedule["cron"],local):
            key=f"cron:{did}:{definition.revision}:"+local.strftime("%Y%m%dT%H%M%z")
            expected.append({"scheduled_at":local.isoformat(),"request_key":key,"observed":key in actual})
        current+=timedelta(minutes=1)
    return {"definition_id":did,"revision":definition.revision,"periods":expected,"missing":sum(not e["observed"] for e in expected),"historical_enablement":"按当前固定修订诊断，停用区间不会冒充实际漏跑"}


@router.get("/ops/retention")
def retention_preview(wid: int, days: int=90, user=Depends(identity), db=Depends(db_session)):
    require_actor(db,user,wid)
    if days<30 or days>3650: raise HTTPException(400,"运行日志保留期为 30 至 3650 天")
    count=db.scalar(select(func.count()).select_from(ServiceCall).where(ServiceCall.workspace_id==wid,ServiceCall.created_at<now()-timedelta(days=days),ServiceCall.status!="TESTED"))
    return {"days":days,"eligible_service_logs":count,"immutable_revisions_batches_and_observations":"retained","test_receipts":"retained","result_cache":"disabled"}


@router.post("/ops/retention")
def retain(wid: int, body: dict, user=Depends(identity), db=Depends(db_session)):
    require_actor(db,user,wid,True)
    if user.role!="admin": raise HTTPException(403,"清理日志需管理员")
    days=body.get("days",90); preview=retention_preview(wid,days,user,db)
    removed=db.execute(delete(ServiceCall).where(ServiceCall.workspace_id==wid,ServiceCall.created_at<now()-timedelta(days=days),ServiceCall.status!="TESTED")).rowcount
    sessions=db.execute(delete(LoginSession).where(LoginSession.expires_at<now())).rowcount
    audit(db,wid,user,"retention.execute",f"service_logs={removed}; expired_sessions={sessions}; retention_days={days}");db.commit()
    return {**preview,"removed_service_logs":removed,"removed_expired_sessions":sessions}
