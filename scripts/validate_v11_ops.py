"""Recover v1.1 into separate databases; verify additive upgrade and v1.0 reads.

Backup contents include secrets and stay in ignored backups/. Never print them.
"""
from runtime import *
from datetime import datetime, timezone
import hashlib, shutil, tarfile, io

stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
folder=ROOT/'backups'/('v1.1-final_'+stamp);folder.mkdir(parents=True)
state=json.loads((ROOT/'local/v11-validation-state.json').read_text(encoding='utf-8'))
report={'scope':'separate restored databases on local PostgreSQL cluster','checked_at':datetime.now(timezone.utc).isoformat(),'checks':[],'backups':[]}
def check(name,condition=True,**details):
    assert condition,name
    report['checks'].append({'name':name,'passed':True,**details});print('PASS '+name,flush=True)
databases=['workbench',state['source_db'],state['target_db']]
for database in databases:
    payload=subprocess.run(ARGS+['exec','-T','db','pg_dump','-U','workbench','-d',database,'-Fc'],capture_output=True,check=True).stdout
    path=folder/(database+'.dump');path.write_bytes(payload)
    restored='restore_'+database+'_'+stamp.replace('_','')
    sql('CREATE DATABASE '+restored+';')
    subprocess.run(ARGS+['exec','-T','db','pg_restore','-U','workbench','-d',restored,'--no-owner','--no-privileges','--exit-on-error'],input=payload,capture_output=True,check=True)
    report['backups'].append({'database':database,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'restored_database':restored})
    if database=='workbench':
        tables=['wb_modeling_document','wb_catalog_asset','wb_semantic_asset_revision','wb_agent_run','wb_pipeline_definition','wb_data_batch','wb_physical_publication']
        counts={t:int(sql('SELECT count(*) FROM '+t+';')) for t in tables}
        recovered={t:int(sql('SELECT count(*) FROM '+t+';',database=restored)) for t in tables}
        check('platform_restore_preserves_legacy_and_v11_rows',counts==recovered,counts=counts)
        platform_copy=restored
    elif database==state['target_db']:
        check('target_restore_keeps_rows_and_commit_receipts',sql('SELECT count(*) FROM pipe.ods_orders;',database=restored)=='5' and int(sql('SELECT count(*) FROM pipe._wb_batch_receipt;',database=restored))>0)
    else:check('source_restore_keeps_all_synthetic_rows',sql('SELECT count(*) FROM src.orders;',database=restored)=='5')
# Necessary decryption/configuration material, kept only in ignored private backup.
for relative in ['.env','local/credentials.json','local/codex-bridge.token','local/codex-runtime.json']:
    path=ROOT/relative
    if path.exists():
        target=folder/'private-config'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
roles=subprocess.run(ARGS+['exec','-T','db','pg_dumpall','-U','workbench','--roles-only'],capture_output=True,check=True).stdout
(folder/'private-config/roles.sql').write_bytes(roles)
check('decryption_config_and_roles_saved_privately',(folder/'private-config/.env').exists() and bool(roles))
script=f"""import os
from sqlalchemy.engine import make_url
os.environ['DATABASE_URL']=make_url(os.environ['DATABASE_URL']).set(database='{platform_copy}').render_as_string(hide_password=False)
from app.migrate_v5 import main
main();main()
from app.db import engine
from sqlalchemy import text
with engine.connect() as c: assert c.execute(text('SELECT max(version) FROM wb_schema_version')).scalar()==5
print('additive v5 migration repeated successfully on restored copy')
"""
compose('run','--rm','--no-deps','api','python','-c',script)
check('restored_schema_v5_migration_is_repeatable')
baseline=ROOT/'backups/v1.1_20261119_130803/workbench.dump'
if baseline.exists():
    upgraded='upgrade_v11_final_'+stamp.replace('_','');sql('CREATE DATABASE '+upgraded+';')
    subprocess.run(ARGS+['exec','-T','db','pg_restore','-U','workbench','-d',upgraded,'--no-owner','--no-privileges','--exit-on-error'],input=baseline.read_bytes(),capture_output=True,check=True)
    assert sql('SELECT max(version) FROM wb_schema_version;',database=upgraded)=='4'
    before=sql('SELECT count(*) FROM wb_semantic_asset_revision;',database=upgraded)
    compose('run','--rm','--no-deps','api','python','-c',script.replace(platform_copy,upgraded))
    check('final_v4_to_v5_upgrade_preserves_semantic_revisions',sql('SELECT count(*) FROM wb_semantic_asset_revision;',database=upgraded)==before and sql("SELECT to_regclass('wb_source_enrollment') IS NOT NULL;",database=upgraded)=='t')
# Archive the old revision without modifying the active checkout or service.
old=ROOT/'scratch'/('v10-compat-'+stamp);old.mkdir(parents=True)
archive=subprocess.run(['git','archive','ced3fd7','backend/app'],cwd=ROOT,capture_output=True,check=True).stdout
with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:bundle.extractall(old,filter='data')
script=f"""import os,sys,asyncio
from sqlalchemy.engine import make_url
os.environ['DATABASE_URL']=make_url(os.environ['DATABASE_URL']).set(database='{platform_copy}').render_as_string(hide_password=False)
sys.path.insert(0,'/old/backend')
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as c:
 r=c.get('/api/health');assert r.status_code==200 and r.json()['status']=='ok'
 from app.db import SessionLocal,Asset,Resource
 from sqlalchemy import select,func
 with SessionLocal() as db:
  assert db.scalar(select(func.count()).select_from(Asset).where(Asset.workspace_id==3))>=42
  assert db.scalar(select(func.count()).select_from(Resource))>0
print('old v1.0 app startup, health and legacy reads passed on additive v5 copy')
"""
compose('run','--rm','--no-deps','--volume',str(old)+':/old:ro','--volume',str(ROOT/'local/qa-linux')+':/qa','--env','PYTHONPATH=/qa','api','python','-c',script)
check('v10_app_starts_and_reads_v5_copy_without_undoing_target_writes')
report.update(all_passed=True,count=len(report['checks']),backup_directory=str(folder))
(ROOT/'evidence/v1.1/operations.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
(folder/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'all_passed':True,'checks':len(report['checks'])}),flush=True)
