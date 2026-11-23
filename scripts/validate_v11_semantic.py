"""Real new-target -> typed semantics -> model -> authorized Agent query.

Uses the isolated fixture from validate_v11_live.py. No business approvals.
"""
from runtime import ROOT, sql, Client as PlainClient
from validate_semantic_live import Client, canonical
from datetime import datetime, timezone
import copy, hashlib, json, time

state=json.loads((ROOT/'local/v11-validation-state.json').read_text(encoding='utf-8'))
credentials=json.loads((ROOT/'local/credentials.json').read_text(encoding='utf-8'))
c=Client('http://127.0.0.1:19090');c.login(credentials['admin_username'],credentials['admin_password'])
plain=PlainClient();plain.login(credentials['admin_username'],credentials['admin_password'])
wid=state['workspace_id'];base=f'/workspaces/{wid}/semantic';prefix=f'/workspaces/{wid}/agent';suffix=time.strftime('%H%M%S')
report={'suite':'pipeline-to-typed-agent/1.1','workspace_id':wid,'scope':'local_validation','production_approved':False,'checks':[],'receipts':[]}
output=ROOT/'evidence/v1.1/semantic-linkage.json'
def save():output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def check(name,condition=True,**details):
    assert condition,name
    report['checks'].append({'name':name,'passed':True,**details});save();print('PASS '+name,flush=True)
def call(path,method='GET',body=None,status=200,version=None):
    if '/semantic/' not in path and '/agent/' not in path:
        return plain.call(path,method,body,status)
    data,receipt=c.request(path,method,body,status,version);report['receipts'].append(receipt);return data
def ref(a):return {k:a[k] for k in ['workspace_id','asset_id','asset_type','revision']}

context=call('/workspaces/3/semantic/maintenance-context')
def template(key):return copy.deepcopy(next(x['asset_revision'] for x in context['assets'] if x['is_current'] and x['asset_ref']['asset_id']==key))
reader=state['source_ids']['reader']
assets=call(f'/workspaces/{wid}/assets')
physical=next(a for a in assets if a['datasource_id']==reader and a['table_name']=='ods_orders')
unknown={'policy_id':None,'revision':None,'resolution':'UNKNOWN'}
fields=[{'field_id':f['name'],'name':f['name'],'data_type':f['type'],'nullable':f['nullable'],'semantic_role':'IDENTIFIER' if f['name']=='id' else 'ATTRIBUTE','unit':None,'classification':'INTERNAL',
    'business_definition':{'name_zh':f.get('comment') or f['name'],'definition':f.get('comment') or f['name'],'aliases':[],'grain':None,'legacy_unit_text':None,'owner_ids':[],'steward_ids':[],'quality_rule_refs':[],'provenance_refs':[],'policy_ref':unknown}} for f in physical['columns']]
sid=f'catalog:v11:orders:{wid}:{suffix}'
dataset={'dataset_id':'pipe.orders','datasource_id':str(reader),'connector_kind':'POSTGRESQL','namespace':'pipe','physical_name':'ods_orders','logical_locator':'pipe.orders',
    'schema_fingerprint':physical['fingerprint'],'fingerprint_algorithm':'platform-catalog-fields-sha256-v1','fields':fields,'key_constraints':[{'constraint_id':'pk:orders','kind':'PRIMARY_KEY','field_ids':['id'],'target_dataset_ref':None,'target_field_ids':[],'observed_status':'UNKNOWN'}], 'policy_ref':unknown,'constraints_state':'DECLARED'}
