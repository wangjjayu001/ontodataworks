import hashlib
import json
import os
import re
import secrets
import time
from decimal import Decimal
from contextlib import asynccontextmanager
from datetime import timedelta
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func, text, delete
from sqlalchemy.exc import IntegrityError
import psycopg
from psycopg import sql
from .db import *
from .security import identity, db_session, workspace_access, hash_password, verify_password
from .warehouse import cipher, connect, inspect_catalog, read_query, table_refs, model_ddl, IDENTIFIER, LAYERS

# Capture legacy tables before new modules register their explicit-migration tables.
LEGACY_TABLES = list(Base.metadata.sorted_tables)
from .semantic import router as semantic_router
from .agent import router as agent_router
from .pipeline import router as pipeline_router, service_router
from . import pipeline_extensions


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine, tables=LEGACY_TABLES)
    with SessionLocal() as db:
        if not db.get(SchemaVersion, 1):
            db.add(SchemaVersion(version=1))
        if not db.get(SchemaVersion, 2):
            db.add(SchemaVersion(version=2))
        if not db.get(SchemaVersion, 3):
            db.add(SchemaVersion(version=3))
        if not db.scalar(select(User.id)):
            user = User(username="admin", display_name="开发者", password_hash=hash_password(os.environ["INITIAL_ADMIN_PASSWORD"]), role="admin")
            db.add(user)
            db.add(Workspace(name="本地开发空间", description="独立数据平台的默认工作空间"))
        db.commit()
    yield


app = FastAPI(title="OntoDataWorks", version="1.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(semantic_router)
app.include_router(agent_router)
app.include_router(pipeline_router)
app.include_router(service_router)
from .observability import install as install_observability
install_observability(app)


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse(status_code=409, content={"detail": "编码已存在或资源仍被引用，请检查后重试"})


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class DocumentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=4000)
    domain_id: int | None = None
    layer: str = ""
    status: str = "draft"
    spec: dict = Field(default_factory=dict)
    revision: int | None = None


class SourceBody(BaseModel):
    name: str = Field(min_length=1,max_length=100)
    host: str = Field(min_length=1,max_length=255)
    port: int = Field(default=5432,ge=1,le=65535)
    database: str = Field(min_length=1,max_length=100)
    username: str = Field(min_length=1,max_length=100)
    password: str = Field(default="",max_length=200)
    schemas: list[str] = Field(default_factory=lambda:["public"],min_length=1,max_length=20)


class QueryBody(BaseModel):
    datasource_id: int
    sql: str = Field(min_length=1,max_length=40000)


def record_audit(db, user, wid, action, detail=""):
    db.add(Audit(workspace_id=wid, actor=user.username if user else "anonymous", action=action, detail=detail))


def row_dict(row):
    return {c.name: getattr(row,c.name) for c in row.__table__.columns if c.name not in {"secret","password_hash","token_hash","csrf"}}


def owned(db, cls, wid, rid):
    row = db.get(cls,rid)
    if not row or row.workspace_id != wid:
        raise HTTPException(404,"资源不存在")
    return row


def documents(db, wid, kind):
    return db.scalars(select(Resource).where(Resource.workspace_id==wid,Resource.kind==kind).order_by(Resource.updated_at.desc())).all()


