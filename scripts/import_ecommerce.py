"""Resumable, isolated DuckDB -> PostgreSQL migration; real warehouse rebuild.
Run with the existing ecommerce dataset's Python venv (duckdb + sqlglot).
Never edits the source snapshot. Existing databases without our state are refused.
"""
from runtime import *
import argparse, hashlib, secrets, re, time
from datetime import datetime, timezone
import duckdb, sqlglot
from sqlglot import exp

SOURCE=Path(r'D:/agent语义取数文件夹/验证数据/高可用验证')
LOCAL=ROOT/'local/ecommerce_import'
EVIDENCE=ROOT/'evidence/ecommerce'
DATABASE='ecommerce_validation'
DOMAINS={'订单交易':'trade','客户':'customer','商品目录':'product','商家':'seller','支付结算':'payment',
    '售后评价':'aftersales','履约物流':'fulfillment','库存供应链':'inventory','营销促销':'promotion','客户行为':'behavior','经营分析':'operations'}
TYPES={'VARCHAR':'text','BIGINT':'bigint','TIMESTAMP':'timestamp','BOOLEAN':'boolean','DOUBLE':'double precision',
    'DATE':'date','DECIMAL(38,2)':'numeric(38,2)','DECIMAL(22,2)':'numeric(22,2)'}

def stamp():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def lit(value):return "'"+str(value).replace("'","''")+"'"
def qi(value):
    assert re.fullmatch(r'[a-z][a-z0-9_]*',value),value
    return '"'+value+'"'
def physical(key):
    schema,name=key.split('.')
    return schema+'.'+schema+'_'+name
def qtable(key):return '.'.join(qi(s) for s in physical(key).split('.'))
def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

def remap_bindings(value):
    if isinstance(value,dict):return {k:remap_bindings(v) for k,v in value.items()}
    if isinstance(value,list):return [remap_bindings(v) for v in value]
    if isinstance(value,str):
        pattern=r'(?<![a-z0-9_])('+ '|'.join(re.escape(k) for k in sorted(TABLES,key=len,reverse=True))+r')(?![a-z0-9_])'
        return re.sub(pattern,lambda m:physical(m[0]),value)
    return value

def translate(query,execute=True):
    tree=sqlglot.parse_one(query,read='duckdb')
    for table in tree.find_all(exp.Table):
        if table.db and table.db+'.'+table.name in TABLES:table.set('this',exp.to_identifier(table.db+'_'+table.name))
    if execute:
        for node in list(tree.find_all(exp.DateDiff)):
            end=node.this.sql(dialect='postgres');start=node.expression.sql(dialect='postgres')
            assert str(node.args.get('unit')).upper()=='HOUR'
            node.replace(sqlglot.parse_one(f"(extract(epoch from (date_trunc('hour',{end})-date_trunc('hour',{start})))/3600)",read='postgres'))
        for node in list(tree.find_all(exp.ArgMax)):
            val=node.this.sql(dialect='postgres');key=node.expression.sql(dialect='postgres')
            node.replace(sqlglot.parse_one(f'(array_agg({val} ORDER BY {key} DESC NULLS LAST) FILTER (WHERE {val} IS NOT NULL AND {key} IS NOT NULL))[1]',read='postgres'))
        for node in tree.find_all(exp.Round):node.set('this',exp.Cast(this=node.this.copy(),to=exp.DataType.build('numeric')))
    return tree.sql(dialect='postgres')

def export_postgres(query,path):
    # Stream potentially millions of CSV rows without retaining stdout in RAM.
    with path.open('wb') as output:
        p=subprocess.run(ARGS+['exec','-T','db','psql','-U','workbench','-d',DATABASE,'-v','ON_ERROR_STOP=1','-c',
            f"COPY ({query}) TO STDOUT WITH (FORMAT CSV, HEADER TRUE, NULL '\\N');"],stdout=output,stderr=subprocess.PIPE)
    if p.returncode:raise RuntimeError(p.stderr.decode('utf-8',errors='replace'))