call(base+'/catalog-snapshots','POST',{'catalog_snapshot':{'workspace_id':wid,'catalog_snapshot_id':sid,'source_revision':'v1.1-new-target-technical/1','datasets':[dataset]}})
check('new_published_target_registered_as_immutable_semantic_catalog')
raw=canonical({'scope':'仅隔离合成技术验证','definition':'按源记录时间统计新落地订单数，按id去重；不声明币种或生产业务批准','time':'updated_at源库原始时间，左闭右开，时区未知不换算'})
source={'workspace_id':wid,'asset_id':'source:v11-technical-'+suffix,'asset_type':'source','revision':1,'name':'V1.1 新表技术核验来源','definition':'明确记录合成订单的身份和时间规则','domain_ref':None,'aliases':[],'owner_ids':[],'steward_ids':[],'validity':{'from':None,'to':None},'provenance_refs':[],'policy_ref':unknown,
    'spec':{'source_kind':'TECHNICAL_QA','uri':f'archive://v11/{wid}','source_revision':'technical/1','content_sha256':hashlib.sha256(raw.encode()).hexdigest(),'classification':'INTERNAL','authority':'TECHNICAL_ONLY'}}
source=call(base+'/assets','POST',{'asset_revision':source})['asset_revision']
call(base+'/sources/'+source['asset_id']+'/archive','POST',{'revision':1,'raw_text':raw})
def common(a,key,name,definition):
    a.update(workspace_id=wid,asset_id=key,revision=1,name=name,definition=definition,domain_ref=None,aliases=[],owner_ids=[],steward_ids=[],policy_ref=unknown,provenance_refs=[ref(source)])
    return a
entity=common(template('entity:commerce.BrowsingSession'),'entity:v11.Order'+suffix,'合成落地订单','每行一个id标识的合成订单，updated_at为源记录时间')
entity['spec']={'kind':'ENTITY','properties':[{'property_id':'id','name':'订单编号','data_type':'INTEGER','semantic_role':'IDENTIFIER','nullable':False,'unit':None},{'property_id':'updated_at','name':'源记录时间','data_type':'TIMESTAMP','semantic_role':'EVENT_TIME','nullable':False,'unit':None}],'business_key':['id'],'grain':'每行一个订单编号'}
entity=call(base+'/assets','POST',{'asset_revision':entity})['asset_revision']
def prop(key):return {'entity_ref':ref(entity),'property_id':key}
metric=common(template('metric:session_count'),'metric:v11_order_count_'+suffix,'落地订单数','按源记录时间范围，对新落地表中的订单编号去重计数')
metric['spec'].update(entity_ref=ref(entity),grain={'description':'一个订单编号','property_refs':[prop('id')]},expression={'op':'call','function':'count_distinct','args':[{'op':'property','property_ref':prop('id')}]},distinct_property_refs=[prop('id')])
metric['spec']['time_policy']['property_ref']=prop('updated_at')
metric=call(base+'/assets','POST',{'asset_revision':metric})['asset_revision']
def field(key):return {'catalog_snapshot_id':sid,'datasource_id':str(reader),'dataset_id':'pipe.orders','field_id':key}
binding=common(template('binding:session_count.local_v1'),'binding:v11_order_count_'+suffix+'.local','落地订单数只读映射','固定新落地表的订单编号、源记录时间及物理粒度；仅本地技术验证')
binding['spec'].update(metric_ref=ref(metric),base_dataset_ref={k:v for k,v in field('id').items() if k!='field_id'},calculation={'op':'call','function':'count_distinct','args':[{'op':'field','field_ref':field('id')}]},
    property_mappings=[{'property_ref':prop(k),'expression':{'op':'field','field_ref':field(k)}} for k in ['id','updated_at']],physical_grain={'description':'真实源按id唯一','key_field_refs':[field('id')]},time_field_ref=field('updated_at'),coverage={'start':None,'end_exclusive':None,'data_as_of':None,'evidence_refs':[]})