def validate_document(db, wid, kind, body, rid=None):
    if kind not in {"domain","dimension","metric","model"}:
        raise HTTPException(404,"不支持的建模文档类型")
    if not IDENTIFIER.fullmatch(body.code):
        raise HTTPException(400,"编码使用小写字母开头的英文、数字和下划线，最长 63 位")
    if body.status not in {"draft","published"}:
        raise HTTPException(400,"状态必须为 draft 或 published")
    if kind != "domain":
        domain = owned(db,Resource,wid,body.domain_id) if body.domain_id else None
        if not domain or domain.kind != "domain":
            raise HTTPException(400,"请选择当前工作空间的业务域")
    spec = body.spec
    if kind == "domain" and body.domain_id:
        parent = owned(db,Resource,wid,body.domain_id)
        if parent.kind != "domain" or parent.id == rid:
            raise HTTPException(400,"父级业务域无效")
        seen = {rid}
        while parent:
            if parent.id in seen:
                raise HTTPException(400,"业务域层级存在循环")
            seen.add(parent.id)
            parent = db.get(Resource,parent.domain_id) if parent.domain_id else None
    if kind == "model":
        if body.layer not in LAYERS or not body.code.startswith(body.layer.lower()+"_"):
            raise HTTPException(400,"模型编码必须以所选分层前缀开头，例如 dwd_order_detail")
        if not spec.get("grain"):
            raise HTTPException(400,"请明确模型的一行代表什么，即数据粒度")
        temp = Resource(code=body.code,spec=spec)
        model_ddl(temp)
        names = [f["name"] for f in spec["fields"]]
        if len(names) != len(set(names)):
            raise HTTPException(400,"模型字段名称不能重复")
    if kind == "dimension":
        attributes = spec.get("attributes",[])
        if not attributes or not spec.get("business_key"):
            raise HTTPException(400,"维度需要业务主键和属性列表")
        names = [a.get("name","") for a in attributes]
        if any(not IDENTIFIER.fullmatch(n) for n in names) or len(names) != len(set(names)) or spec["business_key"] not in names:
            raise HTTPException(400,"维度属性编码必须唯一，且业务主键需存在于属性列表")
    if kind == "metric":
        metric_type = spec.get("metric_type","atomic")
        if metric_type == "atomic":
            if spec.get("aggregation") not in {"SUM","COUNT","COUNT_DISTINCT","AVG","MIN","MAX"} or not spec.get("business_process") or not spec.get("unit"):
                raise HTTPException(400,"原子指标需要业务过程、聚合方式和单位")
            if body.status == "published":
                asset = owned(db,Asset,wid,spec.get("asset_id"))
                if asset.state != "active" or spec.get("measure") not in [c["name"] for c in asset.columns]:
                    raise HTTPException(400,"发布原子指标需要引用已采集且有效的真实字段")
        elif metric_type == "derived":
            parent = owned(db,Resource,wid,spec.get("atomic_metric_id"))
            if parent.kind != "metric" or parent.spec.get("metric_type","atomic") != "atomic" or parent.id == rid:
                raise HTTPException(400,"派生指标必须引用当前空间的原子指标")
            if not spec.get("time_window"):
                raise HTTPException(400,"派生指标需要时间窗口")
            if body.status == "published" and parent.status != "published":
                raise HTTPException(400,"请先发布引用的原子指标")
            for dimension_id in spec.get("dimension_ids",[]):
                if owned(db,Resource,wid,dimension_id).kind != "dimension":
                    raise HTTPException(400,"维度引用无效")
        else:
            raise HTTPException(400,"当前支持原子指标和派生指标")


@app.get("/api/health")
def health(db=Depends(db_session)):
    db.execute(text("SELECT 1"))
    return {"status":"ok","application":"independent-workbench","schema_version":db.scalar(select(func.max(SchemaVersion.version)))}


@app.post("/api/auth/login")
def login(body:LoginBody,request:Request,response:Response,db=Depends(db_session)):
    failures = db.scalar(select(func.count(Audit.id)).where(Audit.actor==body.username,Audit.action=="login.failed",Audit.created_at>now()-timedelta(minutes=15)))
    if failures >= 10:
        raise HTTPException(429,"登录失败过多，请 15 分钟后重试")
    user = db.scalar(select(User).where(User.username==body.username))
    valid = verify_password(body.password, user.password_hash if user else hash_password("dummy-password"))
    if not user or not user.enabled or not valid:
        db.add(Audit(actor=body.username,action="login.failed",detail=request.client.host if request.client else ""))
        db.commit()
        raise HTTPException(401,"账号或密码错误")
    token, csrf = secrets.token_urlsafe(48),secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,csrf=csrf,expires_at=now()+timedelta(hours=8)))
    record_audit(db,user,None,"login.success")
    db.commit()
    response.set_cookie("wb_session",token,httponly=True,samesite="strict",max_age=28800,secure=os.environ.get("COOKIE_SECURE")=="true")
    return {"user":row_dict(user),"csrf":csrf}


@app.get("/api/auth/me")
def me(request:Request,user=Depends(identity),db=Depends(db_session)):
    query = select(Workspace).order_by(Workspace.id)
    if user.role != "admin":
        query = query.join(Membership,Membership.workspace_id==Workspace.id).where(Membership.user_id==user.id)
    spaces=[]
    for workspace in db.scalars(query):
        row=row_dict(workspace)
        row['active_assets']=db.scalar(select(func.count(Asset.id)).where(Asset.workspace_id==workspace.id,Asset.state=='active'))
        spaces.append(row)
    return {"user":row_dict(user),"csrf":request.state.session.csrf,"workspaces":spaces}


