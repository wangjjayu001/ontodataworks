"""CTE names must retain their semantics when a physical policy wraps a table."""
from runtime import *
state=json.loads((ROOT/'local/v11-validation-state.json').read_text(encoding='utf-8'))
creds=json.loads((ROOT/'local/credentials.json').read_text(encoding='utf-8'))
c=Client();c.login(creds['admin_username'],creds['admin_password']);wid=state['workspace_id'];base=f'/workspaces/{wid}'
reader=sql(f"SELECT username FROM wb_datasource WHERE id={state['source_ids']['reader']};")
sql(f"CREATE TABLE public.policy_scope(id integer PRIMARY KEY,region text,amount integer);INSERT INTO public.policy_scope VALUES(1,'east',10),(2,'west',20);GRANT SELECT ON public.policy_scope TO {reader};",database=state['target_db'])
# Clone only encrypted connector metadata inside the server, never read its secret.
script=f"""from app.db import SessionLocal,Datasource
with SessionLocal() as db:
 original=db.get(Datasource,{state['source_ids']['reader']})
 values={{c.name:getattr(original,c.name) for c in Datasource.__table__.columns if c.name not in {{'id','schemas','name'}}}}
 row=Datasource(**values,name='CTE政策专项只读连接',schemas=['public']);db.add(row);db.commit();print(row.id)
"""
sid=int(compose('exec','-T','api','python','-c',script))
c.call(base+f'/datasources/{sid}/collect','POST',{})
asset=next(a for a in c.call(base+'/assets') if a['datasource_id']==sid and a['table_name']=='policy_scope')
user=c.call('/auth/me')['user']
policy=c.call(base+'/pipeline/governance','POST',{'kind':'access_policy','name':'CTE作用域专项政策','content':{'asset_id':asset['id'],'user_ids':[user['id']],'allowed_columns':['id','region'],'row_equals':{'region':'east'}}})
checks=[]
for name,query,expected in [
 ('same_named_cte_keeps_filter_semantics','WITH policy_scope AS (SELECT id,region FROM public.policy_scope WHERE id=2) SELECT * FROM policy_scope',[]),
 ('cte_aggregate_keeps_scope_and_policy','WITH policy_scope AS (SELECT count(*) AS n FROM public.policy_scope) SELECT n FROM policy_scope',[[1]]),
 ('qualified_physical_table_policy_filters_rows','SELECT id,region FROM public.policy_scope',[[1,'east']])]:
 result=c.call(base+'/queries','POST',{'datasource_id':sid,'sql':query});assert result['rows']==expected,(name,result)
 checks.append({'name':name,'passed':True});print('PASS '+name,flush=True)
c.call(base+'/pipeline/governance/'+str(policy['id']),'DELETE')
(ROOT/'evidence/v1.1/policy-sql.json').write_text(json.dumps({'all_passed':True,'count':len(checks),'workspace_id':wid,'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
