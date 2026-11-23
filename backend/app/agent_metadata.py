"""Run-local metadata discovery: clarify, validate source data, then confirm SQL."""
import copy
import re
from sqlalchemy import select

from .db import SessionLocal, User, now
from .agent_models import AgentJob, AgentRun
from .agent_planner import canonical_hash, new_id, normalize_metadata_candidate, compile_metadata_preview, source_check, PlanningBlocked


def queue_metadata(db, row, payload, req, discovery):
    from .agent import event
    req.pop('error', None)
    req['metadata_discovery'] = discovery
    req['metadata_route'] = True
    if not discovery or not discovery.get('tables'):
        question = '当前固定目录没有找到可访问的数据表。请补充数据源、表名或字段；采集目录并重建测试快照后可点击重新查找数据。'
    elif re.search(r'\broi\b|投资回报', ' '.join(req['intent_frame'].get('metric_terms', [])), re.I) and not any(
            a.get('slot') in {'roi_definition', 'metric_definition', 'semantic_definition'} for a in req.get('answers', [])):
        question = '请先确认 ROI 公式。随后系统会查找分子、分母、时间和类目字段，并检查对应期间是否有数据。'
        req.update(state='NEED_CLARIFICATION', clarifications=[{'id': new_id('clarify'), 'slot': 'roi_definition',
            'question': question, 'options': ['销售额÷投放费用', '利润÷投放费用', '其他口径，请说明分子和分母']}])
        event(db, row, payload, 'metadata.caliber.required', req['id'], {'table_count':len(discovery['tables'])})
        return
    else:
        req.update(state='DRAFT', clarifications=[], metadata_parse_version=req['requirement_version'] + 1,
            model_progress={'phase':'QUEUED','section':'metadata','label':'正在下探元数据并核对数据可用性',
                'started_at':now().isoformat(),'updated_at':now().isoformat()})
        db.add(AgentJob(id=new_id('job'),workspace_id=row.workspace_id,run_id=row.id,requirement_id=req['id'],kind='METADATA',status='QUEUED'))
        event(db,row,payload,'metadata.discovery.queued',req['id'],{'table_count':len(discovery['tables'])})
        return
    req.update(state='NEED_CLARIFICATION', clarifications=[{'id':new_id('clarify'),'slot':'semantic_definition','question':question,'options':[]}])


def metadata_job(job_id):
    from .agent import check_run_access, _retrieve, event, save_run, refresh_run_model_selection
    from .agent_worker import metadata_model_context, model_progress
    from .model_runtime import derive_metadata_candidate
    from .security import workspace_access
    with SessionLocal() as db:
        job=db.get(AgentJob,job_id)
        if not job or job.status not in {'QUEUED','RUNNING'}:
            return
        row=db.get(AgentRun,job.run_id); payload=copy.deepcopy(row.payload)
        previous=next(r for r in payload['requirements'] if r['id']==job.requirement_id)
        if payload.get('cancel_requested') or previous['state']!='DRAFT' or previous.get('metadata_parse_version')!=previous['requirement_version']:
            job.status='CANCELLED';db.commit();return
        user=db.get(User,row.actor_id);workspace_access(db,user,row.workspace_id);check_run_access(db,row,user)
        discovery=copy.deepcopy(previous['metadata_discovery']);intent=copy.deepcopy(previous['intent_frame']);bundle=copy.deepcopy(previous['bundle'])
    model_progress(job_id,'CONNECTING','metadata')
    refresh_run_model_selection(payload)
    selection=payload['model_selection']
    model_context=metadata_model_context(discovery)
    response=derive_metadata_candidate(intent.get('original_question') or intent['question'],
        {'data_classification':'synthetic','source_scope':'local_validation','intent_frame':intent,
         'caliber_answers':previous.get('answers',[]),'metadata_candidates':model_context,
         'warnings':['每个表字段必须来自目录；保留用户口径、筛选和独立时间段。缺分母、费用、利润等字段时返回具体缺口，不能编造 SQL 或字段。']},
        selection,request_id=job_id+':metadata',
        progress_callback=lambda phase:model_progress(job_id,phase,'metadata'))
    plan=response.get('output',{}).get('candidate_plan'); candidate=None;availability=None;validation_error=None
    gaps=response.get('output',{}).get('data_gaps') or []
    with SessionLocal() as db:
        job=db.get(AgentJob,job_id)
        row=db.scalar(select(AgentRun).where(AgentRun.id==job.run_id).with_for_update())
        payload=copy.deepcopy(row.payload);req=next(r for r in payload['requirements'] if r['id']==job.requirement_id)
        if payload.get('cancel_requested') or req['state']!='DRAFT' or req['requirement_version']!=previous['requirement_version']:
            job.status='CANCELLED';db.commit();return
        user=db.get(User,row.actor_id);workspace_access(db,user,row.workspace_id);check_run_access(db,row,user)
        payload['model_selection']=selection
        if plan is not None:
            try:
                candidate=normalize_metadata_candidate(plan,discovery,intent,bundle,previous.get('answers',[]))
                frame,preview=compile_metadata_preview(candidate,intent,bundle,row.id,req['id'],{'id':'availability-only'})
                checks=source_check(db,row.workspace_id,frame,preview,explain=False)
                availability={'status':'NO_DATA' if checks.get('no_data') else 'AVAILABLE','label':'当前时间与筛选下没有有效记录' if checks.get('no_data') else '已核对来源结构及对应数据',
                    'checked_at':checks.get('checked_at'),'period_counts':checks.get('measure_row_counts',{}),
                    'sources':checks.get('verified_sources',[]),'scope':'local_validation'}
                if checks.get('no_data'):
                    gaps=['当前日期与筛选条件下没有有效记录，请修改范围后重新查找。'];candidate=None
            except PlanningBlocked as exc:
                gaps=[exc.message];candidate=None;validation_error={'code':exc.code,'message':exc.message}
        req['data_availability']=availability or {'status':'MISSING_FIELDS','label':'已查目录，尚未找到支撑当前口径的完整数据',
            'checked_table_count':len(discovery['tables']),'checked_field_count':sum(len(t['fields']) for t in discovery['tables']),
            'model_table_count':model_context['model_table_count'],'model_field_count':model_context['model_field_count'],
            'truncated':model_context['truncated']}
        if validation_error:
            req['data_availability'].update(status='NEEDS_VALIDATION',label='已核对目录，需要补充字段含义或修正来源',validation_error=validation_error)
        req['metadata_candidate_model_call']={k:v for k,v in response.items() if k!='output'}
        req['metadata_candidate_model_call']['real_inference']=True
        if candidate:
            req.pop('data_gaps',None)
            req['metadata_candidate']=candidate
            _retrieve(db,row,payload,req,user)
        else:
            req['data_gaps']=[str(gap)[:500] for gap in gaps[:8]] or ['目录中的字段尚不能唯一支持该计算，请指定计算字段和口径。']
            req.update(state='NEED_CLARIFICATION',error=None,clarifications=[{'id':new_id('clarify'),'slot':'semantic_definition',
                'question':'；'.join(req['data_gaps']).rstrip('。；;')+'。请补充对应数据表/字段、调整口径或时间；也可采集数据后重新查找。','options':[]}])
            event(db,row,payload,'metadata.data_gap',req['id'],{'gaps':req['data_gaps'],'tables_checked':len(discovery['tables'])})
        req['model_progress']={'phase':'COMPLETE','label':'元数据与数据检查完成','updated_at':now().isoformat()}
        req['requirement_version']+=1
        job.status='DONE';save_run(db,row,payload);db.commit()