@app.post("/api/auth/logout")
def logout(request:Request,response:Response,user=Depends(identity),db=Depends(db_session)):
    db.delete(db.get(LoginSession,request.state.session.token_hash))
    db.commit()
    response.delete_cookie("wb_session")
    return {"ok":True}


@app.post("/api/workspaces")
def add_workspace(body:dict,user=Depends(identity),db=Depends(db_session)):
    if user.role not in {"admin","developer"}:
        raise HTTPException(403,"没有创建权限")
    name = str(body.get("name","")).strip()
    if not name or len(name)>100:
        raise HTTPException(400,"请输入工作空间名称，最长 100 字")
    row = Workspace(name=name,description=str(body.get("description",""))[:2000])
    db.add(row);db.flush()
    db.add(Membership(user_id=user.id,workspace_id=row.id))
    record_audit(db,user,row.id,"workspace.create",name)
    db.commit()
    return row_dict(row)


@app.get("/api/workspaces/{wid}/overview")
def overview(wid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid)
    counts = {kind:db.scalar(select(func.count(Resource.id)).where(Resource.workspace_id==wid,Resource.kind==kind)) for kind in ["domain","dimension","metric","model"]}
    for key,cls in [("datasource",Datasource),("asset",Asset),("query",QueryRun),("lineage",Lineage)]:
        counts[key] = db.scalar(select(func.count(cls.id)).where(cls.workspace_id==wid))
    events = db.scalars(select(Audit).where(Audit.workspace_id==wid).order_by(Audit.id.desc()).limit(8)).all()
    return {"counts":counts,"activity":[row_dict(e) for e in events],"layers":[{"code":c,"name":n,"description":d} for c,n,d in [("ODS","原始数据层","按来源保留原始业务记录"),("DWD","明细数据层","按业务过程明确粒度，清洗并关联维度"),("DWS","汇总数据层","按主题、维度与时间窗口汇总"),("DIM","公共维度层","维护一致维度和业务主键"),("ADS","应用数据层","面向具体消费场景提供数据")]]}


@app.get("/api/workspaces/{wid}/documents/{kind}")
def get_documents(wid:int,kind:str,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid)
    return [row_dict(r) for r in documents(db,wid,kind)]


@app.post("/api/workspaces/{wid}/documents/{kind}")
def create_document(wid:int,kind:str,body:DocumentBody,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid,True);validate_document(db,wid,kind,body)
    values=body.model_dump(exclude={"revision"})
    row=Resource(workspace_id=wid,kind=kind,**values);db.add(row);db.flush()
    db.add(ResourceRevision(resource_id=row.id,revision=1,snapshot=jsonable_encoder(row_dict(row)),actor=user.username))
    record_audit(db,user,wid,f"{kind}.create",body.code);db.commit()
    return row_dict(row)


@app.put("/api/workspaces/{wid}/documents/{kind}/{rid}")
def update_document(wid:int,kind:str,rid:int,body:DocumentBody,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid,True)
    row=db.scalar(select(Resource).where(Resource.id==rid,Resource.workspace_id==wid).with_for_update())
    if not row or row.kind!=kind:raise HTTPException(404,"文档不存在")
    if body.revision != row.revision:raise HTTPException(409,"文档已被修改，请刷新后重试")
    validate_document(db,wid,kind,body,rid)
    for key,value in body.model_dump(exclude={"revision"}).items():setattr(row,key,value)
    row.revision+=1;row.updated_at=now();db.flush()
    db.add(ResourceRevision(resource_id=rid,revision=row.revision,snapshot=jsonable_encoder(row_dict(row)),actor=user.username))
    record_audit(db,user,wid,f"{kind}.update",f"{row.code} v{row.revision}");db.commit()
    return row_dict(row)


@app.get("/api/workspaces/{wid}/documents/{kind}/{rid}/revisions")
def revisions(wid:int,kind:str,rid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid);owned(db,Resource,wid,rid)
    return [row_dict(r) for r in db.scalars(select(ResourceRevision).where(ResourceRevision.resource_id==rid).order_by(ResourceRevision.revision.desc()))]


