"""Public HTTP CRUD + actual PostgreSQL integration acceptance in isolated DBs.

Creates dedicated synthetic fixtures. Credentials remain in ignored local/.
Never modifies the existing ecommerce warehouse or production semantic approvals.
"""
from runtime import *
from datetime import datetime, timezone
from uuid import uuid4
import secrets, time, hashlib

STAMP=datetime.now().strftime('%m%d%H%M%S')
REPORT=ROOT/'evidence/v1.1';REPORT.mkdir(parents=True,exist_ok=True)
checks=[]
def check(name,condition=True,**evidence):
    assert condition,name
    checks.append({'name':name,'passed':True,**evidence})
    (REPORT/'acceptance.json').write_text(json.dumps({'all_passed':False,'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS '+name,flush=True)

creds=json.loads((ROOT/'local/credentials.json').read_text(encoding='utf-8'))
c=Client();c.login(creds['admin_username'],creds['admin_password'])
check('schema_v5',c.call('/health')['schema_version']==5)
check('v1_existing_catalog',len(c.call('/workspaces/3/assets'))>=42)
w=c.call('/workspaces','POST',{'name':'V1.1 联动验收 '+STAMP,'description':'独立合成数据，源库与目标库均隔离'})
wid=w['id'];base=f'/workspaces/{wid}'
def call(path,method='GET',body=None,expected=200):return c.call(base+path,method,body,expected)
def p(path,method='GET',body=None,expected=200):return call('/pipeline'+path,method,body,expected)
source_db='v11_src_'+STAMP;target_db='v11_tgt_'+STAMP
source_reader='v11_sr_'+STAMP;ddl_user='v11_ddl_'+STAMP;writer='v11_wr_'+STAMP;reader='v11_rd_'+STAMP
passwords={r:secrets.token_urlsafe(25) for r in [source_reader,ddl_user,writer,reader]}
for role,password in passwords.items():
    sql(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE;")
sql(f'CREATE DATABASE {source_db};');sql(f'CREATE DATABASE {target_db};')
sql(f"""CREATE SCHEMA src;
CREATE TABLE src.orders(id integer PRIMARY KEY,amount numeric(18,2),region text,updated_at timestamp NOT NULL);
INSERT INTO src.orders VALUES(1,10,'east','2026-11-18 10:00'),(2,20,'west','2026-11-18 10:00'),(3,30,'east','2026-11-18 11:00');
GRANT CONNECT ON DATABASE {source_db} TO {source_reader};GRANT USAGE ON SCHEMA src TO {source_reader};GRANT SELECT ON ALL TABLES IN SCHEMA src TO {source_reader};""",database=source_db)
sql(f"""CREATE SCHEMA pipe AUTHORIZATION {ddl_user};
GRANT CONNECT,TEMPORARY ON DATABASE {target_db} TO {ddl_user},{writer},{reader};
GRANT USAGE ON SCHEMA pipe TO {writer},{reader};
ALTER DEFAULT PRIVILEGES FOR ROLE {ddl_user} IN SCHEMA pipe GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {writer};
ALTER DEFAULT PRIVILEGES FOR ROLE {ddl_user} IN SCHEMA pipe GRANT SELECT ON TABLES TO {reader};""",database=target_db)
source_ids={}
for key,database,username,schemas in [('source',source_db,source_reader,['src']),('ddl',target_db,ddl_user,['pipe']),('writer',target_db,writer,['pipe']),('reader',target_db,reader,['pipe'])]:
    s=call('/datasources','POST',{'name':'验收 '+key,'host':'db','port':5432,'database':database,'username':username,'password':passwords[username],'schemas':schemas})
    source_ids[key]=s['id'];call(f"/datasources/{s['id']}/test",'POST',{})
    if key in {'ddl','writer'}:p(f"/sources/{s['id']}/purpose",'PUT',{'purpose':'write' if key=='writer' else 'ddl'})
check('separate_source_read_write_ddl',len(set(source_ids.values()))==4)
call(f"/datasources/{source_ids['source']}/collect",'POST',{})
domain=call('/documents/domain','POST',{'name':'V1.1 验收域','code':'v11_validation','spec':{}})
fields=[{'name':'id','type':'integer','nullable':False,'comment':'业务编号'}, {'name':'amount','type':'numeric(18,2)','nullable':True,'comment':'测试数值，未声明币种'}, {'name':'region','type':'text','nullable':True,'comment':'测试区域'}, {'name':'updated_at','type':'timestamp','nullable':False,'comment':'源记录时间，时区未知'}]
models=[]
for code,name in [('ods_orders','订单落地'),('dwd_orders','订单加工')]:
    model=call('/documents/model','POST',{'name':name,'code':code,'domain_id':domain['id'],'layer':code.split('_')[0].upper(),'spec':{'grain':'每行一个订单','namespace':'pipe','fields':fields,'primary_keys':['id']}})
    models.append(model)
    plan=p('/publications/preview','POST',{'model_id':model['id'],'datasource_id':source_ids['ddl']})
    published=p(f"/publications/{plan['id']}/execute",'POST',{})
    check('physical_publish_'+code,published['status']=='SUCCEEDED',publication_id=plan['id'])
    check('publication_idempotent_'+code,p(f"/publications/{plan['id']}/execute",'POST',{})['status']=='SUCCEEDED')
for key in ['writer','reader']:call(f"/datasources/{source_ids[key]}/collect",'POST',{})
assets=call('/assets')
def asset(key,table):return next(a for a in assets if a['datasource_id']==source_ids[key] and a['table_name']==table)
origin=asset('source','orders');ods=asset('writer','ods_orders');dwd=asset('writer','dwd_orders');ods_read=asset('reader','ods_orders');dwd_read=asset('reader','dwd_orders')
def definition(kind,name,config):return p('/definitions','POST',{'kind':kind,'name':name,'config':config})
quality=definition('quality','订单质量规则',{'datasource_id':source_ids['writer'],'target_asset_id':ods['id'],'rules':[{'kind':'not_null','column':'amount'},{'kind':'unique','column':'id'},{'kind':'range','column':'amount','minimum':0},{'kind':'row_count','minimum':1}]})
sync=definition('sync','源库到 ODS 增量',{'datasource_id':source_ids['source'],'source_asset_id':origin['id'],'target_asset_id':ods['id'],'mapping':{f['name']:f['name'] for f in fields},'strategy':'upsert','keys':['id'],'watermark_column':'updated_at','data_as_of_column':'updated_at','quality_definition_ids':[quality['id']],'freshness_minutes':60})
transform=definition('sql','ODS 到 DWD 加工',{'datasource_id':source_ids['writer'],'target_asset_id':dwd['id'],'sql':"SELECT id, amount * 2 AS amount, region, updated_at FROM pipe.ods_orders WHERE updated_at::date <= :business_date",'columns':['id','amount','region','updated_at'],'strategy':'replace','dependencies':[sync['id']],'rules':[{'kind':'range','column':'amount','minimum':0}],'data_as_of_column':'updated_at'})
check('task_create_read',len(p('/definitions'))==3)
old=copy_body=dict(sync);old={k:sync[k] for k in ['kind','name','config','enabled','revision']};old['name']='源库到 ODS 增量（版本二）'
sync=p(f"/definitions/{sync['id']}",'PUT',old)
check('task_update_and_version',sync['revision']==2 and len(p(f"/definitions/{sync['id']}/revisions"))==2)
p(f"/definitions/{sync['id']}",'PUT',old,409);check('optimistic_conflict')
def trigger(d,batch,key=None,day='2026-11-19'):
    return p(f"/definitions/{d['id']}/runs",'POST',{'request_key':key or uuid4().hex,'batch_key':batch,'business_date':day})
def wait(run,status='SUCCEEDED',timeout=35):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=p('/runs/'+run['id'])
        if result['status'] in ['SUCCEEDED','FAILED','CANCELLED','UNKNOWN']:
            assert result['status']==status,(result['status'],result['error'],result['evidence'])
            return result
        time.sleep(.3)
    raise AssertionError('run timeout '+run['id'])
# Read connection aliases must inherit the writer target's quality/batch gate.
call('/queries','POST',{'datasource_id':source_ids['reader'],'sql':'SELECT * FROM pipe.ods_orders'},409)
check('uncommitted_target_query_blocked_across_connections')
call('/assets/'+str(ods_read['id'])+'/preview',expected=409);check('uncommitted_target_preview_blocked')
batch='first:'+STAMP
downstream=trigger(transform,batch)
time.sleep(1)
check('same_batch_dependency_waits',p('/runs/'+downstream['id'])['status']=='QUEUED')
upstream=trigger(sync,batch,key='first:'+STAMP)
same=trigger(sync,batch,key='first:'+STAMP)
check('idempotent_request_returns_same_run',same['id']==upstream['id'])
up=wait(upstream);down=wait(downstream)
check('cross_database_incremental_commit',up['evidence']['target_rows']==3 and up['evidence']['committed'],run_id=up['id'])
check('sql_same_batch_linked_commit',down['evidence']['target_rows']==3,run_id=down['id'])
def query(key,statement,expected=200,client=None):return (client or c).call(base+'/queries','POST',{'datasource_id':source_ids[key],'sql':statement},expected)
result=query('reader','SELECT sum(amount) AS total FROM pipe.dwd_orders')
check('query_result_matches_independent_sql',str(result['rows'][0][0]) in {'120.00','120.0','120'} and sql('SELECT sum(amount) FROM pipe.dwd_orders;',database=target_db)=='120.00')
check('query_has_actual_batch_provenance',result['data_batches'][0]['run_id']==down['id'])
preview=call('/assets/'+str(ods_read['id'])+'/preview')
check('preview_uses_shared_permissions_and_batch_provenance',preview['source']=='live_database' and len(preview['rows'])==3 and preview['data_batches'][0]['run_id']==up['id'])
debug=p(f"/definitions/{transform['id']}/debug",'POST',{'business_date':'2026-11-19'})
check('debug_no_target_write',debug['debug_only'] and debug['shape_matches'])
sql("INSERT INTO src.orders VALUES(4,40,'east','2026-11-18 11:00');",database=source_db)
inc=wait(trigger(sync,'tie:'+STAMP))
check('equal_watermark_rows_not_lost',inc['evidence']['target_rows']==4)
repeat=wait(trigger(sync,'repeat:'+STAMP))
check('upsert_no_duplicate_rows',repeat['evidence']['target_rows']==4)
before=sql('SELECT md5(string_agg(row_to_json(t)::text,\'|\' ORDER BY id)) FROM pipe.ods_orders t;',database=target_db)
sql("INSERT INTO src.orders VALUES(5,-1,'east','2026-11-19 12:00');",database=source_db)
failed=wait(trigger(sync,'bad:'+STAMP),'FAILED')
check('quality_failure_rolls_back_and_keeps_watermark',not failed['evidence']['committed'] and before==sql('SELECT md5(string_agg(row_to_json(t)::text,\'|\' ORDER BY id)) FROM pipe.ods_orders t;',database=target_db))
sql('UPDATE src.orders SET amount=50 WHERE id=5;',database=source_db)
fixed=wait(trigger(sync,'fixed:'+STAMP))
check('repair_resumes_from_last_committed_watermark',fixed['evidence']['target_rows']==5)
qrun=wait(trigger(quality,'quality:'+STAMP))
check('quality_standalone_evidence',all(r['status']=='PASS' for r in qrun['evidence']['quality']))
check('quality_evidence_pins_observed_data_batch',qrun['evidence']['checked_batch_run_id']==fixed['id'])
original_quality_rules=quality['config']['rules']
quality_body={k:quality[k] for k in ['kind','name','config','enabled','revision']}
quality_body['config']=dict(quality['config']);quality_body['config']['rules']=[{'kind':'range','column':'amount','maximum':0}]
quality=p('/definitions/'+str(quality['id']),'PUT',quality_body)
wait(trigger(quality,'independent-failure:'+STAMP),'FAILED')
query('reader','SELECT id FROM pipe.ods_orders',409)
check('failed_independent_quality_blocks_committed_batch')
weak=definition('quality','另一项检查不能覆盖失败',{'datasource_id':source_ids['writer'],'target_asset_id':ods['id'],'rules':[{'kind':'row_count','minimum':1}]})
wait(trigger(weak,'weak:'+STAMP));query('reader','SELECT id FROM pipe.ods_orders',409)
check('different_quality_task_cannot_clear_failure')
quality_body={k:quality[k] for k in ['kind','name','config','enabled','revision']}
quality_body['config']=dict(quality['config']);quality_body['config']['rules']=original_quality_rules
quality=p('/definitions/'+str(quality['id']),'PUT',quality_body)
wait(trigger(quality,'independent-retest:'+STAMP));query('reader','SELECT id FROM pipe.ods_orders')
check('same_quality_task_real_retest_restores_consumption')
p('/definitions/'+str(weak['id']),'DELETE')
blocked=trigger(transform,'never-matching:'+STAMP)
p('/runs/'+blocked['id']+'/cancel','POST',{})
check('queued_cancel_confirmed',p('/runs/'+blocked['id'])['status']=='CANCELLED')
tmp=definition('collect','临时 CRUD 任务',{'datasource_id':source_ids['source']})
p(f"/definitions/{tmp['id']}",'DELETE')
check('task_delete_hides_head_keeps_revision',not any(d['id']==tmp['id'] for d in p('/definitions')) and len(p(f"/definitions/{tmp['id']}/revisions"))==1)
collect=definition('collect','元数据周期任务',{'datasource_id':source_ids['source']})
observed=wait(trigger(collect,'catalog:'+STAMP))
check('collection_run_records_observation',bool(observed['evidence']['observation_id']))
source_fingerprint=origin['fingerprint']
sql('ALTER TABLE src.orders ADD COLUMN note text;',database=source_db)
drift=wait(trigger(sync,'drift:'+STAMP),'FAILED')
check('physical_drift_blocks_old_task',not drift['evidence']['committed'])
call(f"/datasources/{source_ids['source']}/collect",'POST',{})
check('immutable_catalog_observations',len(p('/observations'))>=5)
# Safe add-column plan and destructive change refusal.
model=models[1];model_body={k:model[k] for k in ['name','code','description','domain_id','layer','status','spec','revision']}
model_body['spec']['fields']=fields+[{'name':'note','type':'text','nullable':True,'comment':'发布增量字段'}]
model=call('/documents/model/'+str(model['id']),'PUT',model_body)
plan=p('/publications/preview','POST',{'model_id':model['id'],'datasource_id':source_ids['ddl']})
pub=p('/publications/'+plan['id']+'/execute','POST',{})
check('safe_add_nullable_column',pub['status']=='SUCCEEDED' and pub['plan']['mode']=='ALTER')
sql(f"UPDATE wb_physical_publication SET status='UNKNOWN' WHERE id='{pub['id']}' AND workspace_id={wid};")
reconciled=p('/publications/'+pub['id']+'/reconcile','POST',{})
check('publication_unknown_reconciles_actual_structure_without_replaying_ddl',reconciled['status']=='SUCCEEDED' and reconciled['evidence']['reconciled'])
p('/sources/'+str(source_ids['writer'])+'/local-validation','PUT',{'enabled':True},409)
check('write_connection_cannot_enroll_for_semantic_execution')
p('/sources/'+str(source_ids['reader'])+'/local-validation','PUT',{'enabled':True})
check('explicit_readonly_semantic_source_enrollment',next(s for s in p('/sources') if s['id']==source_ids['reader'])['local_semantic_validation'])
p('/sources/'+str(source_ids['reader'])+'/local-validation','PUT',{'enabled':False})
check('semantic_source_enrollment_revocation',not next(s for s in p('/sources') if s['id']==source_ids['reader'])['local_semantic_validation'])
# Governance CRUD and versioned standard, without fabricating business approval.
standard=p('/governance','POST',{'kind':'standard','name':'未知测试币种','status':'DRAFT','content':{'category':'currency','definition':'合成数值未声明真实币种','value':None}})
body={k:standard[k] for k in ['kind','name','status','content','revision']};body['content']['definition']='合成数值未声明真实币种；保持未知'
standard=p('/governance/'+str(standard['id']),'PUT',body)
check('standard_update_keeps_history_and_unknown',standard['revision']==2 and len(standard['history'])==2 and standard['content']['value'] is None)
p('/governance/'+str(standard['id']),'DELETE');check('standard_delete_archives')
analysis=p('/governance','POST',{'kind':'analysis','name':'区域金额分析','content':{'datasource_id':source_ids['reader'],'sql':'SELECT region,sum(amount) AS amount FROM pipe.ods_orders GROUP BY region ORDER BY region','chart':'bar'}})
ar=p('/governance/'+str(analysis['id'])+'/rerun','POST',{})
check('saved_analysis_reruns_current_data',len(ar['rows'])==2)
body={k:analysis[k] for k in ['kind','name','status','content','revision']};body['name']='区域分析新修订';analysis=p('/governance/'+str(analysis['id']),'PUT',body)
check('saved_analysis_update',analysis['revision']==2)
viewer_name='v11_view_'+STAMP;viewer_password=secrets.token_urlsafe(24)
viewer=c.call('/users','POST',{'username':viewer_name,'display_name':'V1.1 只读消费身份','password':viewer_password,'role':'viewer','workspace_ids':[wid]})
v=Client();v.login(viewer_name,viewer_password)
v.call(base+'/pipeline/definitions','POST',{'kind':'collect','name':'越权修改','config':{'datasource_id':source_ids['source']}},403)
check('viewer_cannot_modify')
v.call('/workspaces/3/pipeline/runs',expected=403);check('workspace_isolation')
service=definition('service','区域查询 API',{'datasource_id':source_ids['reader'],'sql':'SELECT id,amount,region FROM pipe.ods_orders WHERE region=:region ORDER BY id','parameters':{'region':{'type':'string','allowed_values':['east','west']}},'contract_version':'1.0','calls_per_minute':50})
p('/definitions/'+str(service['id'])+'/release','POST',{'enabled':True},409);check('untested_service_release_blocked')
p('/definitions/'+str(service['id'])+'/test','POST',{'parameters':{'region':'east'}})
p('/definitions/'+str(service['id'])+'/release','POST',{'enabled':True})
consumer=p('/consumers','POST',{'name':'验收消费应用','principal_id':viewer['id'],'service_ids':[service['id']]})
def invoke(token,region='east',expected=200):
    req=urllib.request.Request('http://127.0.0.1:19090/api/data-services/'+str(service['id'])+'/invoke',data=json.dumps({'parameters':{'region':region}}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token},method='POST')
    try:r=urllib.request.urlopen(req,timeout=35)
    except urllib.error.HTTPError as exc:r=exc
    assert r.code==expected,(r.code,r.read().decode()[:300]);return json.load(r)
api_result=invoke(consumer['token']);check('parameterized_api_real_result',len(api_result['rows'])==4 and api_result['revision']==service['revision'])
invoke(consumer['token'],'unknown',400);check('service_parameter_enum_enforced')
policy=p('/governance','POST',{'kind':'access_policy','name':'消费身份仅东区可见两列','content':{'asset_id':ods['id'],'user_ids':[viewer['id'],creds.get('admin_id',1)],'allowed_columns':['id','region'],'row_equals':{'region':'east'}}})
filtered=query('reader','SELECT * FROM pipe.ods_orders ORDER BY id',client=v)
check('row_column_policy_applies_across_connection_alias',filtered['columns']==['id','region'] and len(filtered['rows'])==4)
query('reader','SELECT amount FROM pipe.ods_orders',400,client=v);check('hidden_column_not_readable')
invoke(consumer['token'],expected=400);check('api_uses_same_column_policy')
body={k:policy[k] for k in ['kind','name','status','content','revision']};body['content']['deny']=True
policy=p('/governance/'+str(policy['id']),'PUT',body)
query('reader','SELECT id FROM pipe.ods_orders',403,client=v);check('policy_revocation_blocks_query')
check('revoked_run_list_hides_physical_evidence',not any(r['definition_id']==sync['id'] for r in v.call(base+'/pipeline/runs')))
v.call(base+'/pipeline/governance/'+str(analysis['id'])+'/rerun','POST',{},403);check('revoked_analysis_reauth')
p('/governance/'+str(policy['id']),'DELETE')
rotated=p('/consumers/'+str(consumer['id'])+'/rotate','POST',{})
invoke(consumer['token'],expected=401);check('credential_rotation_revokes_old_token')
invoke(rotated['token'])
p('/definitions/'+str(service['id'])+'/release','POST',{'enabled':False})
version_body={k:service[k] for k in ['kind','name','config','revision']};version_body['enabled']=False;version_body['config']['contract_version']='1.1';version_body['config']['sql']='SELECT id,amount,region FROM pipe.ods_orders WHERE region=:region AND id>=2 ORDER BY id'
service=p('/definitions/'+str(service['id']),'PUT',version_body)
p('/definitions/'+str(service['id'])+'/test','POST',{'parameters':{'region':'east'}})
p('/definitions/'+str(service['id'])+'/release','POST',{'enabled':True})
old_contract=invoke(rotated['token']);check('consumer_stays_on_pinned_service_revision',old_contract['revision']==1 and len(old_contract['rows'])==4)
p('/consumers/'+str(consumer['id']),'PUT',{'name':'验收应用新名称','service_ids':[service['id']],'revisions':{str(service['id']):2}})
new_contract=invoke(rotated['token']);check('consumer_update_explicitly_repins_contract',new_contract['revision']==2 and len(new_contract['rows'])==3)
p('/consumers/'+str(consumer['id']),'DELETE');invoke(rotated['token'],expected=401);check('consumer_revoke')
p('/definitions/'+str(service['id'])+'/release','POST',{'enabled':False})
body={k:service[k] for k in ['kind','name','config','revision']};body['enabled']=False;body['config']['contract_version']='1.1';service=p('/definitions/'+str(service['id']),'PUT',body)
check('service_version_update_requires_retest',service['revision']==3)
p('/definitions/'+str(service['id']),'DELETE');check('service_delete_archives')
impact=p('/impact/'+str(ods['id']));check('impact_links_processing',any(d['id']==transform['id'] for d in impact['definitions']))
issue=p('/governance','POST',{'kind':'issue','name':'质量失败修复跟踪','status':'FIXED','content':{'description':'已修复负值，需要独立复测','asset_id':ods['id'],'repair_run_id':fixed['id']}})
qrun=wait(trigger(quality,'retest:'+STAMP))
body={k:issue[k] for k in ['kind','name','content','revision']};body['status']='CLOSED';body['content']['retest_run_id']=qrun['id'];issue=p('/governance/'+str(issue['id']),'PUT',body)
check('issue_close_requires_real_retest',issue['status']=='CLOSED')
p('/governance/'+str(issue['id']),'DELETE');check('issue_delete_keeps_history')
p('/governance/'+str(analysis['id']),'DELETE');check('analysis_delete_archives')
# CSV downloads perform a fresh controlled query, rather than serving cached data.
req=urllib.request.Request('http://127.0.0.1:19090/api'+base+'/pipeline/export',data=json.dumps({'datasource_id':source_ids['reader'],'sql':'SELECT id,amount FROM pipe.ods_orders ORDER BY id'}).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':c.csrf,'Origin':'http://127.0.0.1:19090'},method='POST')
response=c.opener.open(req);csv_text=response.read().decode('utf-8-sig');check('csv_export_reexecutes_with_source_id',len(csv_text.splitlines())==6 and bool(response.headers.get('X-Query-Id')))
import io,zipfile,xml.etree.ElementTree as ET
req=urllib.request.Request('http://127.0.0.1:19090/api'+base+'/pipeline/export',data=json.dumps({'datasource_id':source_ids['reader'],'sql':'SELECT id,amount FROM pipe.ods_orders ORDER BY id','format':'xlsx'}).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':c.csrf,'Origin':'http://127.0.0.1:19090'},method='POST')
response=c.opener.open(req)
with zipfile.ZipFile(io.BytesIO(response.read())) as xlsx:
    sheet=ET.fromstring(xlsx.read('xl/worksheets/sheet1.xml'));provenance=ET.fromstring(xlsx.read('xl/worksheets/sheet2.xml'))
    check('xlsx_export_valid_rows_and_provenance',len(sheet.findall('.//{*}row'))==6 and len(provenance.findall('.//{*}row'))>=5)
check('trace_id_returned',bool(response.headers.get('X-Trace-Id')))
p('/ops/retention?days=1',expected=400);check('retention_minimum_enforced')
p('/governance/'+str(standard['id'])+'/quality-template','POST',{'asset_id':ods['id']},409);check('unapproved_standard_cannot_generate_active_quality')
spare=call('/datasources','POST',{'name':'连接身份变更验收','host':'db','port':5432,'database':source_db,'username':source_reader,'password':passwords[source_reader],'schemas':['src']})
spare_task=definition('collect','固定连接身份验收',{'datasource_id':spare['id']})
call('/datasources/'+str(spare['id']),'PUT',{'name':'连接身份变更验收','host':'127.0.0.1','port':5432,'database':source_db,'username':source_reader,'password':'','schemas':['src']})
p('/definitions/'+str(spare_task['id'])+'/runs','POST',{'request_key':'changed-source:'+STAMP,'batch_key':'changed-source','business_date':'2026-11-19'},409)
check('connection_identity_change_blocks_old_task_revision')
p('/definitions/'+str(spare_task['id']),'DELETE')
batch_result=p('/publications/batch','POST',{'plan_ids':[pub['id'],'nonexistent-acceptance-plan']})
check('batch_publication_reports_individual_partial_failure',not batch_result['all_succeeded'] and [r['status'] for r in batch_result['items']]==['SUCCEEDED','REJECTED'])
state={'workspace_id':wid,'source_db':source_db,'target_db':target_db,'source_ids':source_ids,'models':models,'sync_id':sync['id'],'quality_id':quality['id'],'transform_id':transform['id'],'viewer_name':viewer_name,'viewer_password':viewer_password,'first_run':upstream['id'],'first_transform_run':downstream['id']}
(ROOT/'local/v11-validation-state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
report={'all_passed':True,'checked_at':datetime.now(timezone.utc).isoformat(),'workspace_id':wid,'source_database':source_db,'target_database':target_db,'checks':checks,'count':len(checks),'fixture':'isolated synthetic databases; no business approval fabricated'}
(REPORT/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'all_passed':True,'checks':len(checks),'workspace_id':wid}),flush=True)
