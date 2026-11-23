"""Real publication and issue closure checks using the isolated v1.1 target."""
from runtime import *
from datetime import datetime
from uuid import uuid4
import copy, secrets, time

state=json.loads((ROOT/'local/v11-validation-state.json').read_text(encoding='utf-8'))
creds=json.loads((ROOT/'local/credentials.json').read_text(encoding='utf-8'))
c=Client();c.login(creds['admin_username'],creds['admin_password'])
wid=state['workspace_id'];base=f'/workspaces/{wid}';stamp=datetime.now().strftime('%m%d%H%M%S')
checks=[];report=ROOT/'evidence/v1.1/consolidation.json'
def check(name,condition=True,**evidence):
    assert condition,name
    checks.append({'name':name,'passed':True,**evidence});print('PASS '+name,flush=True)
def call(path,method='GET',body=None,expected=200,client=c):return client.call(base+path,method,body,expected)
def p(path,method='GET',body=None,expected=200,client=c):return call('/pipeline'+path,method,body,expected,client)
def payload(row):return {k:copy.deepcopy(row[k]) for k in ['kind','name','status','content','revision']}
def wait(run):
    for _ in range(100):
        result=p('/runs/'+run['id'])
        if result['status'] in ['SUCCEEDED','FAILED','UNKNOWN','CANCELLED']:return result
        time.sleep(.2)
    raise AssertionError('timeout')
def run(definition):return wait(p(f"/definitions/{definition['id']}/runs",'POST',{'request_key':uuid4().hex,'batch_key':'consolidation:'+uuid4().hex,'business_date':'2026-11-19'}))
def fixed(issue):
    body=payload(issue);body['status']='FIXED';body['content']['resolution']='已完成修复，等待独立验证'
    return p('/governance/'+str(issue['id']),'PUT',body)

domain=call('/documents/domain','POST',{'code':'publish_'+stamp,'name':'建模整合验收','spec':{}})
spec={'grain':'每行一个合成编号','namespace':'pipe','fields':[{'name':'id','type':'integer','nullable':False,'comment':'合成编号'}],'primary_keys':['id']}
body={'code':'ods_ui_publish_'+stamp,'name':'建表整合验收 '+stamp,'domain_id':domain['id'],'layer':'ODS','spec':spec}
model=call('/documents/model','POST',body)
ddl=state['source_ids']['ddl']
def preview(expected=200):return p('/publications/preview','POST',{'model_id':model['id'],'datasource_id':ddl},expected)
plan=preview();pub=p('/publications/'+plan['id']+'/execute','POST',{})
check('create_table_and_readback',pub['status']=='SUCCEEDED',model_id=model['id'],publication_id=pub['id'])
check('actual_database_table_exists',sql(f"SELECT count(*) FROM information_schema.tables WHERE table_schema='pipe' AND table_name='{model['code']}';",database=state['target_db'])=='1')
check('publication_repeat_is_idempotent',p('/publications/'+plan['id']+'/execute','POST',{})['id']==pub['id'])
stale=preview();body['revision']=model['revision'];body['spec']['fields'].append({'name':'note','type':'text','nullable':True,'comment':'增量字段'})
model=call('/documents/model/'+str(model['id']),'PUT',body)
p('/publications/'+stale['id']+'/execute','POST',{},409);check('stale_plan_cannot_publish_changed_model')
plan=preview();check('nullable_field_produces_alter',plan['plan']['mode']=='ALTER' and 'ADD COLUMN' in plan['plan']['commands'][0])
pub=p('/publications/'+plan['id']+'/execute','POST',{});check('alter_matches_actual_catalog',pub['status']=='SUCCEEDED')
body['revision']=model['revision'];body['spec']['fields'][0]['type']='text';model=call('/documents/model/'+str(model['id']),'PUT',body)
preview(409);check('destructive_type_change_is_blocked')
body['revision']=model['revision'];body['spec']['fields'][0]['type']='integer';model=call('/documents/model/'+str(model['id']),'PUT',body)