@app.delete("/api/workspaces/{wid}/documents/{kind}/{rid}")
def delete_document(wid:int,kind:str,rid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid,True);row=owned(db,Resource,wid,rid)
    if row.kind!=kind:raise HTTPException(404,"文档不存在")
    for resource in db.scalars(select(Resource).where(Resource.workspace_id==wid,Resource.id!=rid)):
        if resource.domain_id==rid or resource.spec.get("atomic_metric_id")==rid or rid in resource.spec.get("dimension_ids",[]):
            raise HTTPException(409,"资源仍被其他建模文档引用")
    if db.scalar(select(Asset.id).where(Asset.domain_id==rid)):raise HTTPException(409,"业务域仍有关联资产")
    db.execute(delete(ResourceRevision).where(ResourceRevision.resource_id==rid));db.delete(row)
    record_audit(db,user,wid,f"{kind}.delete",row.code);db.commit()
    return {"ok":True}


@app.get("/api/workspaces/{wid}/models/{rid}/ddl")
def ddl(wid:int,rid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid);row=owned(db,Resource,wid,rid)
    if row.kind!="model":raise HTTPException(400,"请选择逻辑模型")
    return {"sql":model_ddl(row),"revision":row.revision,"dialect":"postgres"}


@app.post("/api/workspaces/{wid}/models/{rid}/materialize")
def materialize(wid:int,rid:int,body:dict,user=Depends(identity),db=Depends(db_session)):
    from .pipeline_publication import make_plan,publish
    workspace_access(db,user,wid,True)
    if user.role!="admin":raise HTTPException(403,"物理发布需要管理员")
    plan=make_plan(db,wid,user,rid,body.get("datasource_id"));db.commit()
    result=publish(db,wid,plan)
    return {"ok":result.status=="SUCCEEDED","publication_id":result.id,"status":result.status,"sql":";\n".join(result.plan["commands"]),"next":"已回读物理结构并自动登记目录","evidence":result.evidence}



@app.get("/api/workspaces/{wid}/datasources")
def sources(wid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid)
    return [row_dict(s) for s in db.scalars(select(Datasource).where(Datasource.workspace_id==wid).order_by(Datasource.id))]


@app.post("/api/workspaces/{wid}/datasources")
def add_source(wid:int,body:SourceBody,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid,True)
    if not body.password or any(not IDENTIFIER.fullmatch(s) or s in {"pg_catalog","information_schema"} for s in body.schemas):raise HTTPException(400,"请输入密码和有效业务 Schema")
    source=Datasource(workspace_id=wid,**body.model_dump(exclude={"password"}),secret=cipher.encrypt(body.password.encode()).decode())
    db.add(source);record_audit(db,user,wid,"datasource.create",source.name);db.commit()
    return row_dict(source)


@app.put("/api/workspaces/{wid}/datasources/{sid}")
def edit_source(wid:int,sid:int,body:SourceBody,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid,True);source=owned(db,Datasource,wid,sid)
    if any(not IDENTIFIER.fullmatch(s) or s in {"pg_catalog","information_schema"} for s in body.schemas):raise HTTPException(400,"Schema 无效")
    if body.password or (source.host,source.port,source.database,source.username,source.schemas)!=(body.host,body.port,body.database,body.username,body.schemas):
        from .pipeline_models import SourceUse
        usage=db.get(SourceUse,sid)
        if usage:usage.purpose="read"
    if source.host!=body.host or source.database!=body.database or source.schemas!=body.schemas:
        for asset in db.scalars(select(Asset).where(Asset.datasource_id==sid)):asset.state="stale"
    for k,v in body.model_dump(exclude={"password"}).items():setattr(source,k,v)
    if body.password:source.secret=cipher.encrypt(body.password.encode()).decode()
    source.last_tested_at=None;source.last_collected_at=None
    record_audit(db,user,wid,"datasource.update",source.name);db.commit()
    return row_dict(source)


@app.post("/api/workspaces/{wid}/datasources/{sid}/test")
def test_source(wid:int,sid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid,True);source=owned(db,Datasource,wid,sid)
    try:
        with connect(source) as conn:conn.execute("SELECT 1").fetchone()
    except psycopg.Error:raise HTTPException(400,"连接失败，请检查主机、端口、账号和数据库；密码不会返回到前端")
    source.last_tested_at=now();record_audit(db,user,wid,"datasource.test",source.name);db.commit()
    return {"ok":True,"tested_at":source.last_tested_at}


