"""Export maintained catalog/ontology evidence and non-destructive database backups."""
from runtime import *
from import_ecommerce import SOURCE,LOCAL,EVIDENCE,save,sha
import csv,re
from datetime import datetime

def main():
    state=json.loads((LOCAL/'state.json').read_text('utf-8'))
    manifest=json.loads((EVIDENCE/'migration_manifest.json').read_text('utf-8'))
    acceptance=json.loads((EVIDENCE/'sql_acceptance.json').read_text('utf-8'));assert acceptance['passed']
    creds=json.loads((ROOT/'local/credentials.json').read_text('utf-8'));c=Client();c.login(creds['admin_username'],creds['admin_password'])
    b=f"/workspaces/{state['workspace_id']}";assets={a['id']:a for a in c.call(b+'/assets')}
    by_table={a['namespace']+'.'+a['table_name']:a for a in assets.values()}
    domains={d['id']:d['name'] for d in c.call(b+'/documents/domain')}
    models=c.call(b+'/documents/model');entities={};relationships={}
    def binding(path):
        schema,table,column=path.split('.');a=by_table[schema+'.'+table]
        return next(field for field in a['columns'] if field['name']==column)
    for model in models:
        for entity in model['spec']['ontology_entities']:
            assert entity['canonical_object'] in by_table
            for prop in entity['properties']:assert binding(prop['physical_binding'])['comment']==prop['label']
            canonical=by_table[entity['canonical_object']]
            assert set(entity['key_fields'])=={col['name'] for col in canonical['columns'] if col['primary_key']}
            assert entity['migration_evidence']['source_sha256']==state['source_sha256']
            assert entity['migration_evidence']['current_comparison']['passed']
            entities[entity['entity_id']]=entity
        for relationship in model['spec']['ontology_relationships']:
            for join in relationship['join_conditions']:
                left,right=binding(join['left']),binding(join['right']);assert left['type']==right['type']
            relationships[relationship['relationship_id']]=relationship
    assert len(entities)==13
    ontology={'checked_at':datetime.now().isoformat(),'passed':True,'entities':list(entities.values()),'relationships':list(relationships.values()),
        'summary':{'entities':len(entities),'properties':sum(len(e['properties']) for e in entities.values()),'relationships':len(relationships),
            'all_physical_bindings_exist':True,'key_definitions_match_database':True,'join_types_match':True},
        'scope':'Binding/key/type checks only; inherited business semantics and anomaly gates remain DEMO_ONLY, no semantic recall engine claimed.'}
    save(EVIDENCE/'ontology_binding_acceptance.json',ontology)
    with (EVIDENCE/'metadata_282.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['业务域','分层','中文表名','实际物理表','字段名','PostgreSQL类型','中文含义','可空','主键','外键目标','核验'])
        for a in sorted(assets.values(),key=lambda a:(domains[a['domain_id']],a['namespace'],a['table_name'])):
            for col in a['columns']:
                writer.writerow([domains[a['domain_id']],a['layer'],a['name'],a['namespace']+'.'+a['table_name'],col['name'],col['type'],col['comment'],
                    '是' if col['nullable'] else '否','是' if col['primary_key'] else '否','; '.join(x['namespace']+'.'+x['table']+'.'+x['column'] for x in col['foreign_keys']),'通过'])
    save(EVIDENCE/'platform_catalog.json',{'assets':list(assets.values()),'models':models,'domains':domains})
    # Backup credentials stay under local/, never in public evidence or reports.
    backup_version=re.sub(r'[^0-9]','',acceptance['finished_at'])
    backupdir=ROOT/'backups'/('ecommerce-'+state['run_id'])
    # Keep one immutable archive per accepted data version; a later validation gets fresh backups.
    previous_backup=EVIDENCE/'backup_manifest.json'
    if previous_backup.exists():
        previous=json.loads(previous_backup.read_text('utf-8'))
        if previous.get('acceptance_finished_at')==acceptance['finished_at']:
            backupdir=Path(previous['backups'][0]['path']).parent
        elif previous.get('acceptance_finished_at') is not None:
            backupdir=backupdir/backup_version
    backupdir.mkdir(parents=True,exist_ok=True)
    backups=[]
    for database in ['workbench',state['database']]:
        path=backupdir/(database+'.dump')
        if not path.exists():
            with path.open('wb') as output:
                result=subprocess.run(ARGS+['exec','-T','db','pg_dump','-U','workbench','-d',database,'-Fc'],stdout=output,stderr=subprocess.PIPE)
            if result.returncode:raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))
        with path.open('rb') as stream:
            result=subprocess.run(ARGS+['exec','-T','db','pg_restore','--list'],stdin=stream,capture_output=True)
        assert result.returncode==0
        entries=result.stdout.decode('utf-8');table_data=sum(' TABLE DATA ' in line for line in entries.splitlines())
        if database==state['database']:assert table_data==42
        backups.append({'database':database,'path':str(path),'bytes':path.stat().st_size,'sha256':sha(path),'archive_list_readable':True,'table_data_entries':table_data})
        print('BACKUP',database,path.stat().st_size,'bytes',table_data,'data entries',flush=True)
    save(EVIDENCE/'backup_manifest.json',{'checked_at':datetime.now().isoformat(),'acceptance_finished_at':acceptance['finished_at'],'backups':backups,'restore_exercise_performed':False})
    # Transparent scope: current source code hash is not asserted as the original executor binary.
    manifest['script_hash_scope']='Current import/finalization invocation source; exact executed SQL text is preserved per build event. Historical executable code hash is not reconstructed.'
    save(EVIDENCE/'migration_manifest.json',manifest)
    deployment_path=ROOT/'evidence/deployment-state.json'
    deployment=json.loads(deployment_path.read_text('utf-8'))
    deployment.update(ecommerce_checked_at=datetime.now().isoformat(),no_ecommerce_import=False,
        ecommerce={'workspace_id':state['workspace_id'],'database':state['database'],**manifest['totals'],
            'sql_acceptance_checks':len(acceptance['tests']),'source_quality_gates_triggered':sum(not x['gate_passed'] for x in acceptance['source_quality']),
            'warehouse_lineage_edges':38,'declared_generator_edges':10,'report':'电商数仓迁移验收报告.md'})
    save(deployment_path,deployment)
    domain_rows=[]
    for domain in domains.values():
        group=[a for a in assets.values() if domains[a['domain_id']]==domain]
        rows=sum(x['row_count'] for x in manifest['tables'] if x['physical'] in {a['namespace']+'.'+a['table_name'] for a in group})
        domain_rows.append(f'| {domain} | {len(group)} | {rows:,} |')
    checks=acceptance['source_quality'];anomalies=[x for x in checks if not x['gate_passed']]
    quality_labels={'product_listed_before_order':'交易早于商品上架','seller_onboarded_before_order':'交易早于商家入驻','promotion_applied_within_dates':'活动期外促销关联',
        'inventory_reserved_not_over_on_hand':'库存预留超过在库量','pending_orders_no_captured_payment_review':'pending订单存在已捕获支付','negative_payment_amount':'正向支付金额为负',
        'session_conversion_agrees_purchase_event':'会话转化标记与购买事件矛盾','landing_is_first_event':'会话首事件不是landing'}
    report=f'''# 电商数仓自动迁移与验收报告

验收时间：{datetime.now().isoformat(timespec='seconds')}。本次写入独立数据工作台 http://127.0.0.1:19090 的「电商数仓验证」空间（ID {state['workspace_id']}）。旧 qData 环境未写入。

## 已落地的数据库与目录

- 运行库：PostgreSQL 数据库 `{state['database']}`，容器 `independent-workbench-db-1`，命名卷 `independent-workbench_platform-postgres`，由本机 D 盘 Docker Desktop 保存；不是浏览器 JSON 演示表。
- 源快照：`{SOURCE/'ecommerce_validation.duckdb'}`，只读保留，SHA256 `{state['source_sha256']}`。
- 共 42 表、282 字段、11 业务域、12,825,397 行（跨层合计，同一业务记录会在多层出现）；ODS14、DWD21、DWS3、DIM4。
- 交易与库存日期覆盖 2025-01-01—2026-10-26，共 664 个自然日；主数据注册日期可早于 2025。不是完整 2026 年或实时数据。
- 新物理名称采用 schema + 分层前缀，例如 `dwd.dwd_fct_orders`；完整原表映射见 `evidence/ecommerce/table_mapping.json`。
- 自动化入口：`Import-Ecommerce.ps1`；实现脚本 `scripts/import_ecommerce.py`；SQL 验收脚本 `scripts/validate_ecommerce.py`。本机凭据保存在 Git 忽略的 `local/`，报告不包含密码。

## 建模、字段与元数据校验

42 表主键非空且唯一，42 条明确的业务外键已在 PostgreSQL 真正创建并验证；主键与外键允许重叠，移除了原“以 _id 结尾即外键”的错误推断。282 字段的顺序、类型、中文含义、可空性、PK/FK 和关联目标均已核对，并写入数据库 COMMENT、平台采集元数据和逻辑模型。

已纠正日汇总 `active_customers` 的中文定义：按 `customer_unique_id` 去重的买家数，不是账户 ID；期间买家数须重新去重，不能累加每日去重数。预计/实际送达差值的正值表示提前送达，保留了准确释义。

13 个本体实体、{ontology['summary']['properties']} 个实体属性、{len(relationships)} 个关系已映射到新物理表，属性绑定存在、实体键一致、关联字段类型一致；当前全行对账证据与原 fixture 证据分开标注。见 `ontology_binding_acceptance.json`。这不是智能问数/召回引擎已上线的证明。

平台「数据地图」可按业务域/分层筛选，中文表名可点开抽屉，查看字段、PK/FK 与按物理主键排序的前 100 行实时数据。完整字段导出为 `evidence/ecommerce/metadata_282.csv`。

| 业务域 | 表数 | 跨层行数 |
|---|---:|---:|
{chr(10).join(domain_rows)}

## 加工与血缘的真实范围

ODS14 从原快照批量 COPY 导入；其余28表在 PostgreSQL 实际执行加工 SELECT 重建。42表全部按主键 FULL JOIN、逐字段与 DuckDB 对账，零差异；DOUBLE 绝对容差 1e-7，其他类型精确比较。SQL 方言差异（日期小时边界、ARG_MAX、ROUND）已显式适配，重建结果与源一致。

数仓加工38条表级依赖由真实SQL解析；平台 API 另行执行28个 SELECT，再逐行逐字段比较目标表，全部一致。保留运行键、SQL SHA256、目标/输入结构指纹、时间与源快照哈希。另有10条ODS合成生成依赖，仅标记为当前生成器的静态声明；本次未重跑生成器，也未反补2026-10-26的历史执行证明。

本期为表级血缘；未提供字段表达式血缘、SCD2、增量调度或生产认证。原33个DOUBLE字段（含27个金额字段）保持原类型以保证迁移等价，不能当作精确财务存储已改造。

## SQL 模块与前端验收

{len(acceptance['tests'])}项自动化检查通过，实际请求平台 `/queries`：42表行数/键、42条关联完整性、12项指标（含复合客单价）、年度/月度窗口、库存指定快照、防重复累加、17项源业务规则、漂移阻断、只读、越权与错误血缘拒绝。11项可表达的原子指标已登记；复合客单价以独立SQL验证，未冒充单字段原子指标。

浏览器前端实测：登录、空间切换、中文字段抽屉、100行实时预览、加工血缘、模拟声明、SQL编辑器运行。年度订单数返回2025年27,575、2026年22,425；当前期间去重买家与金额由真实表返回。截图及 `ui_acceptance.json` 在同目录。

旧工程验收空间的元数据已刷新；只读事务阻止函数副作用写入、结构漂移阻断和1,000行返回上限回归通过，见 `evidence/drift-readonly-acceptance.json`。

## 保留并标注的业务异常

迁移和查询结果一致，不等于源业务质量全部正确。17项业务规则中有8项触发，全部按原值保留：

| 规则 | 异常数量 |
|---|---:|
{chr(10).join('| '+quality_labels.get(x['name'].replace('source_quality:',''),x['name'])+' | '+str(x['observed'][0][0])+' |' for x in anomalies)}

其中负支付、活动期外使用、上架/入驻前交易和转化矛盾需要修复生成器；库存超预留、pending已支付、landing顺序还需先确定业务规则。它们不能用于宣称生产数据质量合格。4个DIM仍是Type1当前快照，标准DWD有14张直接投影，未声称完成历史维度或复杂清洗。

## 中文元数据维护补充

默认「本地开发空间」的8条连接资产是4张工程验收表的两套连接。电商42张表的直达地址为 http://127.0.0.1:19090/?workspace=3#assets ，URL指定空间优先于浏览器旧选择。页面显示当前空间、表/字段维护覆盖率，并在验收空间提供切换电商空间的按钮。

42张电商表的282字段与工程8条资产的24字段均有中文字段名、业务释义、角色、数据分级、单位、规则说明和当前模拟枚举；表级维护责任团队、中文粒度、更新策略、质量限制。新增独立维护及版本表（schema v3），不会因重新采集物理结构而丢失。维护者可在表抽屉「维护中文元数据」编辑，版本冲突阻止覆盖；已通过浏览器实际编辑、刷新回读和接口验收。规则描述不等于规则已经全部执行通过。

最新中文字段导出为 `evidence/ecommerce/中文元数据_282字段.csv`；详情见 `chinese_metadata_maintenance.json`、`chinese_metadata_acceptance.json` 和 `chinese_metadata_ui_acceptance.json`。中文元数据齐全与生产业务质量认证分开。

## 复跑和备份

```powershell
# 在本项目目录；默认再次校验全量实表，不覆盖源 DuckDB
.\\Import-Ecommerce.ps1
# 只重新测试当前平台
.\\Import-Ecommerce.ps1 -ValidateOnly
# 当前中断批次续跑，可复用已完成全行对账；日常复核不使用此选项
.\\Import-Ecommerce.ps1 -Resume
```

已存在表按本次运行状态复用；无归属状态的同名数据库会拒绝写入；不会 DROP/TRUNCATE 旧业务表。源数据或真实目标内容改变时校验会失败，须人工决定新版本迁移策略，不会静默覆盖。

本次平台库和电商库备份在 `{backupdir}`，自定义备份格式可读，电商备份含42个TABLE DATA条目。SHA256/大小见 `backup_manifest.json`。本次未进行电商全库恢复演练；恢复数据源连接还需保留 `.env` 中加密密钥与 `local/` 本机凭据。

物理建模/本体的迁移来源仍为原 `ontology/source_spec.json` 和源元数据，迁移脚本负责名称映射；重新迁移可能更新同编码模型。中文目录业务定义则以独立版本化维护文档为准，`maintain_chinese_metadata.py` 初始化已有文档时尊重后续维护修改；不会将目录释义反向改写源本体JSON或物理结构。两者边界与同步方式见README，不将两个编辑入口视为自动双向同步。
'''
    (ROOT/'电商数仓迁移验收报告.md').write_text(report,encoding='utf-8')
    print('COMPLETE catalog + ontology + report',ontology['summary'],flush=True)

if __name__=='__main__':main()
