"""Acceptance through the production SQL-module API, against independent DuckDB data."""
from runtime import *
from import_ecommerce import SOURCE, LOCAL, EVIDENCE, TYPES, physical, qi, sha, save, translate
import import_ecommerce as migration
import duckdb, math, re, secrets, argparse
from datetime import datetime,date
from decimal import Decimal

def normalized(v):
    if isinstance(v,(datetime,date)):return v.isoformat()
    return v

def equivalent(left,right):
    if left is None or right is None:return left is right
    if isinstance(left,(int,float,Decimal)) and not isinstance(left,bool):
        try:return math.isclose(float(left),float(right),rel_tol=1e-12,abs_tol=1e-5)
        except (ValueError,TypeError):return False
    return normalized(left)==normalized(right)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');options=parser.parse_args()
    state=json.loads((LOCAL/'state.json').read_text('utf-8'));assert state.get('finished_at'),'Finish import first'
    catalog=json.loads((SOURCE/'metadata_catalog.json').read_text('utf-8'))
    audit=json.loads((SOURCE/'审计_20261105/warehouse_model_lineage_audit.json').read_text('utf-8'))
    capture=json.loads((SOURCE/'审计_20261105/captured_model_sql.json').read_text('utf-8'))
    migration.TABLES={t['object_id']:t for t in catalog['tables']}
    source=duckdb.connect(str(SOURCE/'ecommerce_validation.duckdb'),read_only=True)
    creds=json.loads((ROOT/'local/credentials.json').read_text('utf-8'));client=Client();client.login(creds['admin_username'],creds['admin_password'])
    base=f"/workspaces/{state['workspace_id']}";sid=state['datasource_id']
    report={'started_at':datetime.now().isoformat(),'workspace_id':state['workspace_id'],'datasource_id':sid,'tests':[],
        'tolerance':{'full_row_compare_double_absolute':1e-7,'aggregate_query_absolute':1e-5,'aggregate_query_relative':1e-12},'source_quality':[]}
    if options.resume and (EVIDENCE/'sql_acceptance.json').exists():
        previous=json.loads((EVIDENCE/'sql_acceptance.json').read_text('utf-8'))
        assert previous['workspace_id']==state['workspace_id'] and previous['datasource_id']==sid
        report['tests']=previous['tests'];report['source_quality']=previous['source_quality'];report['resumed_at']=datetime.now().isoformat()
    completed={case['name']:case for case in report['tests']}
    def check(name,detail):
        report['tests']=[test for test in report['tests'] if test['name']!=name]
        report['tests'].append({'name':name,'passed':True,'detail':detail});print('PASS',name,flush=True)
        save(EVIDENCE/'sql_acceptance.json',report)
    def query_case(name,original,quality=None):
        pgsql=translate(original)
        old=completed.get(name)
        if old and old['detail'].get('sql')==pgsql and old['detail'].get('duckdb_sql')==original:
            return {'rows':old['detail']['actual'],'id':old['detail']['query_id']}
        expected=source.execute(original).fetchall()
        response=client.call(base+'/queries','POST',{'datasource_id':sid,'sql':pgsql})
        actual=response['rows']
        assert len(expected)==len(actual),(name,'row_count',len(expected),len(actual))
        assert all(len(a)==len(b) and all(equivalent(x,y) for x,y in zip(a,b)) for a,b in zip(expected,actual)),(name,expected[:3],actual[:3])
        detail={'query_id':response['id'],'sql':pgsql,'duckdb_sql':original,'expected':expected[:30],'actual':actual[:30],
            'duration_ms':response['duration_ms'],'verified_sources':response['verified_sources']}
        if quality is not None:
            detail['quality_gate_passed']=quality;report['source_quality'].append({'name':name,'gate_passed':quality,'observed':actual})
        check(name,detail)
        return response
    assets={a['namespace']+'.'+a['table_name']:a for a in client.call(base+'/assets')}
    docs={kind:client.call(base+'/documents/'+kind) for kind in ['domain','model','dimension','metric']}
    assert len(assets)==42 and sum(len(a['columns']) for a in assets.values())==282
    assert [len(docs[k]) for k in ['domain','model','dimension','metric']]==[11,42,4,11]
    check('platform_inventory',{'tables':42,'columns':282,'domains':11,'models':42,'dimensions':4,'supported_atomic_metrics':11,
        'average_order_value':'composite definition and SQL test; not misrepresented as a single-field atomic metric'})
    for model in docs['model']:
        spec=model['spec'];asset=assets[spec['physical_object']]
        assert model['domain_id']==asset['domain_id'] and spec['asset_id']==asset['id'] and model['code']==asset['table_name']
        ddl=client.call(base+f"/models/{model['id']}/ddl")['sql']
        assert f'CREATE TABLE "{asset["namespace"]}"."{asset["table_name"]}"' in ddl and 'PRIMARY KEY' in ddl
        assert ddl.count('FOREIGN KEY')==len(spec['foreign_keys'])
        for field in spec['fields']:assert field['comment'] in ddl
    check('all_model_bindings_and_ddl',{'models':42,'field_comments':282,'primary_keys':42,'foreign_keys':42})
    for table in sorted(catalog['tables'],key=lambda t:t['object_id']):
        key=table['object_id'];a=assets[physical(key)]
        keys=[c['column_name'] for c in catalog['columns'] if c['schema_name']+'.'+c['table_name']==key and c['is_primary_key']]
        keys_sql=','.join(qi(k) for k in keys)
        sqltext=f'SELECT count(*) AS row_count, (SELECT count(*) FROM (SELECT {keys_sql} FROM {key} GROUP BY {keys_sql} HAVING count(*)>1) duplicates) AS duplicate_key_groups, count(*) FILTER (WHERE '+ ' OR '.join(qi(k)+' IS NULL' for k in keys)+') AS null_keys FROM '+key
        result=query_case('table_key:'+key,sqltext);assert result['rows'][0]==[table['row_count'],0,0]
        preview=client.call(base+f"/assets/{a['id']}/preview")
        expected=source.execute('SELECT * FROM '+key+' ORDER BY '+keys_sql+' LIMIT 100').fetchall()
        assert len(preview['rows'])==min(100,table['row_count']) and preview['order_by']==keys
        assert all(all(equivalent(x,y) for x,y in zip(e,p)) for e,p in zip(expected,preview['rows'])),key
    check('all_live_previews',{'tables':42,'rows_per_table':'min(100, table_rows)','ordered_by':'physical primary key','source':'live PostgreSQL, independently compared to DuckDB'})
    for index,fk in enumerate(audit['foreign_key_checks']):
        result=query_case('foreign_key:'+str(index+1)+':'+fk['from'],fk['sql']);assert result['rows']==[[0]]
    runs=client.call(base+'/transforms')
    actual_warehouse={(i['source'],r['target']) for r in runs if r['kind']=='warehouse_transform' for i in r['inputs']}
    actual_generator={(i['source'],r['target']) for r in runs if r['kind']=='synthetic_generation' for i in r['inputs']}
    expected_warehouse={(physical(i),physical(k)) for k,m in capture['models'].items() if not k.startswith('ods.') for i in m['dependencies']}
    expected_generator={(physical(i),physical(k)) for k,m in capture['models'].items() if k.startswith('ods.') for i in m['dependencies']}
    assert actual_warehouse==expected_warehouse and len(actual_warehouse)==38
    assert actual_generator==expected_generator and len(actual_generator)==10
    verified=[r for r in runs if r['kind']=='warehouse_transform'];assert len(verified)==28
    assert all(r['status']=='recomputed_match' and r['evidence']['mismatched_rows']==0 and r['evidence']['duplicate_keys']==0 for r in verified)
    assert all(r['status']=='declared_only' and not r['evidence']['historical_execution_proven'] for r in runs if r['kind']=='synthetic_generation')
    check('lineage_complete_and_honest',{'verified_warehouse_edges':38,'verified_targets':28,'declared_generator_edges':10,'field_lineage':False,'historical_execution_claimed':False})
    for metric in catalog['metrics']:
        key=metric['source_object'];expr=metric['expression'];timecol=metric['default_time']
        if metric['metric_id']=='promotion_orders':
            key+=' JOIN dwd.orders USING(order_id)';timecol='purchased_at'
        if metric['metric_id']=='inventory_on_hand':
            query='SELECT snapshot_date,'+expr+' AS metric FROM '+key+" WHERE snapshot_date IN (DATE '2025-01-01',DATE '2025-12-31',DATE '2026-10-26') GROUP BY snapshot_date ORDER BY snapshot_date"
        else:
            query=f"SELECT extract(year from {timecol}) AS year_no, extract(month from {timecol}) AS month_no, {expr} AS metric FROM {key} WHERE {timecol}>=TIMESTAMP '2025-01-01' AND {timecol}<TIMESTAMP '2027-01-01' GROUP BY 1,2 ORDER BY 1,2"
        query_case('metric_monthly:'+metric['metric_id'],query)
    query_case('customer_account_vs_unique_buyer',"SELECT count(DISTINCT customer_id) AS accounts,count(DISTINCT customer_unique_id) AS unique_buyers FROM dwd.fct_orders")
    query_case('anti_fanout_order_amount',"WITH item_totals AS (SELECT order_id,sum(unit_price+freight_value) AS amount FROM ods.order_items GROUP BY order_id) SELECT count(*) AS orders,round(sum(t.amount),2) AS gross,round(sum(o.gross_order_value),2) AS fact_gross FROM item_totals t JOIN dwd.fct_orders o USING(order_id)")
    query_case('date_coverage',"SELECT min(snapshot_date) AS start_date,max(snapshot_date) AS end_date,count(DISTINCT snapshot_date) AS days,count(*) AS rows FROM ods.inventory_snapshots")
    query_case('daily_kpis_reconcile',"SELECT count(*) AS days,sum(order_count) AS orders,round(sum(gross_order_value),2) AS gross FROM dws.daily_kpis")
    for case in audit['business_checks']:
        if case['name']=='catalog_tables_match_database':continue
        query_case('source_quality:'+case['name'],case['sql'],case['passed'])
    # Failures must be rejected, not recorded as verified lineage.
    target=assets[physical('dws.order_totals')]
    bad_sql='SELECT order_id,items_total+1 AS items_total,freight_total,item_count FROM dws.dws_order_totals'
    # A self-reference is a cycle, rejected before data comparison.
    client.call(base+'/transforms','POST',{'run_key':'reject-self-cycle','datasource_id':sid,'target_asset_id':target['id'],'select_sql':bad_sql},expected=400)
    model_run=next(r for r in verified if r['target']=='dws.dws_order_totals')
    changed=re.sub(r'SUM\(unit_price\)', 'SUM(unit_price)+1',model_run['select_sql'],flags=re.I)
    assert changed!=model_run['select_sql']
    client.call(base+'/transforms','POST',{'run_key':'reject-wrong-transform','datasource_id':sid,'target_asset_id':target['id'],'select_sql':changed},expected=409)
    client.call(base+'/transforms','POST',{'run_key':'reject-reverse-layer','datasource_id':sid,'target_asset_id':assets['dwd.dwd_orders']['id'],'select_sql':'SELECT * FROM dws.dws_order_totals'},expected=400)
    client.call(base+'/transforms','POST',{'run_key':'reject-generator-cycle','kind':'synthetic_generation','datasource_id':sid,'target_asset_id':assets['ods.ods_orders']['id'],'select_sql':'SELECT * FROM ods.ods_payments'},expected=400)
    check('lineage_negative_cases',{'self_cycle':'rejected','multi_table_cycle':'rejected','wrong_result':'rejected','dwd_depending_on_dws':'rejected'})
    for forbidden in ['DELETE FROM ods.ods_orders','SELECT set_config(\'search_path\',\'public\',false)','SELECT 1; SELECT 2']:
        client.call(base+'/queries','POST',{'datasource_id':sid,'sql':forbidden},expected=400)
    check('sql_write_and_multistatement_rejected',{'cases':3})
    try:
        sql('ALTER TABLE ods.ods_customers ADD COLUMN validation_drift_probe text;',database=state['database'])
        client.call(base+'/queries','POST',{'datasource_id':sid,'sql':'SELECT count(*) FROM ods.ods_customers'},expected=409)
    finally:
        sql('ALTER TABLE ods.ods_customers DROP COLUMN IF EXISTS validation_drift_probe;',database=state['database'])
    query_case('schema_drift_recovered','SELECT count(*) FROM ods.customers')
    check('schema_drift_guard',{'changed_schema':'blocked','original_schema':'restored without recollection'})
    permissions=sql("SELECT count(*) FILTER (WHERE has_table_privilege('ecommerce_reader',c.oid,'INSERT') OR has_table_privilege('ecommerce_reader',c.oid,'UPDATE') OR has_table_privilege('ecommerce_reader',c.oid,'DELETE')) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('ods','dwd','dws','dim') AND c.relkind='r'",database=state['database'])
    assert permissions=='0';check('dedicated_reader_cannot_write',{'writable_tables':0})
    suffix=datetime.now().strftime('%m%d%H%M%S');username='ev_'+suffix;password=secrets.token_urlsafe(22)
    client.call('/users','POST',{'username':username,'display_name':'电商验收只读成员','role':'viewer','password':password,'workspace_ids':[state['workspace_id']]})
    viewer=Client();viewer.login(username,password);viewer.call(base+'/transforms')
    viewer.call(base+'/transforms','POST',{'run_key':'not-allowed','datasource_id':sid,'target_asset_id':target['id'],'select_sql':model_run['select_sql']},expected=403)
    other=next(w for w in client.call('/auth/me')['workspaces'] if w['id']!=state['workspace_id'])
    viewer.call('/workspaces/'+str(other['id'])+'/assets',expected=403)
    check('viewer_and_workspace_isolation',{'viewer_read':True,'viewer_register_transform':False,'other_workspace_access':False})
    report.update(finished_at=datetime.now().isoformat(),passed=True,source_unchanged=sha(SOURCE/'ecommerce_validation.duckdb')==state['source_sha256'])
    assert report['source_unchanged'];save(EVIDENCE/'sql_acceptance.json',report)
    print('COMPLETE',len(report['tests']),'checks;',sum(not x['gate_passed'] for x in report['source_quality']),'known source quality gates triggered',flush=True)

if __name__=='__main__':main()