@app.post("/api/workspaces/{wid}/datasources/{sid}/collect")
def collect(wid:int,sid:int,user=Depends(identity),db=Depends(db_session)):
    from .pipeline_catalog import observe_catalog
    workspace_access(db,user,wid,True);source=owned(db,Datasource,wid,sid)
    try: result=observe_catalog(db,source,actor_id=user.id)
    except psycopg.Error:raise HTTPException(400,"采集失败，请检查数据源连接和读取权限")
    record_audit(db,user,wid,"metadata.collect",f"{source.name}: {result['tables']} tables");db.commit()
    return {**result,"collected_at":source.last_collected_at}



@app.get("/api/workspaces/{wid}/assets")
def assets(wid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid)
    maintained={m.asset_id:m for m in db.scalars(select(CatalogMetadata).where(CatalogMetadata.workspace_id==wid))}
    result=[]
    for asset in db.scalars(select(Asset).where(Asset.workspace_id==wid).order_by(Asset.table_name)):
        row=row_dict(asset);metadata=maintained.get(asset.id)
        row['governance']=row_dict(metadata) if metadata else None
        definitions={c['name']:c for c in metadata.spec.get('columns',[])} if metadata else {}
        row['columns']=[{**c,'business_metadata':definitions.get(c['name'])} for c in asset.columns]
        row['maintained_columns']=sum(bool(definitions.get(c['name'],{}).get('chinese_name')) for c in asset.columns)
        row['metadata_state']='complete' if metadata and row['maintained_columns']==len(asset.columns) and not (set(definitions)-{c['name'] for c in asset.columns}) else 'needs_maintenance'
        result.append(row)
    return result


class ColumnDefinitionBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=63)
    chinese_name: str = Field(min_length=1,max_length=80)
    definition: str = Field(min_length=1,max_length=2000)
    semantic_role: str
    classification: str = 'internal'
    unit: str = Field(default='',max_length=40)
    quality_rules: list[str] = Field(default_factory=list,max_length=20)
    enum_values: list[str] = Field(default_factory=list,max_length=100)


class CatalogDefinitionBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: int = Field(default=0,ge=0)
    owner: str = Field(min_length=1,max_length=100)
    grain: str = Field(min_length=1,max_length=500)
    update_policy: str = Field(min_length=1,max_length=500)
    quality_note: str = Field(min_length=1,max_length=3000)
    certification: str = 'validation_only'
    columns: list[ColumnDefinitionBody] = Field(min_length=1,max_length=1000)


@app.put("/api/workspaces/{wid}/assets/{aid}/metadata")
def maintain_metadata(wid:int,aid:int,body:CatalogDefinitionBody,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid,True);asset=owned(db,Asset,wid,aid)
    chinese=lambda value:bool(re.search(r'[\u4e00-\u9fff]',value))
    physical={c['name'] for c in asset.columns};names=[c.name for c in body.columns]
    if len(names)!=len(set(names)) or set(names)!=physical:
        raise HTTPException(400,'维护字段必须与当前采集的真实字段逐一对应，不能遗漏、重复或新增物理字段')
    if not chinese(body.grain) or not chinese(body.update_policy) or not chinese(body.quality_note):
        raise HTTPException(400,'粒度、更新策略和质量说明需要中文定义')
    if body.certification not in {'validation_only','unreviewed'}:raise HTTPException(400,'当前支持模拟验证或待评审状态')
    for column in body.columns:
        if not chinese(column.chinese_name) or not chinese(column.definition):raise HTTPException(400,f'{column.name} 需要中文名和业务释义')
        if column.semantic_role not in {'identifier','attribute','measure','event_time','status'}:raise HTTPException(400,'字段语义角色无效')
        if column.classification not in {'public','internal','sensitive'}:raise HTTPException(400,'字段分级无效')
        if any(len(rule)>500 for rule in column.quality_rules+column.enum_values):raise HTTPException(400,'质量规则或枚举值过长')
    row=db.scalar(select(CatalogMetadata).where(CatalogMetadata.asset_id==aid).with_for_update())
    if body.revision!=(row.revision if row else 0):raise HTTPException(409,'元数据已被其他人修改，请刷新后再保存')
    spec=body.model_dump(exclude={'revision'})
    if row:
        row.revision+=1;row.spec=spec;row.actor=user.username;row.updated_at=now()
    else:
        row=CatalogMetadata(workspace_id=wid,asset_id=aid,spec=spec,actor=user.username);db.add(row)
    db.flush()
    db.add(CatalogMetadataRevision(metadata_id=row.id,revision=row.revision,spec=spec,actor=user.username))
    record_audit(db,user,wid,'metadata.maintain',f'{asset.namespace}.{asset.table_name} v{row.revision}; {len(names)} fields');db.commit()
    return row_dict(row)