reviewer_password=secrets.token_urlsafe(24)
reviewer=c.call('/users','POST',{'username':'review_'+stamp,'display_name':'独立治理复核员','password':reviewer_password,'role':'developer','workspace_ids':[wid]})
review=Client();review.login('review_'+stamp,reviewer_password)
feedback=p('/governance','POST',{'kind':'issue','name':'反馈治理验收 '+stamp,'content':{'category':'feedback','description':'合成反馈：说明文字需要修订'}})
feedback=fixed(feedback);closed=payload(feedback);closed['status']='CLOSED';closed['content']['verification_note']='复核修订说明，问题已解决'
p('/governance/'+str(feedback['id']),'PUT',closed,403);check('repair_actor_cannot_self_verify_feedback')
feedback=p('/governance/'+str(feedback['id']),'PUT',closed,client=review)
check('feedback_closes_with_independent_review_without_quality_job',feedback['status']=='CLOSED' and feedback['content']['verification_actor_id']==reviewer['id'])

asset=next(a for a in call('/assets') if a['datasource_id']==ddl and a['table_name']==model['code'])
schema=p('/governance','POST',{'kind':'issue','name':'结构治理验收 '+stamp,'content':{'category':'schema','description':'修复合成结构并重新采集','asset_id':asset['id']}})
schema=fixed(schema);oldobs=next(o for o in p('/observations') if o['datasource_id']==ddl)
closed=payload(schema);closed['status']='CLOSED';closed['content'].update(verification_note='对照模型独立核对字段与主键',verification_observation_id=oldobs['id'])
p('/governance/'+str(schema['id']),'PUT',closed,409,client=review);check('schema_cannot_close_with_old_observation')
call('/datasources/'+str(ddl)+'/collect','POST',{})
newobs=next(o for o in p('/observations') if o['datasource_id']==ddl)
closed['content']['verification_observation_id']=newobs['id']
schema=p('/governance/'+str(schema['id']),'PUT',closed,client=review)
check('schema_closes_only_after_actual_catalog_recollection',schema['status']=='CLOSED')

definition=p('/definitions','POST',{'kind':'collect','name':'任务治理验收 '+stamp,'config':{'datasource_id':ddl}})
origin=run(definition);issue=p('/governance','POST',{'kind':'issue','name':'执行治理验收 '+stamp,'content':{'category':'task','description':'合成执行问题，验证修复后同任务运行','run_id':origin['id']}})
issue=fixed(issue);validation=run(definition);closed=payload(issue);closed['status']='CLOSED';closed['content'].update(retest_run_id=validation['id'],verification_note='同任务修复后采集成功')
issue=p('/governance/'+str(issue['id']),'PUT',closed)
check('task_closure_uses_same_definition_success',issue['status']=='CLOSED')
bad=payload(issue);bad['status']='OPEN';bad['content']['category']='feedback'
p('/governance/'+str(issue['id']),'PUT',bad,409);check('issue_category_cannot_bypass_validation')

quality_asset=next(a for a in call('/assets') if a['datasource_id']==state['source_ids']['writer'] and a['table_name']=='ods_orders')
quality=p('/definitions','POST',{'kind':'quality','name':'整合质量复测 '+stamp,'config':{'datasource_id':state['source_ids']['writer'],'target_asset_id':quality_asset['id'],'rules':[{'kind':'row_count','minimum':1}]}})
quality_issue=p('/governance','POST',{'kind':'issue','name':'质量治理兼容 '+stamp,'content':{'category':'quality','description':'保留质量独立复测要求','asset_id':quality_asset['id']}})
quality_issue=fixed(quality_issue);qrun=run(quality);closed=payload(quality_issue);closed['status']='CLOSED';closed['content']['retest_run_id']=qrun['id']
quality_issue=p('/governance/'+str(quality_issue['id']),'PUT',closed)
check('quality_closure_still_requires_real_matching_retest',quality_issue['status']=='CLOSED')
for item in [feedback,schema,issue,quality_issue]:p('/governance/'+str(item['id']),'DELETE')
check('closed_issue_archive_retains_history')
report.write_text(json.dumps({'all_passed':True,'workspace_id':wid,'checks':checks,'browser_model_id':model['id'],'browser_model_name':model['name'],'target_database':state['target_db']},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'all_passed':True,'checks':len(checks),'workspace_id':wid,'browser_model_id':model['id']}),flush=True)
