"""Maintenance retention, revisions, workspace links, negative inputs and SQL regression."""
from runtime import *
from datetime import datetime
import copy,csv

def main():
    cr=json.loads((ROOT/'local/credentials.json').read_text('utf-8'));c=Client();c.login(cr['admin_username'],cr['admin_password'])
    state=json.loads((ROOT/'local/ecommerce_import/state.json').read_text('utf-8'));wid=state['workspace_id'];b='/workspaces/'+str(wid)
    report={'checked_at':datetime.now().isoformat(),'tests':[]};out=ROOT/'evidence/ecommerce'
    def check(name,detail):report['tests'].append({'name':name,'passed':True,'detail':detail});print('PASS',name,flush=True)
    read=lambda wid:c.call('/workspaces/'+str(wid)+'/assets')
    inventory={ws['id']:read(ws['id']) for ws in c.call('/auth/me')['workspaces']}
    assert len(inventory[wid])==42 and sum(len(a['columns']) for a in inventory[wid])==282
    assert len(inventory[1])==8 and sum(len(a['columns']) for a in inventory[1])==24
    rows=[]
    for space,assets in inventory.items():
        for a in assets:
            assert a['metadata_state']=='complete' and a['governance']['spec']['owner'] and a['domain_id']
            for col in a['columns']:
                m=col['business_metadata'];assert m and any('\u4e00'<=x<='\u9fff' for x in m['chinese_name'])
                assert any('\u4e00'<=x<='\u9fff' for x in m['definition']) and m['quality_rules']
                if col['primary_key'] or col.get('foreign_keys'):assert m['semantic_role']=='identifier'
                if col['name'] in {'email','customer_unique_id'}:assert m['classification']=='sensitive'
                if space==wid:rows.append([a['name'],a['namespace']+'.'+a['table_name'],a['layer'],col['name'],m['chinese_name'],m['definition'],
                    col['type'],col['nullable'],col['primary_key'],';'.join(x['namespace']+'.'+x['table']+'.'+x['column'] for x in col['foreign_keys']),
                    m['semantic_role'],m['classification'],m['unit'],';'.join(m['quality_rules']),';'.join(m['enum_values']),a['governance']['spec']['owner'],a['governance']['spec']['grain']])
    check('all_chinese_definitions',{'ecommerce_tables':42,'ecommerce_fields':282,'fixture_assets':8,'fixture_fields':24,'pk_fk_roles':'identifiers','required_sensitive_fields':'classified'})
    before={a['id']:(a['governance']['spec'],a['fingerprint']) for a in inventory[wid]}
    sid=state['datasource_id'];c.call(b+'/datasources/'+str(sid)+'/collect','POST',{})
    after=read(wid);assert all((a['governance']['spec'],a['fingerprint'])==before[a['id']] for a in after)
    check('recollection_preserves_maintenance',{'tables':42,'definitions_unchanged':True,'fingerprints_unchanged':True})
    target=next(a for a in after if a['table_name']=='dim_customers');aid=target['id'];meta=target['governance']
    original=copy.deepcopy(meta['spec']);payload={'revision':meta['revision'],**copy.deepcopy(original)}
    payload['quality_note']+=' 已验证元数据编辑与重新采集保留。'
    try:
        updated=c.call(b+f'/assets/{aid}/metadata','PUT',payload);assert updated['revision']==meta['revision']+1
        c.call(b+f'/assets/{aid}/metadata','PUT',payload,expected=409)
        c.call(b+f'/datasources/{sid}/collect','POST',{})
        current=next(a for a in read(wid) if a['id']==aid);assert current['governance']['spec']['quality_note']==payload['quality_note']
    finally:
        live=next(a for a in read(wid) if a['id']==aid)
        c.call(b+f'/assets/{aid}/metadata','PUT',{'revision':live['governance']['revision'],**original})
    versions=c.call(b+f'/assets/{aid}/metadata/revisions');assert len(versions)>=3
    check('version_conflict_and_restore',{'stale_update_rejected':True,'changed_definition_survived_collection':True,'original_definition_restored':True,'versions':len(versions)})
    live=next(a for a in read(wid) if a['id']==aid)
    for name,edit in [
        ('missing_field',lambda p:p['columns'].pop()),
        ('duplicate_field',lambda p:p['columns'].append(copy.deepcopy(p['columns'][0]))),
        ('non_chinese_name',lambda p:p['columns'][0].update(chinese_name='customer')),
        ('invalid_classification',lambda p:p['columns'][0].update(classification='unknown'))]:
        bad={'revision':live['governance']['revision'],**copy.deepcopy(original)};edit(bad)
        c.call(b+f'/assets/{aid}/metadata','PUT',bad,expected=400)
    check('invalid_metadata_rejected',{'missing_field':True,'duplicate_field':True,'non_chinese_name':True,'invalid_classification':True})
    viewer_path=ROOT/'local/ecommerce_import/metadata_viewer.json'
    if viewer_path.exists():viewer_credentials=json.loads(viewer_path.read_text('utf-8'))
    else:
        viewer_credentials={'username':'metadata_reader_'+datetime.now().strftime('%m%d%H%M%S'),'password':__import__('secrets').token_urlsafe(24)}
        c.call('/users','POST',{**viewer_credentials,'display_name':'元数据权限验收','role':'viewer','workspace_ids':[wid]})
        viewer_path.write_text(json.dumps(viewer_credentials),encoding='utf-8')
    viewer=Client();viewer.login(**viewer_credentials)
    viewer.call(b+f'/assets/{aid}/metadata','PUT',{'revision':live['governance']['revision'],**original},expected=403)
    viewer.call('/workspaces/1/assets',expected=403)
    check('viewer_cannot_edit_or_cross_workspace',{'write_rejected':True,'other_workspace_rejected':True})
    response=c.call(b+'/queries','POST',{'datasource_id':sid,'sql':'SELECT extract(year from purchased_at) AS year_no,count(*) AS orders FROM dwd.dwd_fct_orders GROUP BY 1 ORDER BY 1'})
    assert [[int(v) for v in row] for row in response['rows']]==[[2025,27575],[2026,22425]]
    check('sql_still_executes',{'query_id':response['id'],'rows':response['rows']})
    with (out/'中文元数据_282字段.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['中文表名','物理表名','分层','英文字段名','中文字段名','业务释义','物理类型','可空','主键','外键目标','语义角色','数据分级','单位','质量规则说明','当前模拟枚举','责任团队','数据粒度']);writer.writerows(rows)
    report.update(passed=True,workspace_assets={str(k):len(v) for k,v in inventory.items()})
    (out/'chinese_metadata_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('COMPLETE',len(report['tests']),'metadata acceptance checks',flush=True)

if __name__=='__main__':main()