@app.get("/api/workspaces/{wid}/assets/{aid}/metadata/revisions")
def metadata_revisions(wid:int,aid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid);owned(db,Asset,wid,aid)
    row=db.scalar(select(CatalogMetadata).where(CatalogMetadata.asset_id==aid))
    if not row:return []
    return [row_dict(r) for r in db.scalars(select(CatalogMetadataRevision).where(CatalogMetadataRevision.metadata_id==row.id).order_by(CatalogMetadataRevision.revision.desc()))]


@app.patch("/api/workspaces/{wid}/assets/{aid}")
def annotate_asset(wid:int,aid:int,body:dict,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid,True);asset=owned(db,Asset,wid,aid)
    if body.get("domain_id") and owned(db,Resource,wid,body["domain_id"]).kind!="domain":raise HTTPException(400,"业务域无效")
    for key in ["name","description","domain_id"]:
        if key in body:setattr(asset,key,body[key])
    record_audit(db,user,wid,"asset.annotate",asset.table_name);db.commit()
    return row_dict(asset)


@app.get("/api/workspaces/{wid}/assets/{aid}/preview")
def preview(wid:int,aid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid);asset=owned(db,Asset,wid,aid);source=owned(db,Datasource,wid,asset.datasource_id)
    from .pipeline_catalog import guard_asset,batch_gate
    guard_asset(db,wid,user,asset,raw=True);batch_gate(db,wid,asset)
    if asset.state!="active":raise HTTPException(409,"物理表已失效或需重新采集")
    from .pipeline_query import execute_read
    keys=sorted([c for c in asset.columns if c.get("primary_key")],key=lambda c:c.get("primary_key_position") or 0)
    ordering=sql.SQL(" ORDER BY ")+sql.SQL(",").join(sql.Identifier(c["name"]) for c in keys) if keys else sql.SQL("")
    query=sql.SQL("SELECT * FROM {}.{}{}").format(sql.Identifier(asset.namespace),sql.Identifier(asset.table_name),ordering).as_string()
    result=execute_read(db,wid,user,source.id,query,limit=100);db.commit()
    return {**result,"source":"live_database","order_by":[c["name"] for c in keys]}


@app.post("/api/workspaces/{wid}/queries")
def execute_query(wid:int,body:QueryBody,user=Depends(identity),db=Depends(db_session)):
    from .pipeline_query import execute_read
    try:
        result=execute_read(db,wid,user,body.datasource_id,body.sql)
        record_audit(db,user,wid,"sql.execute",f"query:{result['id']}");db.commit()
        return result
    except HTTPException:
        db.commit();raise



@app.get("/api/workspaces/{wid}/queries")
def query_history(wid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid)
    return [row_dict(r) for r in db.scalars(select(QueryRun).where(QueryRun.workspace_id==wid).order_by(QueryRun.id.desc()).limit(100))]


@app.get("/api/workspaces/{wid}/lineage")
def lineage(wid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid)
    return [row_dict(e) for e in db.scalars(select(Lineage).where(Lineage.workspace_id==wid).order_by(Lineage.id.desc()).limit(300))]


@app.get("/api/workspaces/{wid}/audit")
def audit_log(wid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid)
    return [row_dict(a) for a in db.scalars(select(Audit).where(Audit.workspace_id==wid).order_by(Audit.id.desc()).limit(100))]


class TransformBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_key: str = Field(min_length=1,max_length=150)
    datasource_id: int
    target_asset_id: int
    kind: str = "warehouse_transform"
    select_sql: str = Field(min_length=1,max_length=40000)
    manifest: dict = Field(default_factory=dict)


