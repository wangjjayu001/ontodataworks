"""Controlled worker interruptions in the isolated v1.1 acceptance workspace."""
from runtime import *
import concurrent.futures,time,secrets
from uuid import uuid4

state=json.loads((ROOT/'local/v11-validation-state.json').read_text(encoding='utf-8'))
creds=json.loads((ROOT/'local/credentials.json').read_text(encoding='utf-8'))
c=Client();c.login(creds['admin_username'],creds['admin_password']);wid=state['workspace_id'];base=f'/workspaces/{wid}/pipeline'
def p(path,method='GET',body=None,expected=200):return c.call(base+path,method,body,expected)
checks=[]
def check(name,condition=True):
    assert condition,name
    checks.append({'name':name,'passed':True});print('PASS '+name,flush=True)
def body(d):return {k:d[k] for k in ['kind','name','config','enabled','revision']}
def create(kind,name,config,enabled=False):return p('/definitions','POST',{'kind':kind,'name':name,'config':config,'enabled':enabled})
def run(d,key=None,batch=None):return p(f"/definitions/{d['id']}/runs",'POST',{'request_key':key or uuid4().hex,'batch_key':batch or uuid4().hex,'business_date':'2026-11-19'})
def wait(r,status,timeout=25):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        item=p('/runs/'+r['id'])
        if item['status']==status:return item
        if item['status'] in {'SUCCEEDED','FAILED','CANCELLED','UNKNOWN'}:raise AssertionError(item)
        time.sleep(.3)
    raise AssertionError('timeout '+r['id'])
defs=p('/definitions');sync=next(d for d in defs if d['id']==state['sync_id'])
p(f"/definitions/{sync['id']}/runs",'POST',{'request_key':uuid4().hex,'batch_key':'old-contract','business_date':'2026-11-19'},409)
check('recollected_catalog_does_not_rewrite_task_contract')
old_revision=sync['revision']
sync=p('/definitions/'+str(sync['id']),'PUT',body(sync))
check('reviewed_task_new_revision_pins_new_catalog',sync['revision']==old_revision+1)
a=create('collect','依赖循环测试 A',{'datasource_id':state['source_ids']['source']})
b=create('collect','依赖循环测试 B',{'datasource_id':state['source_ids']['source'],'dependencies':[a['id']]})
payload=body(a);payload['config']['dependencies']=[b['id']]
p('/definitions/'+str(a['id']),'PUT',payload,409);check('dependency_cycle_rejected')
p('/definitions/'+str(b['id']),'DELETE');p('/definitions/'+str(a['id']),'DELETE')
key=uuid4().hex;batch='concurrent:'+key
clients=[]
for i in range(3):
    client=Client();client.login(creds['admin_username'],creds['admin_password']);clients.append(client)
def parallel(client):return client.call(base+f"/definitions/{sync['id']}/runs",'POST',{'request_key':key,'batch_key':batch,'business_date':'2026-11-19'})
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(parallel,clients))
check('concurrent_idempotency_returns_one_instance',len({r['id'] for r in results})==1)
wait(results[0],'SUCCEEDED')
# Stop only the new pipeline worker; existing Agent/UI remain online.
compose('stop','pipeline-worker')
try:
    pending=run(sync,batch='ack-loss:'+uuid4().hex)
    rid=pending['id']
    sql(f"UPDATE wb_pipeline_run SET status='RUNNING',attempt=1,lease_until=now()+interval '5 minutes' WHERE id='{rid}' AND workspace_id={wid};")
    # Test process replaces local finish only. Target transaction commits normally.
    script="""from app import pipeline_execution as e
original=e.finish
class LostAcknowledgement(Exception): pass
def interrupt(db,run,status,evidence,target=None):
    if target is not None and status=='SUCCEEDED':raise LostAcknowledgement()
    return original(db,run,status,evidence,target)
e.finish=interrupt
try:e.execute('"""+rid+"""')
except LostAcknowledgement:print('Simulated target commit acknowledgement loss')
"""
    print(compose('exec','-T','api','python','-c',script))
    check('target_committed_without_local_batch',sql(f"SELECT count(*) FROM pipe._wb_batch_receipt WHERE run_id='{rid}';",database=state['target_db'])=='1' and sql(f"SELECT count(*) FROM wb_data_batch WHERE run_id='{rid}';")=='0')
    sql(f"UPDATE wb_pipeline_run SET lease_until=now()-interval '1 second' WHERE id='{rid}';")