binding=call(base+'/assets','POST',{'asset_revision':binding})['asset_revision']
diagnostic=call(base+'/assets/'+binding['asset_id']+'/implementation?revision=1')
check('new_target_typed_binding_is_executable',diagnostic['outcome']=='PASS')
call(f'/workspaces/{wid}/pipeline/sources/{reader}/local-validation','PUT',{'enabled':True})
pointer=call(base+'/deployments/local_validation')['deployment'];version=pointer['pointer_version'] if pointer else 0
deploy=call(base+'/deployments/local_validation/rebuild','POST',{'scope':'local_validation','draft_asset_refs':[ref(a) for a in [source,entity,metric,binding]],'expected_pointer_version':version,'reason':'新建表、同步批次与真实只读问数全链路技术验收'},version=version)
check('new_target_semantics_released_without_fabricating_business_approval',not deploy['production_approved'])
directory=call(prefix+'/models');model=next((m for m in directory['models'] if m.get('isDefault')),directory['models'][0])
selection={'catalog_version':directory['catalog_version'],'model':model.get('model',model['id']),'reasoningEffort':model['defaultReasoningEffort'],'serviceTier':None}
def wait(run):
    deadline=time.monotonic()+180
    while run['state'] in {'DRAFT','EXECUTING','VALIDATING'}:
        if time.monotonic()>deadline:raise AssertionError('Agent timeout')
        time.sleep(.5);run=call(prefix+'/runs/'+run['id'])['run']
    return run
question='请按源记录时间统计2026年11月18日的落地订单数，按订单编号去重。'
run=wait(call(prefix+'/runs','POST',{'message':{'text':question,'attachment_refs':[]},'model_selection':selection,'execution_mode':'local_validation','interaction_mode':'CONVERSATION'},status=202)['run'])
report['run_id']=run['id'];save()
assert len(run['requirements'])==1,run
check('real_model_uses_new_deployed_metric_vocabulary',run['model_call']['real_inference'])
def action(name,extra=None):
    global run
    req=run['requirements'][0]
    body={'expected_run_version':run['run_version'],'expected_requirement_version':req['requirement_version'],'intent_frame_hash':req.get('intent_frame_hash'),'bundle_content_hash':req.get('bundle_content_hash'),'metric_card_hash':req.get('metric_card_hash'),'query_frame_ref':req.get('query_frame_ref'),'sql_hash':req.get('sql_hash'),'gate_result_ref':req.get('gate_result_ref'),'authorization_id':req.get('authorization',{}).get('id'),'acknowledgement':True}
    body.update(extra or {})
    run=call(prefix+'/runs/'+run['id']+'/requirements/'+req['id']+'/'+name,'POST',body)['run']
    return run['requirements'][0]
while run['requirements'][0]['state']=='NEED_CLARIFICATION':
    clarification=run['requirements'][0]['clarifications'][0]
    assert clarification['slot']=='timezone',clarification
    action('clarifications',{'clarification_id':clarification['id'],'answer':'SOURCE'})
assert run['requirements'][0]['state']=='TARGET_CONFIRMATION',run
req=action('confirm-intent');assert req['state']=='NEED_CONFIRMATION',req
check('retrieval_pins_new_metric_and_binding',req['bundle']['selected_metric_ref']==ref(metric) and req['bundle']['selected_binding_ref']==ref(binding))
req=action('confirm-metric');assert req['state']=='PREVIEW_READY',req
req=action('gate');assert req['state']=='GATE_PASSED',req
req=action('authorize');assert req['state']=='AUTHORIZED',req
action('execute');run=wait(run);req=run['requirements'][0]
assert req['state']=='DELIVERED',req
expected=int(sql("SELECT count(DISTINCT id) FROM pipe.ods_orders WHERE updated_at>='2026-11-18' AND updated_at<'2026-11-19';",database=state['target_db']))
check('new_target_agent_result_equals_independent_sql',req['result']['rows']==[[expected]],actual=req['result']['rows'],expected=[[expected]])
check('agent_result_records_committed_pipeline_batch',bool(req['result']['data_batches'][0]['run_id']))
impact=call(f'/workspaces/{wid}/pipeline/impact/{physical["id"]}')
check('new_target_impact_reaches_exact_binding_revision',any(x['asset_id']==binding['asset_id'] and x['revision']==1 for x in impact['semantic_revisions']))
call(f'/workspaces/{wid}/pipeline/sources/{reader}/local-validation','PUT',{'enabled':False})
report.update(all_passed=True,count=len(report['checks']),finished_at=datetime.now(timezone.utc).isoformat(),sql=req['preview']['sql'],result=req['result'],binding_ref=ref(binding),metric_ref=ref(metric))
save();print(json.dumps({'all_passed':True,'checks':len(report['checks']),'run_id':run['id']}),flush=True)