@app.post("/api/workspaces/{wid}/transforms")
def verify_transform(wid:int,body:TransformBody,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid,True)
    if body.kind not in {"warehouse_transform","synthetic_generation"}:
        raise HTTPException(400,"不支持的加工类型")
    source=owned(db,Datasource,wid,body.datasource_id)
    target=owned(db,Asset,wid,body.target_asset_id)
    if target.datasource_id!=source.id or target.state!='active':raise HTTPException(400,"目标资产或数据源无效")
    if (body.kind=='synthetic_generation') != (target.layer=='ODS'):
        raise HTTPException(400,"合成生成声明仅用于 ODS；数仓加工验证用于非 ODS 目标")
    tree=read_query(body.select_sql);refs=table_refs(tree)
    parents=[]
    for ns,name in refs:
        asset=db.scalar(select(Asset).where(Asset.datasource_id==source.id,Asset.namespace==ns,Asset.table_name==name,Asset.state=='active'))
        if ns not in source.schemas or not asset:raise HTTPException(400,f"上游资产未采集：{ns}.{name}")
        if asset.id==target.id:raise HTTPException(400,"加工不能引用自身")
        if target.layer=='DWD' and asset.layer in {'DWS','ADS'}:raise HTTPException(400,"明细层不能反向依赖汇总或应用层")
        parents.append(asset)
    graph={}
    for edge in db.scalars(select(TransformEdge).where(TransformEdge.workspace_id==wid)):
        graph.setdefault(edge.source_asset_id,set()).add(edge.target_asset_id)
    reachable=set();pending=[target.id]
    while pending:
        node=pending.pop()
        if node not in reachable:reachable.add(node);pending.extend(graph.get(node,[]))
    if any(p.id in reachable for p in parents):raise HTTPException(400,"加工关系将形成循环")
    digest=hashlib.sha256(body.select_sql.encode()).hexdigest()
    old=db.scalar(select(TransformRun).where(TransformRun.workspace_id==wid,TransformRun.run_key==body.run_key))
    if old:
        if old.target_asset_id!=target.id or old.evidence['sql_sha256']!=digest or old.kind!=body.kind:raise HTTPException(409,"运行键已用于其他加工")
        return row_dict(old)
    current={(a['namespace'],a['table_name']):a for a in inspect_catalog(source)}
    for asset in parents+[target]:
        if current.get((asset.namespace,asset.table_name),{}).get('fingerprint')!=asset.fingerprint:raise HTTPException(409,"结构已变化，请重新采集")
    evidence={"sql_sha256":digest,"verified_at":now().isoformat(),"scope":"table_level",
        "input_snapshots":[{"asset_id":a.id,"table":f'{a.namespace}.{a.table_name}',"fingerprint":a.fingerprint} for a in parents],
        "target_fingerprint":target.fingerprint,"migration_manifest":body.manifest}
    status='declared_only'
    if body.kind=='warehouse_transform':
        keys=[c['name'] for c in sorted(target.columns,key=lambda c:c.get('primary_key_position') or 0) if c.get('primary_key')]
        if not keys:raise HTTPException(400,"加工验证需要已采集的物理主键")
        try:
            with connect(source) as conn:
                conn.execute('SET TRANSACTION READ ONLY')
                conn.execute('SET LOCAL statement_timeout=25000')
                result=conn.execute('SELECT * FROM ('+tree.sql(dialect='postgres')+') expected LIMIT 0')
                if [d.name for d in result.description]!=[c['name'] for c in target.columns]:raise HTTPException(400,"输出字段顺序或名称与目标表不同")
                keyjoin=sql.SQL(' AND ').join(sql.SQL('a.{}=e.{}').format(sql.Identifier(k),sql.Identifier(k)) for k in keys)
                comparisons=[]
                for c in target.columns:
                    col=sql.Identifier(c['name'])
                    if c['type']=='double precision':
                        comparisons.append(sql.SQL('CASE WHEN a.{0} IS NULL OR e.{0} IS NULL THEN a.{0} IS DISTINCT FROM e.{0} ELSE abs(a.{0}-e.{0})>1e-7 END').format(col))
                    else:comparisons.append(sql.SQL('a.{0} IS DISTINCT FROM e.{0}').format(col))
                statement=sql.SQL('WITH e AS MATERIALIZED ({query}), bad_keys AS (SELECT {keys} FROM e GROUP BY {keys} HAVING count(*)>1) SELECT (SELECT count(*) FROM e), (SELECT count(*) FROM {target}), (SELECT count(*) FROM bad_keys), count(*) FILTER (WHERE {different}) FROM {target} a FULL JOIN e ON {join}').format(
                    query=sql.SQL(tree.sql(dialect='postgres')),keys=sql.SQL(',').join(sql.Identifier(k) for k in keys),
                    target=sql.Identifier(target.namespace,target.table_name),different=sql.SQL(' OR ').join(comparisons),join=keyjoin)
                expected_count,actual_count,duplicate_keys,mismatches=conn.execute(statement).fetchone()
            if expected_count!=actual_count or duplicate_keys or mismatches:raise HTTPException(409,f"重算不一致：expected={expected_count}, actual={actual_count}, duplicate_keys={duplicate_keys}, mismatches={mismatches}")
            evidence.update(origin='server_recomputed_select',expected_count=expected_count,target_count=actual_count,
                duplicate_keys=duplicate_keys,mismatched_rows=mismatches,compared_columns=len(target.columns),double_absolute_tolerance=1e-7)
            status='recomputed_match'
        except psycopg.Error as exc:raise HTTPException(400,'加工重算验证失败：'+(exc.diag.message_primary or '数据库错误'))
    else:
        evidence.update(origin='static_generator_sql',historical_execution_proven=False,
            note='仅证明当前生成器 SQL 的静态依赖；ODS 从原快照导入，未重新执行该生成器。')
    run=TransformRun(workspace_id=wid,datasource_id=source.id,target_asset_id=target.id,run_key=body.run_key,
        kind=body.kind,status=status,select_sql=body.select_sql,evidence=evidence,actor=user.username)
    db.add(run);db.flush()
    for parent in parents:db.add(TransformEdge(workspace_id=wid,run_id=run.id,source_asset_id=parent.id,target_asset_id=target.id))
    record_audit(db,user,wid,'transform.'+status,f'{target.namespace}.{target.table_name} {len(parents)} upstream');db.commit()
    return row_dict(run)