finally:compose('start','pipeline-worker')
unknown=wait(pending,'UNKNOWN')
recovered=p('/runs/'+rid+'/reconcile','POST',{})
check('expired_lease_becomes_unknown_then_receipt_recovers',recovered['status']=='SUCCEEDED' and sql(f"SELECT count(*) FROM wb_data_batch WHERE run_id='{rid}';")=='1')
check('receipt_recovery_did_not_duplicate_rows',sql('SELECT count(*) FROM pipe.ods_orders;',database=state['target_db'])=='5')
compose('stop','pipeline-worker')
try:
    no_receipt=run(sync,batch='no-receipt:'+uuid4().hex)
    sql(f"UPDATE wb_pipeline_run SET status='RUNNING',attempt=1,lease_until=now()-interval '1 second' WHERE id='{no_receipt['id']}';")
finally:compose('start','pipeline-worker')
wait(no_receipt,'UNKNOWN')
reconciled=p('/runs/'+no_receipt['id']+'/reconcile','POST',{})
check('missing_receipt_reconciled_under_target_lock',reconciled['status']=='FAILED' and reconciled['evidence']['committed']==False)
p('/runs/'+no_receipt['id']+'/retry','POST',{});wait(no_receipt,'SUCCEEDED');check('reconciled_no_commit_can_retry_safely')
# Exercise finite retry and extracted row limits without changing the target.
cfg=dict(sync['config']);cfg['watermark_column']=None;cfg['max_rows']=2
limited=create('sync','批次上限与有限重试',cfg)
limited_run=run(limited)
for i in range(3):
    failed=wait(limited_run,'FAILED')
    if i<2:p('/runs/'+limited_run['id']+'/retry','POST',{})
p('/runs/'+limited_run['id']+'/retry','POST',{},409)
check('bounded_retries_and_batch_limit',failed['attempt']==3 and sql('SELECT count(*) FROM pipe.ods_orders;',database=state['target_db'])=='5')
p('/definitions/'+str(limited['id']),'DELETE')
scheduled=create('collect','周期时区触发验收',{'datasource_id':state['source_ids']['source'],'schedule':{'cron':'* * * * *','timezone':'Asia/Shanghai'}},True)
deadline=time.monotonic()+25;scheduled_runs=[]
while time.monotonic()<deadline:
    scheduled_runs=[r for r in p('/runs') if r['definition_id']==scheduled['id']]
    if scheduled_runs:break
    time.sleep(.4)
check('cron_timezone_trigger_persisted',bool(scheduled_runs) and scheduled_runs[0]['business_date']=='2026-11-19' and scheduled_runs[0]['request_key'].startswith('cron:'))
wait(scheduled_runs[0],'SUCCEEDED')
gaps=p(f"/definitions/{scheduled['id']}/schedule-gaps?start=2026-11-19T00:00:00Z&end_exclusive=2026-11-19T00:02:00Z")
check('scheduler_gap_diagnostic_has_expected_periods',len(gaps['periods'])==2 and gaps['missing']>=0)
payload=body(scheduled);payload['enabled']=False;p('/definitions/'+str(scheduled['id']),'PUT',payload)
check('cron_can_be_disabled')
bf=p('/definitions/'+str(scheduled['id'])+'/backfill','POST',{'start':'2026-11-16','end_exclusive':'2026-11-18'})
bf2=p('/definitions/'+str(scheduled['id'])+'/backfill','POST',{'start':'2026-11-16','end_exclusive':'2026-11-18'})
for row in bf:wait(row,'SUCCEEDED')
check('backfill_dates_and_idempotency',{r['business_date'] for r in bf}=={'2026-11-16','2026-11-17'} and [r['id'] for r in bf]==[r['id'] for r in bf2])
check('outbox_consumption_idempotent',sql(f"SELECT count(*) FROM wb_pipeline_event e JOIN wb_pipeline_run r ON r.id=e.run_id WHERE r.workspace_id={wid} AND NOT e.delivered;")=='0')
result={'all_passed':True,'workspace_id':wid,'checks':checks,'count':len(checks)}
(ROOT/'evidence/v1.1/recovery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'all_passed':True,'checks':len(checks)}))