def upsert_document(client,base,kind,body):
    rows=client.call(base+'/documents/'+kind);old=next((r for r in rows if r['code']==body['code']),None)
    if old:
        if all(old[k]==v for k,v in body.items()):return old
        return client.call(base+'/documents/'+kind+'/'+str(old['id']),'PUT',{**body,'revision':old['revision']})
    return client.call(base+'/documents/'+kind,'POST',body)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',action='store_true',help='Continue this run using already completed full comparisons; default rechecks every live row')
    options=parser.parse_args()
    LOCAL.mkdir(parents=True,exist_ok=True);EVIDENCE.mkdir(parents=True,exist_ok=True)
    global TABLES
    catalog=json.loads((SOURCE/'metadata_catalog.json').read_text('utf-8'))
    audit=json.loads((SOURCE/'审计_20261105/warehouse_model_lineage_audit.json').read_text('utf-8'))
    capture=json.loads((SOURCE/'审计_20261105/captured_model_sql.json').read_text('utf-8'))
    TABLES={t['object_id']:t for t in catalog['tables']}
    columns={k:sorted([c for c in catalog['columns'] if c['schema_name']+'.'+c['table_name']==k],key=lambda c:c['ordinal_position']) for k in TABLES}
    keys={k:[c['column_name'] for c in columns[k] if c['is_primary_key']] for k in TABLES}
    fks={k:[] for k in TABLES}
    for fk in audit['foreign_key_checks']:
        tab,col=fk['from'].rsplit('.',1);remote,rcol=fk['to'].rsplit('.',1)
        fks[tab].append({'column':col,'target':remote,'target_column':rcol,'nullable':fk['nullable']})
    dbpath=SOURCE/'ecommerce_validation.duckdb';source_hash=sha(dbpath)
    assert source_hash==audit['database_sha256'], 'Source snapshot changed since independent audit'
    assert sha(SOURCE/'build_validation_warehouse.py')==capture['builder_sha256'],'Captured SQL is stale'
    statepath=LOCAL/'state.json'
    if statepath.exists():
        state=json.loads(statepath.read_text('utf-8'));assert state['source_sha256']==source_hash
    else:
        assert sql(f"SELECT count(*) FROM pg_database WHERE datname='{DATABASE}'")=='0','Refusing unowned existing database'
        state={'run_id':datetime.now().strftime('ecommerce-%Y%m%dT%H%M%S'),'started_at':stamp(),'source_sha256':source_hash,
            'database':DATABASE,'loaded':{},'verified':{},'transforms':{}}
        save(statepath,state)
    exportdir=LOCAL/'exports';exportdir.mkdir(exist_ok=True)
    con=duckdb.connect(str(dbpath),read_only=True);con.execute("SET memory_limit='600MB'")
    print('Source unchanged:',source_hash,'Run:',state['run_id'],flush=True)
    if sql(f"SELECT count(*) FROM pg_database WHERE datname='{DATABASE}'")=='0':sql(f'CREATE DATABASE {DATABASE};')
    sql('REVOKE ALL ON DATABASE '+DATABASE+' FROM PUBLIC;',database=DATABASE)
    sql(''.join(f'CREATE SCHEMA IF NOT EXISTS {s};' for s in ['ods','dwd','dws','dim']),database=DATABASE)
    manifest={'run_id':state['run_id'],'started_at':state['started_at'],'source_sha256':source_hash,
        'builder_sha256':capture['builder_sha256'],'import_script_sha256':sha(__file__),'snapshot_cutoff':'2026-10-26',
        'scope':'ODS snapshot import + actual PostgreSQL rebuild; not historical build proof','tables':[]}
    # ODS are copied; all 28 derived tables are rebuilt from the PostgreSQL inputs.
    order=list(capture['models'])
    for key in order:
        assert key in TABLES
        t=TABLES[key];cs=columns[key];query=None;started=stamp()
        definitions=[]
        for c in cs:
            name=c['column_name'];required=name in keys[key] or any(f['column']==name and not f['nullable'] for f in fks[key])
            definitions.append(qi(name)+' '+TYPES[c['data_type']]+(' NOT NULL' if required else ''))
        ddl='CREATE TABLE '+qtable(key)+' ('+','.join(definitions)+');'
        if key not in state['loaded']:
            # A previous interrupted table transaction rolls back; there is no truncate/overwrite.
            if key.startswith('ods.'):
                csv=exportdir/(key+'.source.csv')
                con.execute(f"COPY (SELECT * FROM {key}) TO {lit(csv.as_posix())} (FORMAT CSV, HEADER TRUE, NULL '\\N')")
                dest='/tmp/'+state['run_id']+'-'+key+'.csv'
                compose('cp',str(csv),'db:'+dest)
                command='BEGIN;'+ddl+f"\n\\copy {qtable(key)} FROM '{dest}' WITH (FORMAT CSV, HEADER TRUE, NULL '\\N')\nCOMMIT;"
                sql(command,database=DATABASE)
                mode='source_snapshot_copy'
            else:
                inner=translate(capture['models'][key]['select_sql'])
                query='SELECT '+','.join('CAST(r.'+qi(c['column_name'])+' AS '+TYPES[c['data_type']]+') AS '+qi(c['column_name']) for c in cs)+' FROM ('+inner+') r'
                sql('BEGIN;'+ddl+'INSERT INTO '+qtable(key)+' '+query+';COMMIT;',database=DATABASE)
                mode='executed_postgres_transform'
            state['loaded'][key]={'mode':mode,'started_at':started,'finished_at':stamp(),'ddl':ddl,'select_sql':query,
                'sql_sha256':hashlib.sha256((query or ddl).encode()).hexdigest(),'row_count':int(sql('SELECT count(*) FROM '+qtable(key),database=DATABASE))}
            save(statepath,state);print('BUILT',key,state['loaded'][key]['row_count'],flush=True)
        event=state['loaded'][key];manifest['tables'].append({'original':key,'physical':physical(key),**event})
    # Install physical keys only after every referenced table exists. PK + FK roles can overlap.
    for key in order:
        prefix=physical(key).split('.')[1]
        if not state.get('constraints_done'):
            commands=[]
            for c in columns[key]:
                desc=c['description']
                if key=='dws.daily_kpis' and c['column_name']=='active_customers':desc='当日去重买家数，按跨账户买家标识 customer_unique_id 去重；不得跨日直接累加为期间去重人数'
                commands.append('COMMENT ON COLUMN '+qtable(key)+'.'+qi(c['column_name'])+' IS '+lit(desc)+';')
            note=t=TABLES[key]
            comment=t['table_name_zh']+'\n业务域：'+t['business_domain']+'；粒度：'+','.join(keys[key])+'。模拟数据，截止2026-10-26；存在已知业务异常，非生产认证。'
            commands.append('COMMENT ON TABLE '+qtable(key)+' IS '+lit(comment)+';')
            exists=sql("SELECT count(*) FROM pg_constraint WHERE conname="+lit(prefix+'_pkey'),database=DATABASE)
            if exists=='0':commands.append('ALTER TABLE '+qtable(key)+' ADD CONSTRAINT '+qi(prefix+'_pkey')+' PRIMARY KEY ('+','.join(qi(k) for k in keys[key])+');')
            sql('BEGIN;'+''.join(commands)+'COMMIT;',database=DATABASE)
    if not state.get('constraints_done'):
        for key in order:
            for i,fk in enumerate(fks[key]):
                constraint=physical(key).split('.')[1]+'_fk_'+str(i+1)
                if sql('SELECT count(*) FROM pg_constraint WHERE conname='+lit(constraint),database=DATABASE)=='0':
                    sql('ALTER TABLE '+qtable(key)+' ADD CONSTRAINT '+qi(constraint)+' FOREIGN KEY ('+qi(fk['column'])+') REFERENCES '+qtable(fk['target'])+' ('+qi(fk['target_column'])+');',database=DATABASE)
        state['constraints_done']=True;save(statepath,state)
    # Full row-by-key and all-column comparison, not just sample or counts.
    for key in order:
        if not options.resume or key not in state['verified']:
            output=exportdir/(key+'.postgres.csv');export_postgres('SELECT * FROM '+qtable(key),output)
            types={c['column_name']:c['data_type'] for c in columns[key]}
            column_types='{'+','.join(lit(n)+':'+lit(t) for n,t in types.items())+'}'
            relation=con.sql('SELECT * FROM read_csv('+lit(output.as_posix())+", header=true, nullstr='\\N', columns="+column_types+')')
            con.register('pg_compare',relation)
            compare=[];keycols=keys[key]
            for c in columns[key]:
                n=qi(c['column_name']);expr=f'a.{n} IS DISTINCT FROM b.{n}'
                if c['data_type']=='DOUBLE':expr=f'CASE WHEN a.{n} IS NULL OR b.{n} IS NULL THEN a.{n} IS DISTINCT FROM b.{n} ELSE abs(a.{n}-b.{n})>1e-7 END'
                compare.append('count(*) FILTER (WHERE '+expr+')')
            join=' AND '.join('a.'+qi(k)+'=b.'+qi(k) for k in keycols)
            result=con.execute('SELECT count(*),'+','.join(compare)+' FROM '+key+' a FULL JOIN pg_compare b ON '+join).fetchone()
            assert result[0]==TABLES[key]['row_count'] and not any(result[1:]),(key,result)
            state['verified'][key]={'rows':result[0],'columns':len(columns[key]),'column_mismatches':dict(zip([c['column_name'] for c in columns[key]],result[1:])),
                'passed':True,'method':'full outer join by declared key; every field compared','double_absolute_tolerance':1e-7,'postgres_csv_sha256':sha(output),'verified_at':stamp()}
            save(statepath,state);print('MATCH',key,result[0],'all',len(columns[key]),'fields',flush=True)
    # Dedicated source reader has no DDL or data writes. All secrets remain in local/.
    connection_path=LOCAL/'connection.json'
    connection=json.loads(connection_path.read_text('utf-8')) if connection_path.exists() else {'username':'ecommerce_reader','password':secrets.token_urlsafe(28)}
    save(connection_path,connection)
    if sql("SELECT count(*) FROM pg_roles WHERE rolname='ecommerce_reader'")=='0':sql('CREATE ROLE ecommerce_reader LOGIN PASSWORD '+lit(connection['password'])+';')
    sql('GRANT CONNECT ON DATABASE '+DATABASE+' TO ecommerce_reader; GRANT USAGE ON SCHEMA ods,dwd,dws,dim TO ecommerce_reader; GRANT SELECT ON ALL TABLES IN SCHEMA ods,dwd,dws,dim TO ecommerce_reader; ANALYZE;',database=DATABASE)
    creds=json.loads((ROOT/'local/credentials.json').read_text('utf-8'));client=Client();client.login(creds['admin_username'],creds['admin_password'])
    me=client.call('/auth/me');workspace=next((w for w in me['workspaces'] if w['name']=='电商数仓验证'),None)
    if not workspace:workspace=client.call('/workspaces','POST',{'name':'电商数仓验证','description':'11业务域/42表/282字段；2025-01-01至2026-10-26；合成验证数据，业务异常单独标注。'})
    wid=workspace['id'];base=f'/workspaces/{wid}';state['workspace_id']=wid
    source=next((s for s in client.call(base+'/datasources') if s['database']==DATABASE),None)
    if not source:source=client.call(base+'/datasources','POST',{'name':'电商数仓 PostgreSQL（验证快照）','host':'db','port':5432,'database':DATABASE,**connection,'schemas':['ods','dwd','dws','dim']})
    sid=source['id'];state['datasource_id']=sid;save(statepath,state)
    client.call(base+f'/datasources/{sid}/test','POST',{})
    collected=client.call(base+f'/datasources/{sid}/collect','POST',{});assert collected['tables']==42 and collected['columns']==282,collected
    assets={a['namespace']+'.'+a['table_name']:a for a in client.call(base+'/assets')}
    domain_ids={}
    for name,code in DOMAINS.items():
        doc=upsert_document(client,base,'domain',{'code':code,'name':name,'description':name+'业务边界；当前资产为合成验证快照。','status':'published','spec':{'owner':'local-developer','source_revision':catalog['revision']}})
        domain_ids[name]=doc['id']
    metadata_checks=[]
    entities=catalog['entities']
    for key in order:
        t=TABLES[key];a=assets[physical(key)];domain=domain_ids[t['business_domain']]
        client.call(base+'/assets/'+str(a['id']),'PATCH',{'name':t['table_name_zh'],'domain_id':domain})
        assert len(a['columns'])==len(columns[key]);assert a['name'].strip()==t['table_name_zh']
        for src,pg in zip(columns[key],a['columns']):
            expected=src['description']
            if key=='dws.daily_kpis' and src['column_name']=='active_customers':expected='当日去重买家数，按跨账户买家标识 customer_unique_id 去重；不得跨日直接累加为期间去重人数'
            fk_expected=[f for f in fks[key] if f['column']==src['column_name']]
            assert pg['name']==src['column_name'] and pg['comment']==expected and pg['primary_key']==src['is_primary_key']
            assert len(pg['foreign_keys'])==len(fk_expected)
            assert pg['type'].replace('timestamp without time zone','timestamp')==TYPES[src['data_type']]
            for actual,expected_fk in zip(pg['foreign_keys'],fk_expected):
                assert actual['namespace']+'.'+actual['table']==physical(expected_fk['target']) and actual['column']==expected_fk['target_column']
            metadata_checks.append({'original':src['column_id'],'physical':physical(key)+'.'+pg['name'],**pg,'passed':True})
        related=[{**remap_bindings(e),'original_canonical_object':e['canonical_object'],
            'migration_evidence':{'run_id':state['run_id'],'source_sha256':source_hash,
                'current_comparison':state['verified'][e['canonical_object']],
                'legacy_evidence_status':'Original evidence_refs are fixture references, not proof of this migration',
                'current_acceptance_report':'evidence/ecommerce/sql_acceptance.json'}}
            for e in entities if key==e['canonical_object'] or key in e.get('serving_objects',[])]
        model_fks=[{'columns':[f['column']],'reference_schema':physical(f['target']).split('.')[0],'reference_table':physical(f['target']).split('.')[1],'reference_columns':[f['target_column']]} for f in fks[key]]
        grain='每行唯一对应 '+('、'.join(keys[key]))+'；'+t['table_name_zh']
        normalized_fields=[{**pg,'type':TYPES[src['data_type']]} for src,pg in zip(columns[key],a['columns'])]
        spec={'namespace':key.split('.')[0],'grain':grain,'fields':normalized_fields,'primary_keys':keys[key],'foreign_keys':model_fks,
            'datasource_id':sid,'asset_id':a['id'],'source_object':key,'physical_object':physical(key),'owner':t['owner'],
            'history_policy':'Type 1 当前快照；未实现 SCD2' if key.startswith('dim.') else '静态验证快照；不代表已实现增量调度',
            'upstream_objects':[physical(d) for d in capture['models'][key]['dependencies']],
            'ontology_entities':related,'ontology_relationships':remap_bindings([r for r in catalog['relationships'] if any(e['entity_id'] in [r['source_entity'],r['target_entity']] for e in related)]),
            'validation':state['verified'][key],'known_quality_findings':[f['id'] if 'id' in f else str(f)[:80] for f in audit['findings']]}
        doc=upsert_document(client,base,'model',{'code':physical(key).split('.')[1],'name':t['table_name_zh'],'description':a['description'],'domain_id':domain,
            'layer':key.split('.')[0].upper(),'status':'published','spec':spec})
        if key.startswith('dim.'):
            upsert_document(client,base,'dimension',{'code':physical(key).split('.')[1],'name':t['table_name_zh'],'domain_id':domain,'status':'published',
                'description':'当前快照 Type 1；通过物理主键与事实表关联，尚未实现 SCD2。','spec':{'business_key':keys[key][0],'attributes':normalized_fields,'asset_id':a['id'],'history_policy':'Type 1','source_object':key}})
    # Register verified table dependencies; the API re-executes SELECT and compares full target rows.
    for key in order:
        original=capture['models'][key]
        if key.startswith('ods.') and not original['dependencies']:continue
        kind='synthetic_generation' if key.startswith('ods.') else 'warehouse_transform'
        query=translate(original['select_sql'],execute=False) if kind=='synthetic_generation' else state['loaded'][key]['select_sql']
        body={'run_key':state['run_id']+':'+key,'datasource_id':sid,'target_asset_id':assets[physical(key)]['id'],
            'kind':kind,'select_sql':query,'manifest':{'run_id':state['run_id'],'build_event':state['loaded'][key],
                'source_sha256':source_hash,'builder_sha256':capture['builder_sha256'],'source_object':key}}
        run=client.call(base+'/transforms','POST',body)
        state['transforms'][key]=run;save(statepath,state);print('LINEAGE',key,run['status'],flush=True)
    # Import supported single-source metrics. AOV remains an explicit composite draft, never misclassified as atomic AVG.
    for metric in catalog['metrics']:
        if metric['metric_id']=='average_order_value':continue
        a=assets[physical(metric['source_object'])];expression=metric['expression'];match=re.fullmatch(r'(sum|count|avg)\((DISTINCT )?([a-z_]+)\)',expression,re.I);assert match,expression
        agg=('COUNT_DISTINCT' if match[2] else match[1].upper());unit='元' if agg=='SUM' and metric['metric_id'] not in ['inventory_on_hand'] else '件' if metric['metric_id']=='inventory_on_hand' else '分' if metric['metric_id']=='review_score' else '个'
        sem=metric['semantic'];sem=dict(sem)
        if metric['metric_id']=='active_customers':sem['definition']='所选下单期间按 customer_unique_id 去重的买家数；与客户账户数不同，不得把日去重数直接累加。'
        time_binding={'time_field':metric['default_time']}
        if metric['metric_id']=='promotion_orders':
            time_binding={'time_field':'purchased_at','time_asset_id':assets[physical('dwd.orders')]['id'],
                'time_join':[{'left':'dwd.dwd_order_promotions.order_id','right':'dwd.dwd_orders.order_id','cardinality':'N:1'}]}
        upsert_document(client,base,'metric',{'code':metric['metric_id'],'name':metric['metric_name'],'domain_id':domain_ids[metric['business_domain']],
            'status':'published','description':sem['definition']+'；仅作模拟验证，未获业务生产认证。','spec':{'metric_type':'atomic','business_process':metric['business_domain'],
            'aggregation':agg,'unit':unit,'asset_id':a['id'],'measure':match[3],**time_binding,'semantic_contract':remap_bindings(sem),'certification':'DEMO_ONLY'}})
    manifest.update(finished_at=stamp(),database=DATABASE,workspace_id=wid,datasource_id=sid,source_unchanged=sha(dbpath)==source_hash,
        totals={'tables':42,'columns':282,'rows':sum(t['row_count'] for t in TABLES.values()),'physical_primary_keys':42,'physical_foreign_keys':sum(map(len,fks.values()))},
        full_comparisons=state['verified'],metadata_checks=metadata_checks)
    save(EVIDENCE/'migration_manifest.json',manifest);save(EVIDENCE/'table_mapping.json',{k:physical(k) for k in order})
    save(EVIDENCE/'known_source_quality.json',{'source_audit':'审计_20261105/warehouse_model_lineage_audit.json','business_checks':audit['business_checks'],'findings':audit['findings']})
    assert manifest['source_unchanged'];state['finished_at']=stamp();save(statepath,state)
    print('COMPLETE',json.dumps(manifest['totals']), 'workspace',wid,'datasource',sid,flush=True)

if __name__=='__main__':main()