@app.get("/api/workspaces/{wid}/transforms")
def transforms(wid:int,user=Depends(identity),db=Depends(db_session)):
    workspace_access(db,user,wid)
    assets={a.id:a for a in db.scalars(select(Asset).where(Asset.workspace_id==wid))}
    result=[]
    for run in db.scalars(select(TransformRun).where(TransformRun.workspace_id==wid).order_by(TransformRun.id.desc())):
        row=row_dict(run);row['target']=f'{assets[run.target_asset_id].namespace}.{assets[run.target_asset_id].table_name}'
        row['target_name']=assets[run.target_asset_id].name;row['inputs']=[]
        for edge in db.scalars(select(TransformEdge).where(TransformEdge.run_id==run.id)):
            a=assets[edge.source_asset_id];row['inputs'].append({'asset_id':a.id,'source':f'{a.namespace}.{a.table_name}','name':a.name})
        result.append(row)
    return result


@app.get("/api/users")
def users(user=Depends(identity),db=Depends(db_session)):
    if user.role!="admin":raise HTTPException(403,"需要管理员权限")
    return [row_dict(u) for u in db.scalars(select(User).order_by(User.id))]


@app.post("/api/users")
def add_user(body:dict,user=Depends(identity),db=Depends(db_session)):
    if user.role!="admin":raise HTTPException(403,"需要管理员权限")
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,39}",str(body.get("username",""))) or len(str(body.get("password","")))<12 or body.get("role") not in {"admin","developer","viewer"}:raise HTTPException(400,"账号为 3—40 位小写英文，密码至少 12 位；请选择有效角色")
    row=User(username=body["username"],display_name=str(body.get("display_name",body["username"]))[:100],password_hash=hash_password(body["password"]),role=body["role"])
    db.add(row);db.flush()
    for wid in set(body.get("workspace_ids",[])):
        if not db.get(Workspace,wid):raise HTTPException(400,"工作空间不存在")
        db.add(Membership(user_id=row.id,workspace_id=wid))
    record_audit(db,user,None,"user.create",row.username);db.commit()
    return row_dict(row)
