"""Deterministic compiler and live source checks for local synthetic validation.
No model SQL is accepted. Unsupported semantics fail closed.
"""
import copy
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
import sqlglot
from sqlglot import exp
from sqlalchemy import select
from fastapi import HTTPException
from .db import Asset, Datasource, now
from .warehouse import connect, inspect_catalog, read_query, table_refs


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False, default=str).encode()).hexdigest()


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex


class PlanningBlocked(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def normalize_metadata_candidate(plan, discovery, intent, bundle, answers=()):
    """Pin a model proposal to visible metadata; never accept model-authored SQL."""
    if plan is None:
        return None
    if not isinstance(plan,dict) or not isinstance(discovery,dict):
        raise PlanningBlocked("METADATA_CANDIDATE_INVALID","目录候选结构无效，请补充统计口径")
    supplied_hash=discovery.get("content_hash")
    discovery_content={k:v for k,v in discovery.items() if k!="content_hash"}
    if not supplied_hash or canonical_hash(discovery_content)!=supplied_hash:
        raise PlanningBlocked("METADATA_CANDIDATE_STALE","目录候选已变化，请重新检索")
    if (discovery.get("release_id")!=bundle.get("release_id") or discovery.get("release_hash")!=bundle.get("release_hash")
            or discovery.get("index_snapshot_id")!=bundle.get("index_snapshot_id")
            or discovery.get("index_snapshot_hash")!=bundle.get("index_snapshot_hash")):
        raise PlanningBlocked("METADATA_CANDIDATE_STALE","目录候选不属于本次固定发布版本")
    if intent.get("negative_terms"):
        raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","当前目录候选无法证明排除条件，请补充明确字段和规则")
    from .agent_time import infer_periods
    periods=infer_periods(intent)
    start,end=(periods[0]['start'],periods[0]['end_exclusive']) if periods else (intent.get('start'),intent.get('end_exclusive'))
    try:
        valid_range=all(isinstance(v,str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}",v) for v in (start,end)) and date.fromisoformat(start)<date.fromisoformat(end)
    except (TypeError,ValueError):
        valid_range=False
    if not valid_range:
        raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","请先明确有效的左闭右开统计日期范围")
    table=next((t for t in discovery.get("tables",[]) if
        t.get("catalog_snapshot_id")==plan.get("catalog_snapshot_id") and
        t.get("datasource_id")==str(plan.get("datasource_id")) and
        t.get("dataset_id")==plan.get("dataset_id")),None)
    if not table:
        raise PlanningBlocked("METADATA_CANDIDATE_INVALID","候选数据表不在本次可访问目录中")
    fields={f["field_id"]:f for f in table.get("fields",[]) if isinstance(f,dict) and isinstance(f.get("field_id"),str)}
    operation=plan.get("operation")
    if operation not in {"COUNT_ROWS","COUNT_DISTINCT","SUM","AVG","RATIO_SUM"}:
        raise PlanningBlocked("METADATA_CANDIDATE_UNSUPPORTED","目录候选仅支持记录数、去重计数、求和或平均值")
    measure_id=plan.get("measure_field_id")
    measure=fields.get(measure_id) if measure_id else None
    if operation=="COUNT_ROWS" and measure_id is not None:
        raise PlanningBlocked("METADATA_CANDIDATE_INVALID","记录数候选不能额外绑定度量字段")
    if operation!="COUNT_ROWS" and not measure:
        raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","请明确计算所用字段")
    if operation=="COUNT_DISTINCT" and measure.get("semantic_role")!="IDENTIFIER" and not measure.get("is_primary"):
        raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","去重计数需要目录标记为标识符或主键的字段")
    denominator=fields.get(plan.get('denominator_field_id'))
    if operation=='RATIO_SUM':
        if not denominator or not measure or denominator['field_id']==measure['field_id']:
            raise PlanningBlocked('METADATA_CANDIDATE_INCOMPLETE','比值需要当前表中的两个明确数值字段作为分子和分母')
        for field in (measure,denominator):
            if not re.search(r'(smallint|integer|bigint|numeric|decimal|real|double|float)',field.get('data_type','').lower()):
                raise PlanningBlocked('METADATA_CANDIDATE_INVALID','比值的分子和分母必须是数值字段')
        answers_text=' '.join(str(a.get('answer') or '') for a in answers)
        if not re.search(r'÷|/|除以|比值|比例',answers_text):
            raise PlanningBlocked('METADATA_CANDIDATE_INCOMPLETE','请先明确确认分子除以分母的计算口径')
        units=[field.get('unit') for field in (measure,denominator)]
        known=all(isinstance(u,dict) and u.get('kind')!='UNKNOWN' and u.get('authority') in {'CONFIRMED','SYNTHETIC_ONLY'} for u in units)
        if known and any(units[0].get(key)!=units[1].get(key) for key in ('kind','code','currency','scale')):
            raise PlanningBlocked('METADATA_CANDIDATE_INCOMPLETE','分子和分母单位不一致，不能直接计算比值')
        if not known and not re.search(r'同一单位|单位一致|相同单位|均为|同币种',answers_text):
            raise PlanningBlocked('METADATA_CANDIDATE_INCOMPLETE','请确认分子和分母采用相同单位或币种')
        unit={'kind':'RATIO','code':'ratio','currency':None,'scale':None,'authority':'SYNTHETIC_ONLY'}
    elif operation in {"SUM","AVG"}:
        if not re.search(r"(smallint|integer|bigint|numeric|decimal|real|double|float)",str(measure.get("data_type","")).lower()):
            raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","求和或平均值需要数值类型字段")
        unit=measure.get("unit")
        unit_known=isinstance(unit,dict) and unit.get("kind") not in {None,"UNKNOWN"} and unit.get("authority") in {"CONFIRMED","SYNTHETIC_ONLY"}
        if not unit_known:
            answer_text=" ".join(str(a.get("answer") or "") for a in answers if isinstance(a,dict))
            unit_label=str(plan.get("unit_label") or "").strip()
            if not unit_label or unit_label.casefold() not in answer_text.casefold():
                raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","该度量字段没有权威单位；请明确本次技术验证采用的单位")
            unit={"kind":"UNKNOWN","code":unit_label,"currency":None,"scale":None,"authority":"UNKNOWN"}
        elif unit.get("kind")=="CURRENCY" and not unit.get("currency"):
            raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","金额字段缺少币种定义，请先补充并确认")
    else:
        unit={"kind":"COUNT","code":"count","currency":None,"scale":None,"authority":"SYNTHETIC_ONLY"}
    time_id=plan.get("time_field_id")
    time_field=fields.get(time_id) if time_id else None
    if not time_field or not re.search(r"(date|time)",str(time_field.get("data_type","")).lower()):
        raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","请从目录中确认可用于统计时间的日期/时间字段")
    role=str(time_field.get("semantic_role") or "UNKNOWN")
    if role not in {"EVENT_TIME","BUSINESS_DATE","SNAPSHOT_DATE","PROCESSING_TIME"}:
        explicit_time=' '.join(str(a.get('answer') or '') for a in answers)
        if time_field['name'] not in explicit_time or not re.search(r'时间|日期|time|date',explicit_time,re.I):
            raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE",f"字段 {time_field['name']}（{time_field.get('name_zh') or '未维护中文名'}）尚未维护时间角色，请明确确认本次按此字段表示哪种业务时间")
    dimension_ids=plan.get("dimension_field_ids") or []
    requested_dimensions=intent.get("dimension_terms") or []
    if len(dimension_ids)>1 or len(dimension_ids)!=len(set(requested_dimensions)):
        raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","候选分组字段与用户请求的分组数量不一致")
    dimension_fields=[]
    for field_id in dimension_ids:
        field=fields.get(field_id)
        if not field:
            raise PlanningBlocked("METADATA_CANDIDATE_INVALID","候选分组字段不在所选目录表中")
        dimension_fields.append(field)
    plan_filters=plan.get("filters") or []
    requested_filters=intent.get("filters") or []
    if len(plan_filters)!=len(requested_filters):
        raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","目录候选没有完整保留用户筛选条件")
    filters=[]
    remaining=list(requested_filters)
    for item in plan_filters:
        field=fields.get(item.get("field_id")) if isinstance(item,dict) else None
        if not field or item.get("operator")!="=" or not isinstance(item.get("value"),str):
            raise PlanningBlocked("METADATA_CANDIDATE_INVALID","目录候选筛选条件不符合受控字段契约")
        labels={field['field_id'],field['name'],field.get('name_zh'),*field.get('aliases',[])}
        match=next((f for f in remaining if str(f.get("value"))==item["value"] and f.get("operator") in {"=","eq","equals"}
            and (f.get('field') in labels or (re.search('类目|类别|品类',str(f.get('field'))) and re.search('类目|类别|品类|category',str(field))))),None)
        if not match:
            raise PlanningBlocked("METADATA_CANDIDATE_INVALID","目录候选包含原问题未提出的筛选值")
        remaining.remove(match)
        filters.append({"field":field,"operator":"=","value":item["value"]})
    name=str(plan.get("name") or "").strip()
    definition=str(plan.get("definition") or "").strip()
    if not name or len(name)>120 or not definition or len(definition)>500:
        raise PlanningBlocked("METADATA_CANDIDATE_INCOMPLETE","候选口径需要明确的名称和业务解释")
    snapshot_ref={"catalog_snapshot_id":table["catalog_snapshot_id"],"datasource_id":table["datasource_id"],"dataset_id":table["dataset_id"]}
    candidate={"schema_version":"metadata-query-candidate/1.0.0","candidate_id":new_id("candidate"),
        "scope":"local_validation","name":name,"definition":definition,"operation":operation,
        "metric_field":measure,"denominator_field":denominator if operation=='RATIO_SUM' else None,"time_field":time_field,"dimension_fields":dimension_fields,
        "filters":filters,"dataset_ref":snapshot_ref,"dataset_label":table["table_label"],
        "datasource_name":table.get("datasource_name",""),"logical_locator":table["logical_locator"],
        "namespace":table["namespace"],"physical_name":table["physical_name"],
        "physical_source":table["namespace"]+"."+table["physical_name"],
        "schema_fingerprint":table["schema_fingerprint"],"catalog_snapshot_hash":table["catalog_snapshot_hash"],
        "release_id":discovery["release_id"],"release_hash":discovery["release_hash"],
        "index_snapshot_id":discovery["index_snapshot_id"],"index_snapshot_hash":discovery["index_snapshot_hash"],
        "metadata_context_hash":supplied_hash,"intent_frame_hash":canonical_hash(intent),
        "time_range":{"start":start,"end_exclusive":end},"time_periods":periods,"unit":unit,
        "time_role_source":"CATALOG" if role in {"EVENT_TIME","BUSINESS_DATE","SNAPSHOT_DATE","PROCESSING_TIME"} else "USER_CLARIFICATION",
        "unit_source":"CATALOG" if unit.get("authority") in {"CONFIRMED","SYNTHETIC_ONLY"} else "USER_CLARIFICATION",
        "null_policy":"EXCLUDE_AND_WARN" if operation in {"SUM","AVG","RATIO_SUM"} else "NOT_APPLICABLE",
        "grain_note":"按当前表的物理记录粒度；目录未提供业务粒度证明" if not table.get("key_constraints") else "按固定目录声明键核对，业务含义仍待Owner审核",
        "business_approved":False,"metadata_only_source":True}
    candidate["content_hash"]=canonical_hash(candidate)
    return candidate


def compile_metadata_preview(candidate, intent, bundle, run_id, requirement_id, confirmation_ref):
    """Compile a one-table, run-local proposal. The model never supplies SQL."""
    if candidate.get("scope")!="local_validation" or candidate.get("business_approved") is not False:
        raise PlanningBlocked("METADATA_CANDIDATE_INVALID","仅允许本地验证的临时候选口径")
    clean={k:v for k,v in candidate.items() if k!="content_hash"}
    if canonical_hash(clean)!=candidate.get("content_hash") or candidate.get("intent_frame_hash")!=canonical_hash(intent):
        raise PlanningBlocked("METADATA_CANDIDATE_STALE","候选口径或需求已变化，请重新确认")
    if candidate.get("release_hash")!=bundle.get("release_hash") or candidate.get("index_snapshot_hash")!=bundle.get("index_snapshot_hash"):
        raise PlanningBlocked("METADATA_CANDIDATE_STALE","候选目录不属于当前固定发布")
    physical=candidate.get("physical_source","")
    if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*",physical):
        raise PlanningBlocked("PHYSICAL_MAPPING_UNKNOWN","候选来源不是已登记的物理表")
    namespace,table_name=physical.split(".")
    def col(field):
        name=field.get("name","")
        if not re.fullmatch(r"[a-z][a-z0-9_]*",name):
            raise PlanningBlocked("PHYSICAL_MAPPING_UNKNOWN","目录字段名无效")
        return '"b"."'+name+'"'
    dims=candidate.get("dimension_fields",[])
    if len(dims)>1:
        raise PlanningBlocked("DIMENSION_COMBINATION_UNSUPPORTED","当前本地候选路径一次只支持一个目录分组字段")
    select_items=[]
    if dims:
        select_items.append(col(dims[0])+" AS dimension_value")
    operation=candidate["operation"]
    measure=candidate.get("metric_field")
    if operation=="COUNT_ROWS":aggregate="COUNT(*)"
    elif operation=="COUNT_DISTINCT" and measure:aggregate="COUNT(DISTINCT "+col(measure)+")"
    elif operation=="SUM" and measure:aggregate="SUM("+col(measure)+")"
    elif operation=="AVG" and measure:aggregate="AVG("+col(measure)+")"
    elif operation=="RATIO_SUM" and measure and candidate.get('denominator_field'):
        aggregate='CAST(SUM('+col(measure)+') AS DOUBLE PRECISION) / NULLIF(SUM('+col(candidate['denominator_field'])+'), 0)'
    else:raise PlanningBlocked("METADATA_CANDIDATE_INVALID","候选计算方式与度量字段不一致")
    select_items.append(aggregate+" AS metric_value")
    time_field=candidate.get("time_field") or {}
    time_column=col(time_field)
    where=[time_column+" >= :start_date",time_column+" < :end_date"]
    params={"start_date":candidate["time_range"]["start"],"end_date":candidate["time_range"]["end_exclusive"]}
    for index,item in enumerate(candidate.get("filters",[])):
        where.append(col(item["field"])+" = :filter_"+str(index))
        params["filter_"+str(index)]=item["value"]
    observation_where=list(where)
    if operation in {"SUM","AVG","RATIO_SUM"}:
        where.append(col(measure)+" IS NOT NULL")
        if operation=='RATIO_SUM':
            where.append(col(candidate['denominator_field'])+' IS NOT NULL')
    sql="SELECT "+", ".join(select_items)+' FROM "'+namespace+'"."'+table_name+'" AS b WHERE '+" AND ".join(where)
    if dims:
        sql+=" GROUP BY "+col(dims[0])+" ORDER BY dimension_value NULLS LAST"
    periods=candidate.get('time_periods') or [candidate['time_range']]
    period_observations=[]
    if len(periods)>1:
        parts=[]; params={k:v for k,v in params.items() if k not in {'start_date','end_date'}}
        params.update(start_date=min(p['start'] for p in periods),end_date=max(p['end_exclusive'] for p in periods))
        for index,period in enumerate(periods):
            params.update({f'period_{index}_start':period['start'],f'period_{index}_end':period['end_exclusive'],f'period_{index}_label':period.get('label') or f'时间段{index+1}'})
            predicates=[part.replace(':start_date',f':period_{index}_start').replace(':end_date',f':period_{index}_end') for part in where]
            # Empty aggregate rows must not turn into zero-valued periods.
            part='SELECT '+str(index)+' AS period_order, :period_'+str(index)+'_label AS period_label, '+', '.join(select_items)+' FROM "'+namespace+'"."'+table_name+'" AS b WHERE '+' AND '.join(predicates)
            if dims:part+=' GROUP BY '+col(dims[0])
            part+=' HAVING COUNT(*) > 0'
            parts.append(part)
            observed_where=[part.replace(':start_date',f':period_{index}_start').replace(':end_date',f':period_{index}_end') for part in observation_where]
            observed_measure=col(measure) if measure else '*'
            if operation=='RATIO_SUM':observed_measure='CASE WHEN '+col(candidate['denominator_field'])+' IS NOT NULL THEN '+col(measure)+' END'
            period_observations.append('SELECT COUNT(*) AS source_rows, COUNT('+observed_measure+') AS valid_measure_rows FROM "'+namespace+'"."'+table_name+'" AS b WHERE '+' AND '.join(observed_where))
        output=['period_label']+(['dimension_value'] if dims else [])+['metric_value']
        measures=(intent.get('analysis') or {}).get('measures',[])
        partition='PARTITION BY dimension_value' if dims else ''
        base='MAX(CASE WHEN period_order = 0 THEN metric_value END) OVER ('+partition+')'
        if 'ABSOLUTE_CHANGE' in measures:output.append('metric_value - '+base+' AS absolute_change')
        if 'GROWTH_RATE' in measures:output.append('(metric_value - '+base+') / NULLIF(CAST('+base+' AS DOUBLE PRECISION), 0) AS growth_rate')
        sql='WITH period_values AS ('+' UNION ALL '.join(parts)+') SELECT '+', '.join(output)+' FROM period_values ORDER BY period_order'+(', dimension_value NULLS LAST' if dims else '')
    tree=sqlglot.parse_one(sql,read="postgres")
    ast_template=tree.sql(dialect="postgres")
    sql_text=re.sub(r":([a-z_][a-z0-9_]*)",r"%(\1)s",ast_template)
    read_query(ast_template)
    dimensions=[{"name":"dimension_value","label":dims[0].get("name_zh") or dims[0]["name"],
        "field_ref":{**candidate["dataset_ref"],"field_id":dims[0]["field_id"]}}] if dims else []
    result_kind="COUNT" if operation in {"COUNT_ROWS","COUNT_DISTINCT"} else "NUMBER"
    frame={"frame_id":new_id("frame"),"revision":1,"run_id":run_id,"requirement_id":requirement_id,
        "intent_frame_hash":canonical_hash(intent),"bundle_id":bundle["bundle_id"],"bundle_content_hash":canonical_hash(bundle),
        "metric_confirmation_ref":confirmation_ref,"metric_ref":None,"binding_ref":None,"dimension_refs":[],
        "metadata_candidate_ref":{"candidate_id":candidate["candidate_id"],"content_hash":candidate["content_hash"]},
        "metadata_candidate":copy.deepcopy(candidate),
        "catalog_snapshot_ref":candidate["dataset_ref"],"catalog_snapshot_hash":candidate["catalog_snapshot_hash"],
        "physical_refs":[{"kind":"DATASET","target_ref":candidate["dataset_ref"]}]+
            [{"kind":"FIELD","target_ref":{**candidate["dataset_ref"],"field_id":field["field_id"]}}
                for field in ([candidate.get("metric_field"),candidate.get('denominator_field'),time_field]+dims+[item["field"] for item in candidate.get("filters",[])]) if field],
        "release_id":bundle["release_id"],"release_hash":bundle["release_hash"],
        "index_snapshot_id":bundle["index_snapshot_id"],"index_snapshot_hash":bundle["index_snapshot_hash"],
        "resolved_parameters":params,"datasource_id":int(candidate["dataset_ref"]["datasource_id"]),
        "scope":"local_validation","metric_id":"metadata:"+candidate["candidate_id"],"unit":candidate["unit"],
        "time_expression":"b."+time_field["name"],"time_physical_source":candidate["physical_source"],
        "time_field_name":time_field["name"],"execution_authority":"MetadataCandidate",
        "metadata_candidate_hash":candidate["content_hash"],"metadata_context_hash":candidate["metadata_context_hash"],
        "source_object":candidate["logical_locator"],"physical_source":candidate["physical_source"],
        "dimension_ids":[],"dimension_field_refs":dimensions,"result_kind":result_kind,
        "result_shape":["dimension_value","metric_value"] if dims else ["metric_value"],
        "result_column_metadata":dimensions+[{"name":"metric_value","label":candidate["name"],
            "unit":candidate["unit"],"candidate_only":True}],
        "metric_null_policy":"REJECT_ROW_AND_WARN" if operation in {"SUM","AVG","RATIO_SUM"} else "IGNORE",
        "empty_result_policy":"NO_DATA","observation_sql":None,
        "budget":{"statement_timeout_ms":10000,"max_rows":1000,"max_plan_rows":5000000,"max_total_cost":1000000},
        "candidate_only":True,"business_approved":False}
    measure_observation=col(measure) if measure else "*"
    if operation=='RATIO_SUM':
        measure_observation='CASE WHEN '+col(candidate['denominator_field'])+' IS NOT NULL THEN '+col(measure)+' END'
    observation_sql="SELECT COUNT(*) AS source_rows, COUNT("+measure_observation+") AS valid_measure_rows FROM \""+namespace+"\".\""+table_name+"\" AS b WHERE "+" AND ".join(observation_where)
    frame["observation_sql"]=re.sub(r":([a-z_][a-z0-9_]*)",r"%(\1)s",observation_sql)
    if len(periods)>1:
        frame['time_periods']=periods
        frame['observation_queries']=[re.sub(r':([a-z_][a-z0-9_]*)',r'%(\1)s',q) for q in period_observations]
        additions=[]
        if 'ABSOLUTE_CHANGE' in measures:additions.append({'name':'absolute_change','label':'相对基期变化量'})
        if 'GROWTH_RATE' in measures:additions.append({'name':'growth_rate','label':'相对基期变化率'})
        frame['result_shape']=['period_label']+frame['result_shape']+[c['name'] for c in additions]
        frame['result_column_metadata']=[{'name':'period_label','label':'时间段'}]+frame['result_column_metadata']+additions
    frame["content_hash"]=canonical_hash(frame)
    preview={"sql":sql_text,"sql_ast_template":ast_template,"sql_hash":canonical_hash({"sql":sql_text}),
        "parameters":params,"parameters_hash":canonical_hash(params),"datasource_id":frame["datasource_id"],
        "execution_mode":"local_validation","executed":False,"query_frame_hash":frame["content_hash"],
        "sources":[physical],"compiler_version":"metadata-candidate-sql/1.0.0"}
    return frame,preview


SAFE_METRICS = {"order_count", "session_count", "shipment_count", "promotion_orders", "review_score"}
DIMENSIONS = {"customer_state": "买家所在地区（省/州）", "product_category": "商品类别"}
TIME_EVENTS = {"order_count": {"下单", "下单时间", "订单创建", "订单创建时间", "订单下单时间", "purchased_at", "order_placed", "purchase", "order_purchase"},
    "session_count": {"会话开始", "会话开始时间", "访问时间", "started_at", "session_started"},
    "shipment_count": {"发货", "发货时间", "shipped_at", "shipment_shipped"},
    "promotion_orders": {"下单", "下单时间", "订单创建", "purchased_at", "order_placed"},
    "review_score": {"评价", "评价时间", "评价创建时间", "reviewed_at", "review_created"}}


def _mapping(contract, key):
    value = contract.get("physical_mapping", {}).get(key)
    if isinstance(value, dict):
        value = value.get("physical_object") or value.get("physical_name") or value.get("physical_locator")
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", value):
        raise PlanningBlocked("PHYSICAL_MAPPING_UNKNOWN", "目录中缺少明确的逻辑与物理表映射")
    return value


def compile_preview(contract, intent, bundle, run_id, requirement_id, confirmation_ref):
    from .agent_clarification import check_analysis_support
    check_analysis_support(intent)
    if contract.get("scope") != "local_validation":
        raise PlanningBlocked("PRODUCTION_NOT_APPROVED", "生产执行尚未取得业务审批与权限验收")
    metric_id = contract.get("metric_id")
    if contract.get("unsupported_filters"):
        raise PlanningBlocked("BINDING_FILTER_UNSUPPORTED", "当前映射含未实现的默认或适用范围筛选，不能忽略后执行")
    if metric_id == "active_customers" or (contract.get("result_kind") not in {"COUNT", "SCORE"} if "result_kind" in contract else metric_id not in SAFE_METRICS):
        reason = "金额币种尚未得到确认" if any(x in metric_id for x in ("value", "amount", "revenue", "payment")) else "该指标的单位、粒度或快照策略仍需治理"
        raise PlanningBlocked("SEMANTIC_GOVERNANCE_REQUIRED", reason)
    if contract.get("temporal_mode") != "event_range":
        raise PlanningBlocked("TEMPORAL_MODE_UNSUPPORTED", "当前执行器尚未验收该快照计算模式")
    typed = contract.get("authority") == "AssetRevision+CatalogSnapshot"
    if typed:
        if contract.get("metric_null_policy") not in {"IGNORE", "REJECT_ROW_AND_WARN"}:
            raise PlanningBlocked("METRIC_NULL_POLICY_UNSUPPORTED", "指标空值策略未知或尚未实现，不能用数据库默认聚合规则替代")
        if contract.get("empty_result_policy") != "NO_DATA":
            raise PlanningBlocked("EMPTY_RESULT_POLICY_UNSUPPORTED", "当前执行器只支持明确返回无数据；补零需要权威的完整性核验，不能仅凭观测时间覆盖推断")
        if not contract.get("measure_expression") or not contract.get("physical_grain_fields"):
            raise PlanningBlocked("GRAIN_OR_MEASURE_MAPPING_REQUIRED", "统计字段与物理粒度键需要完整映射")
        if contract.get("result_kind") == "SCORE" and set(contract.get("metric_grain_expressions", [])) != set(contract.get("physical_grain_expressions", [])):
            raise PlanningBlocked("METRIC_GRAIN_UNSUPPORTED", "平均评分的业务粒度与物理记录粒度不一致，不能直接对明细行求平均")
    policy = contract.get("time_policy")
    if policy:
        if policy.get("interval") != "[start,end)":
            raise PlanningBlocked("TIME_INTERVAL_UNSUPPORTED", "当前日期范围执行器要求明确的左闭右开区间，不能忽略时间边界规则")
        if policy.get("history_policy") not in {"CURRENT", "IMMUTABLE"}:
            raise PlanningBlocked("HISTORY_POLICY_UNSUPPORTED", "历史时点归属策略未知或尚未实现，请明确使用当前源库快照的统计策略")
    time_event = intent.get("requested_time_event") or intent.get("time_event")
    accepted_events = set(contract.get("time_event_aliases", [])) if contract.get("authority") == "AssetRevision+CatalogSnapshot" else TIME_EVENTS.get(metric_id, set())
    if time_event and time_event not in accepted_events:
        raise PlanningBlocked("TIME_EVENT_UNSUPPORTED", "请求的时间事件与当前指标映射不一致，需要澄清或新增映射")
    start, end = intent.get("start"), intent.get("end_exclusive")
    if not all(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in (start, end)):
        raise PlanningBlocked("TIME_PRECISION_UNSUPPORTED", "当前只支持明确日期区间，精确时分秒范围需单独实现，不能截断为整天")
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        raise PlanningBlocked("TIME_REQUIRED", "请明确查询起止日期")
    if start_date >= end_date:
        raise PlanningBlocked("TIME_INVALID", "结束日期必须晚于开始日期")
    if intent.get("negative_terms"):
        raise PlanningBlocked("NEGATIVE_CONSTRAINT_REVIEW", "问题含排除或否定约束，需要先确认其精确口径")
    dims = intent.get("dimension_ids", [])
    if len(dims) > min(1, contract.get("max_dimensions", 1)):
        raise PlanningBlocked("DIMENSION_COMBINATION_UNSUPPORTED", "组合维度尚未完成核验，请逐项查询")
    allowed = contract.get("dimensions", {})
    if any(dim not in allowed for dim in dims):
        raise PlanningBlocked("UNSUPPORTED_DIMENSION", "所选指标的映射尚未实现该维度")
    if any(allowed[dim].get("history_policy", "CURRENT") != "CURRENT" for dim in dims):
        raise PlanningBlocked("DIMENSION_HISTORY_UNSUPPORTED", "当前执行器只支持明确的当前快照维度归属，不能用当前值替代历史值")
    if any(allowed[dim].get("null_member", "ERROR") != "ERROR" for dim in dims):
        raise PlanningBlocked("DIMENSION_NULL_UNSUPPORTED", "维度空成员策略未知或未实现，不能用 NULL 结果冒充其他业务成员")
    source_key = contract["source_object"]
    # The trusted imported contract is parsed into an AST before physical remapping.
    expression = sqlglot.parse_one(contract["expression"], read="postgres")
    for node in expression.find_all(exp.Column):
        if not node.table:
            node.set("table", exp.to_identifier("b"))
    joins = list(contract.get("base_joins", []))
    selections = [expression.sql(dialect="postgres") + " AS metric_value"]
    group = ""
    dimension_expr = None
    if dims:
        implementation = allowed[dims[0]]
        dimension_expr = implementation["expression"]
        joins.extend(implementation.get("joins", []))
        selections.insert(0, dimension_expr + " AS dimension_value")
        group = " GROUP BY " + dimension_expr + " ORDER BY dimension_value NULLS LAST"
    if typed:
        selected_joins = set(contract.get("base_join_ids", []))
        for dim in dims:
            selected_joins.update(contract.get("dimension_join_ids", {}).get(dim, []))
        ordered_ids, join_definitions = contract.get("ordered_join_ids", []), contract.get("join_sql_by_id", {})
        if selected_joins - set(ordered_ids) or any(jid not in join_definitions for jid in selected_joins):
            raise PlanningBlocked("JOIN_DEPENDENCY_UNKNOWN", "关联执行顺序或依赖不完整，不能生成查询")
        joins = [join_definitions[jid] for jid in ordered_ids if jid in selected_joins]
    time_expr = contract["time_expression"]
    # Deliberately fixed parameter placeholders; no user text is interpolated.
    query = "SELECT " + ", ".join(selections) + " FROM " + source_key + " b " + " ".join(dict.fromkeys(joins))
    query += " WHERE " + time_expr + " >= :start_date AND " + time_expr + " < :end_date"
    params = {"start_date": str(start_date), "end_date": str(end_date)}
    required_sources = {p["source"] for p in contract.get("parameter_bindings", []) if p["required"]}
    if required_sources - {"INTENT_TIME_START", "INTENT_TIME_END"}:
        raise PlanningBlocked("BINDING_PARAMETER_UNSUPPORTED", "映射包含尚未支持的必需参数来源，不能忽略后执行")
    for index, filt in enumerate(intent.get("filters", [])):
        field, op, value = filt.get("field"), filt.get("operator"), filt.get("value")
        if field not in {"customer_state", "region", "买家所在地区（省/州）"} or not dimension_expr or dims != ["customer_state"] or op not in {"=", "eq", "equals"}:
            raise PlanningBlocked("FILTER_UNSUPPORTED", "该筛选条件尚未建立已核验字段映射")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{2}", value):
            raise PlanningBlocked("FILTER_VALUE_INVALID", "地区筛选需要明确的两位地区代码")
        query += " AND " + dimension_expr + " = :filter_" + str(index)
        params["filter_" + str(index)] = value
    observation_query = None
    if typed:
        # Observe the same FROM/JOIN/time/filter relation before rejecting NULL
        # measure rows. COUNT alone cannot distinguish no source records from 0.
        relation = query[query.index(" FROM "):]
        observation_query = "SELECT COUNT(*) AS source_rows, COUNT(" + contract["measure_expression"] + ") AS valid_measure_rows" + relation
        if contract["metric_null_policy"] == "REJECT_ROW_AND_WARN":
            query += " AND (" + contract["measure_expression"] + ") IS NOT NULL"
    query += group
    tree = sqlglot.parse_one(query, read="postgres")
    for table in tree.find_all(exp.Table):
        logical = (table.db or "public") + "." + table.name
        physical = _mapping(contract, logical)
        namespace, name = physical.split(".")
        table.set("db", exp.to_identifier(namespace, quoted=True))
        table.set("this", exp.to_identifier(name, quoted=True))
    sql_template = tree.sql(dialect="postgres")
    # SQLGlot renders :name as %(name)s for PostgreSQL; normalize for psycopg.
    sql_text = re.sub(r":([a-z_][a-z0-9_]*)", r"%(\1)s", sql_template)
    observation_sql = None
    if observation_query:
        observed_tree = sqlglot.parse_one(observation_query, read="postgres")
        for table in observed_tree.find_all(exp.Table):
            namespace, name = _mapping(contract, (table.db or "public") + "." + table.name).split(".")
            table.set("db", exp.to_identifier(namespace, quoted=True))
            table.set("this", exp.to_identifier(name, quoted=True))
        observation_sql = re.sub(r":([a-z_][a-z0-9_]*)", r"%(\1)s", observed_tree.sql(dialect="postgres"))
    ast_template = re.sub(r"%\(([a-z_][a-z0-9_]*)\)s", r":\1", sql_text)
    read_query(ast_template)
    frame = {"frame_id": new_id("frame"), "revision": 1, "run_id": run_id,
        "requirement_id": requirement_id, "intent_frame_hash": canonical_hash(intent),
        "bundle_id": bundle["bundle_id"], "bundle_content_hash": canonical_hash(bundle),
        "metric_confirmation_ref": confirmation_ref, "metric_ref": bundle["selected_metric_ref"],
        "binding_ref": bundle["selected_binding_ref"], "dimension_refs": bundle["requested_dimension_refs"],
        "release_id": bundle["release_id"], "release_hash": bundle["release_hash"],
        "index_snapshot_id": bundle["index_snapshot_id"], "index_snapshot_hash": bundle["index_snapshot_hash"],
        "physical_refs": bundle["closure_refs"], "resolved_parameters": params,
        "datasource_id": contract["datasource_id"], "scope": "local_validation",
        "metric_id": metric_id, "unit": contract.get("unit"), "time_expression": time_expr,
        "result_kind": contract.get("result_kind", "SCORE" if metric_id == "review_score" else "COUNT"),
        "time_physical_source": contract.get("time_physical_source"), "time_field_name": contract.get("time_field_name"),
        "execution_authority": contract.get("authority", "legacy_import"),
        "binding_content_hash": contract.get("binding_content_hash"),
        "source_contract_hash": contract.get("source_contract_hash"),
        "source_object": source_key, "physical_source": _mapping(contract, source_key),
        "dimension_ids": dims, "result_shape": ["dimension_value", "metric_value"] if dims else ["metric_value"],
        "observation_sql": observation_sql,
        "metric_null_policy": contract.get("metric_null_policy"), "empty_result_policy": contract.get("empty_result_policy"),
        "physical_grain_fields": contract.get("physical_grain_fields", []),
        "result_column_metadata": ([{"name": "dimension_value", "label": allowed[dims[0]].get("name", DIMENSIONS.get(dims[0], dims[0])), "dimension_ref": allowed[dims[0]].get("dimension_ref")}] if dims else []) +
            [{"name": "metric_value", "label": contract.get("name", metric_id), "metric_ref": bundle["selected_metric_ref"], "unit": contract.get("unit")}],
        "budget": {"statement_timeout_ms": 10000, "max_rows": 1000, "max_plan_rows": 5000000, "max_total_cost": 1000000}}
    content_hash = canonical_hash(frame)
    frame["content_hash"] = content_hash
    preview = {"sql": sql_text, "sql_ast_template": ast_template, "sql_hash": canonical_hash({"sql": sql_text}),
        "parameters": params, "parameters_hash": canonical_hash(params), "datasource_id": contract["datasource_id"],
        "execution_mode": "local_validation", "executed": False, "query_frame_hash": content_hash,
        "sources": [".".join(ref) for ref in table_refs(tree)], "compiler_version": "semantic-sql/1.0.0"}
    return frame, preview


def source_check(db, wid, frame, preview, explain=True):
    if frame.get("execution_authority")=="MetadataCandidate":
        from .semantic import resolve_physical, sha
        from .semantic_models import SemanticSnapshot
        candidate=frame.get("metadata_candidate") or {}
        candidate_clean={k:v for k,v in candidate.items() if k!="content_hash"}
        if canonical_hash(candidate_clean)!=candidate.get("content_hash") or candidate.get("content_hash")!=frame.get("metadata_candidate_hash"):
            raise PlanningBlocked("METADATA_CANDIDATE_STALE","临时候选口径校验不一致，请重新确认")
        if candidate.get("release_hash")!=frame.get("release_hash") or candidate.get("index_snapshot_hash")!=frame.get("index_snapshot_hash"):
            raise PlanningBlocked("METADATA_CANDIDATE_STALE","候选口径与固定发布版本不一致")
        snapshot_ref=candidate.get("dataset_ref") or {}
        snapshot=db.get(SemanticSnapshot,snapshot_ref.get("catalog_snapshot_id"))
        if not snapshot or snapshot.workspace_id!=wid or snapshot.content_hash!=candidate.get("catalog_snapshot_hash") or sha(snapshot.content)!=snapshot.content_hash:
            raise PlanningBlocked("PINNED_CATALOG_STALE","候选目录快照不可验证，请重新检索")
        dataset=resolve_physical(db,wid,snapshot_ref)
        if dataset.get("schema_fingerprint")!=candidate.get("schema_fingerprint") or dataset.get("logical_locator")!=candidate.get("logical_locator"):
            raise PlanningBlocked("PINNED_CATALOG_STALE","候选表结构与固定目录不一致")
        allowed={field["field_id"] for field in dataset.get("fields",[])}
        if any(ref.get("target_ref",{}).get("field_id") not in allowed for ref in frame.get("physical_refs",[]) if ref.get("kind")=="FIELD"):
            raise PlanningBlocked("PINNED_CATALOG_STALE","候选字段已不在固定目录快照中")
        current_contract={"authority":"MetadataCandidate","catalog_fingerprints":{candidate["physical_source"]:candidate["schema_fingerprint"]},"join_checks":{}}
    else:
        from .semantic import get_execution_contract
        current_contract = get_execution_contract(db, wid, frame["binding_ref"])
        if current_contract.get("binding_content_hash") != frame.get("binding_content_hash") or current_contract.get("source_contract_hash") != frame.get("source_contract_hash"):
            raise PlanningBlocked("SEMANTIC_REVISION_STALE", "语义映射或来源已变化，请重新召回确认")
    source = db.get(Datasource, frame["datasource_id"])
    if not source or source.workspace_id != wid:
        raise PlanningBlocked("SOURCE_UNAVAILABLE", "查询数据源不可用")
    if source.username != "ecommerce_reader" or source.database != "ecommerce_validation":
        from .pipeline_catalog import enrollment_current
        if not enrollment_current(db, source):
            raise PlanningBlocked("LOCAL_SCOPE_REQUIRED", "请管理员先登记本空间的本地只读语义验证源；不授予生产执行权限")
    # Verify the SQL and parameter bytes still match the stored frame.
    clean_frame = {k: v for k, v in frame.items() if k != "content_hash"}
    if canonical_hash(clean_frame) != frame["content_hash"] or canonical_hash({"sql": preview["sql"]}) != preview["sql_hash"] or canonical_hash(preview["parameters"]) != preview["parameters_hash"] or preview["parameters"] != frame["resolved_parameters"]:
        raise PlanningBlocked("FRAME_HASH_MISMATCH", "查询帧或SQL参数已变化，需重新预览")
    ast_template = re.sub(r"%\(([a-z_][a-z0-9_]*)\)s", r":\1", preview["sql"])
    if preview.get("sql_ast_template") != ast_template:
        raise PlanningBlocked("SQL_TEMPLATE_MISMATCH", "查询模板与执行参数不一致")
    tree = read_query(ast_template)
    refs = table_refs(tree)
    current = {(a["namespace"], a["table_name"]): a for a in inspect_catalog(source)}
    verified = []
    for namespace, name in refs:
        if namespace not in source.schemas:
            raise PlanningBlocked("SCHEMA_FORBIDDEN", "查询超出数据源可用范围")
        stored = db.scalar(select(Asset).where(Asset.workspace_id == wid, Asset.datasource_id == source.id,
            Asset.namespace == namespace, Asset.table_name == name, Asset.state == "active"))
        if not stored or (namespace, name) not in current or stored.fingerprint != current[(namespace, name)]["fingerprint"]:
            raise PlanningBlocked("SOURCE_STALE", "目录与真实表结构不一致，请重新核验")
        if current_contract.get("authority") in {"AssetRevision+CatalogSnapshot", "MetadataCandidate"} and current_contract["catalog_fingerprints"].get(namespace + "." + name) != stored.fingerprint:
            raise PlanningBlocked("PINNED_CATALOG_STALE", "真实结构已偏离当前映射固定的目录快照，重新采集不能自动改变历史映射")
        from .pipeline_catalog import batch_gate
        # Agent currently executes as the explicitly authorized local admin;
        # its authorization gates call check_bundle_access before this function.
        # All physical quality/freshness gates apply here as on SQL/API paths.
        batch_gate(db, wid, stored)
        verified.append({"asset_id": stored.id, "physical_object": namespace + "." + name, "fingerprint": stored.fingerprint})
    with connect(source) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        from .pipeline_catalog import verify_readonly_source
        verify_readonly_source(conn, source)
        privileges = conn.execute("SELECT current_user, r.rolsuper, r.rolcreatedb, r.rolcreaterole FROM pg_roles r WHERE r.rolname=current_user").fetchone()
        if not privileges or any(privileges[1:]):
            raise PlanningBlocked("READONLY_ACCOUNT_REQUIRED", "执行账户不符合最小只读权限要求")
        writable = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = ANY(%s) AND table_type='BASE TABLE' AND (has_table_privilege(quote_ident(table_schema)||'.'||quote_ident(table_name),'INSERT') OR has_table_privilege(quote_ident(table_schema)||'.'||quote_ident(table_name),'UPDATE') OR has_table_privilege(quote_ident(table_schema)||'.'||quote_ident(table_name),'DELETE'))", (source.schemas,)).fetchone()[0]
        if writable:
            raise PlanningBlocked("READONLY_ACCOUNT_REQUIRED", "执行账户仍有业务表写权限")
        grain_verification = None
        if current_contract.get("authority") == "AssetRevision+CatalogSnapshot":
            grain_verification = verify_physical_grain(conn, frame)
        physical_source = frame["physical_source"]
        field = frame["time_expression"].split(".")[-1]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", field):
            raise PlanningBlocked("TIME_MAPPING_INVALID", "时间映射无效")
        # Coverage is observed from the exact source. Not merely a static date label.
        from psycopg import sql
        ns, name = physical_source.split(".")
        if frame.get("time_physical_source"):
            ns, name = frame["time_physical_source"].split(".")
            field = frame["time_field_name"]
        elif frame["metric_id"] == "promotion_orders":
            ns, name, field = "dwd", "dwd_orders", "purchased_at"
        coverage = conn.execute(sql.SQL("SELECT min({}), max({}) FROM {}.{}").format(sql.Identifier(field), sql.Identifier(field), sql.Identifier(ns), sql.Identifier(name))).fetchone()
        start = date.fromisoformat(frame["resolved_parameters"]["start_date"])
        end = date.fromisoformat(frame["resolved_parameters"]["end_date"])
        coverage_start=coverage[0].date() if isinstance(coverage[0],datetime) else coverage[0]
        coverage_end=coverage[1].date() if isinstance(coverage[1],datetime) else coverage[1]
        if (not coverage_start or start < coverage_start or end - timedelta(days=1) > coverage_end) and frame.get('execution_authority')!='MetadataCandidate':
            raise PlanningBlocked("TIME_COVERAGE_UNKNOWN", "请求时间超出当前快照已观测的数据覆盖范围")
        plan = None
        if explain:
            raw = conn.execute("EXPLAIN (FORMAT JSON) " + preview["sql"], preview["parameters"]).fetchone()[0]
            plan = raw[0]["Plan"]
            if plan["Total Cost"] > frame["budget"]["max_total_cost"] or plan["Plan Rows"] > frame["budget"]["max_plan_rows"]:
                raise PlanningBlocked("RESOURCE_BUDGET_EXCEEDED", "查询计划超出本地验证预算")
        if frame["dimension_ids"]:
            null_query = sql.SQL("SELECT count(*) FROM ({}) AS checked_result WHERE dimension_value IS NULL").format(sql.SQL(preview["sql"]))
            if conn.execute(null_query, preview["parameters"]).fetchone()[0]:
                raise PlanningBlocked("DIMENSION_NULL_MEMBER", "当前维度策略拒绝空成员，真实查询会产生空分组，已阻断")
        join_ids = set(current_contract.get("base_join_ids", []))
        for dim in frame["dimension_ids"]:
            join_ids.update(current_contract.get("dimension_join_ids", {}).get(dim, []))
        join_verifications = []
        for join_id in sorted(join_ids):
            join = current_contract["join_checks"][join_id]
            def physical(target):
                from .semantic import resolve_physical
                ds = resolve_physical(db, wid, target)
                return sql.Identifier(ds["namespace"], ds["physical_name"])
            left, right = physical(join["from_dataset_ref"]), physical(join["to_dataset_ref"])
            from .semantic import resolve_physical
            left_keys = [resolve_physical(db, wid, p["from"])["name"] for p in join["key_pairs"]]
            right_keys = [resolve_physical(db, wid, p["to"])["name"] for p in join["key_pairs"]]
            if join["null_key_policy"] == "ERROR":
                for relation, keys in ((left, left_keys), (right, right_keys)):
                    nulls = conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE {}").format(relation, sql.SQL(" OR ").join(sql.SQL("{} IS NULL").format(sql.Identifier(k)) for k in keys))).fetchone()[0]
                    if nulls:
                        raise PlanningBlocked("JOIN_NULL_KEY", "关联映射要求拒绝空键，真实源发现空值，查询已阻断")
            grouped = sql.SQL(", ").join(sql.Identifier(k) for k in right_keys)
            duplicates = conn.execute(sql.SQL("SELECT count(*) FROM (SELECT {} FROM {} GROUP BY {} HAVING count(*) > 1) AS duplicate_keys").format(grouped, right, grouped)).fetchone()[0]
            conditions = sql.SQL(" AND ").join(sql.SQL("l.{} = r.{}").format(sql.Identifier(lk), sql.Identifier(rk)) for lk, rk in zip(left_keys, right_keys))
            orphans = conn.execute(sql.SQL("SELECT count(*) FROM {} l WHERE NOT EXISTS (SELECT 1 FROM {} r WHERE {})").format(left, right, conditions)).fetchone()[0]
            if duplicates or (orphans and join["join_type"] == "INNER"):
                raise PlanningBlocked("JOIN_UNVERIFIED", "关联键存在重复或 INNER JOIN 丢失源记录，不能给出可能膨胀或漏算的结果")
            if join["expected_cardinality"] == "1:1":
                grouped_left = sql.SQL(", ").join(sql.Identifier(k) for k in left_keys)
                left_duplicates = conn.execute(sql.SQL("SELECT count(*) FROM (SELECT {} FROM {} GROUP BY {} HAVING count(*) > 1) AS duplicate_keys").format(grouped_left, left, grouped_left)).fetchone()[0]
                if left_duplicates:
                    raise PlanningBlocked("JOIN_UNVERIFIED", "声明的 1:1 关联在源端存在重复")
            join_verifications.append({"join_id": join_id, "target_duplicates": duplicates, "unmatched_source_rows": orphans})
        if not current_contract.get("join_checks") and frame["dimension_ids"] == ["customer_state"]:
            stats = conn.execute('SELECT (SELECT count(*)-count(DISTINCT customer_id) FROM dim.dim_customers), (SELECT count(*) FROM dwd.dwd_fct_orders b LEFT JOIN dim.dim_customers c ON b.customer_id=c.customer_id WHERE c.customer_id IS NULL)').fetchone()
            if stats != (0, 0):
                raise PlanningBlocked("JOIN_UNVERIFIED", "地区关联存在重复或无法匹配记录")
        measure_counts = observe_measure_rows(conn, frame, preview["parameters"])
        warnings = []
        if measure_counts and measure_counts["null_measure_rows"] and frame.get("metric_null_policy") == "REJECT_ROW_AND_WARN":
            warnings.append({"code": "NULL_MEASURE_ROWS_REJECTED", "message": "已按指标规则排除统计字段为空的记录", "row_count": measure_counts["null_measure_rows"]})
        return {"verified_sources": verified, "data_coverage": {"start": str(coverage[0]), "end": str(coverage[1])},
            "data_as_of": str(coverage[1]), "account": privileges[0], "read_only": True,
            "plan": {"total_cost": plan["Total Cost"], "plan_rows": plan["Plan Rows"]} if plan else None,
            "join_verifications": join_verifications,
            "grain_verification": grain_verification, "measure_row_counts": measure_counts, "warnings": warnings,
            "no_data": bool(measure_counts is not None and measure_counts["valid_measure_rows"] == 0 and frame.get("empty_result_policy") == "NO_DATA"),
            "scope": "local_validation", "business_approved": False, "checked_at": now().isoformat()}


def verify_physical_grain(conn, frame):
    """Check the declared base grain independently of aggregate deduplication."""
    from psycopg import sql
    fields = frame.get("physical_grain_fields", [])
    if not fields or len(fields) != len(set(fields)) or any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in fields):
        raise PlanningBlocked("PHYSICAL_GRAIN_UNKNOWN", "物理粒度键缺失或无效")
    namespace, name = frame["physical_source"].split(".")
    table = sql.Identifier(namespace, name)
    grouped = sql.SQL(", ").join(sql.Identifier(field) for field in fields)
    nulls = conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE {}").format(table, sql.SQL(" OR ").join(sql.SQL("{} IS NULL").format(sql.Identifier(field)) for field in fields))).fetchone()[0]
    duplicates = conn.execute(sql.SQL("SELECT count(*) FROM (SELECT {} FROM {} GROUP BY {} HAVING count(*) > 1) AS duplicate_grain").format(grouped, table, grouped)).fetchone()[0]
    if nulls or duplicates:
        raise PlanningBlocked("PHYSICAL_GRAIN_VIOLATION", "声明的物理粒度键存在空值或重复，不能通过聚合掩盖粒度问题")
    return {"fields": fields, "null_key_rows": nulls, "duplicate_key_groups": duplicates}


def observe_measure_rows(conn, frame, parameters):
    if frame.get('observation_queries'):
        periods=[]
        for period,query in zip(frame['time_periods'],frame['observation_queries']):
            observed=conn.execute(query,parameters).fetchone()
            periods.append({**period,'source_rows':observed[0],'valid_measure_rows':observed[1], 'null_measure_rows':observed[0]-observed[1]})
        return {**{key:sum(p[key] for p in periods) for key in ('source_rows','valid_measure_rows','null_measure_rows')},'periods':periods}
    if not frame.get("observation_sql"):
        return None
    observed = conn.execute(frame["observation_sql"], parameters).fetchone()
    return {"source_rows": observed[0], "valid_measure_rows": observed[1], "null_measure_rows": observed[0] - observed[1]}


def apply_result_policies(frame, measure_counts, values):
    no_data = bool(measure_counts is not None and measure_counts["valid_measure_rows"] == 0 and frame.get("empty_result_policy") == "NO_DATA")
    warnings = []
    if frame.get('metadata_candidate',{}).get('operation')=='RATIO_SUM':
        metric_index=frame['result_shape'].index('metric_value')
        if any(row[metric_index] is None for row in values):
            warnings.append({'code':'ZERO_DENOMINATOR','message':'部分比值的分母合计为零或为空，结果保留为空值'})
    if measure_counts and any(p['valid_measure_rows']==0 for p in measure_counts.get('periods',[])):
        warnings.append({'code':'PERIOD_NO_DATA','message':'部分时间段没有有效记录，不补零，也不更换基期'})
    if measure_counts and measure_counts["null_measure_rows"] and frame.get("metric_null_policy") == "REJECT_ROW_AND_WARN":
        warnings.append({"code": "NULL_MEASURE_ROWS_REJECTED", "message": "已按指标规则排除统计字段为空的记录", "row_count": measure_counts["null_measure_rows"]})
    return ([] if no_data else values), no_data, warnings
