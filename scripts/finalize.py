from runtime import *
from datetime import datetime
import hashlib

integration=json.loads((ROOT/"evidence/integration-acceptance.json").read_text(encoding="utf-8"))
drift=json.loads((ROOT/"evidence/drift-readonly-acceptance.json").read_text(encoding="utf-8"))
persistence=json.loads((ROOT/"evidence/backup-recreation-acceptance.json").read_text(encoding="utf-8"))
browser=json.loads((ROOT/"evidence/browser-acceptance.json").read_text(encoding="utf-8"))
assert integration["all_passed"] and drift["all_passed"] and persistence["all_passed"]
assert not browser["network"]["external_requests"] and not browser["network"]["page_errors"] and not browser["network"]["http_failures"]
old_ids=subprocess.check_output([str(CLI),"ps","-aq","--filter","label=com.docker.compose.project=qdata-local"],encoding="utf-8").split()
old_containers=json.loads(subprocess.check_output([str(CLI),"inspect",*old_ids],encoding="utf-8"))
assert all(c["State"]["Status"]!="running" for c in old_containers)
old_state=[{"name":c["Name"].lstrip("/"),"state":c["State"]["Status"],"previous_restart_policy":c["HostConfig"]["RestartPolicy"]["Name"]} for c in old_containers]
# Retain the old install and its volumes, prevent automatic restart on Docker launch.
subprocess.run([str(CLI),"update","--restart=no",*old_ids],capture_output=True,check=True)
ids=compose("ps","-aq").split()
running=json.loads(subprocess.check_output([str(CLI),"inspect",*ids],encoding="utf-8"))
assert len(running)==3 and all(c["State"].get("Health",{}).get("Status")=="healthy" for c in running)
safe=[{"name":c["Name"].lstrip("/"),"image_id":c["Image"],"state":c["State"]["Status"],"health":c["State"].get("Health",{}).get("Status"),"restart_count":c["RestartCount"],"oom_killed":c["State"]["OOMKilled"],"volumes":[m["Name"] for m in c["Mounts"] if m["Type"]=="volume"]} for c in running]
stamp=datetime.now().astimezone().isoformat()
manifest={"checked_at":stamp,"status":"COMPLETE_INDEPENDENT_PHASE1_LOCAL_ACCEPTANCE","portal":"http://127.0.0.1:19090","source_root":str(ROOT),"original_business_implementation":True,"qdata_runtime_dependency":False,"old_qdata":{"containers":old_state,"restart_policy_now":"no","files_and_volumes_retained":True},"containers":safe,"integration_tests_passed":len(integration["tests"]),"schema_drift_and_readonly_test":True,"browser_acceptance":True,"database_restoration_test":True,"no_ecommerce_import":not (ROOT/"evidence/ecommerce/migration_manifest.json").exists()}
if not manifest["no_ecommerce_import"]:
    manifest["ecommerce_report"]="电商数仓迁移验收报告.md"
(ROOT/"evidence/deployment-state.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
backups="\n".join(f"| {b['database']} | {Path(b['file']).name} | {b['bytes']} | {b['verified_count']} |" for b in persistence["backups"])
report=f"""# 独立数据工作台一期交付与验收

完成时间：{stamp}（本机时区 Asia/Shanghai）。当前入口：**http://127.0.0.1:19090**。

## 结论

本次交付为从空目录独立编写的 React 前端、FastAPI 后端和 PostgreSQL 平台库，不再以 qData 为底座。原 qData 的 10 个容器已停止，自动重启策略设为 no，原文件、数据库和数据卷保留。新平台在旧服务全部停止后，仍通过真实浏览器登录、数据预览与 SQL 查询验收。

源码位置：`{ROOT.as_posix()}`。本机账号 admin，密码在 `local/本机登录信息.txt`。启动使用本目录 start-workbench.cmd；停止使用 stop-workbench.cmd。

## 已交付的一期能力

| 模块 | 实际实现 |
| --- | --- |
| 登录与空间 | 独立账号库、强散列密码、HttpOnly 会话、CSRF、成员角色、工作空间隔离 |
| 业务域 | 中文名称、编码、父级域、业务说明、版本维护与引用保护 |
| 数仓建模 | ODS / DWD / DWS / DIM / ADS；分层前缀、粒度、字段与主键校验；DDL 生成、受控建空表 |
| 维度 | 业务主键与属性维护；业务域归属 |
| 指标 | 原子指标业务过程、聚合与单位；真实表字段校验；派生指标引用原子指标、时间窗口与维度 |
| 数据源 | PostgreSQL 连接配置与测试，密码加密保存，API 不返回密码 |
| 元数据/地图 | 从数据库采集表、字段、注释、结构指纹；维护业务归属；抽屉预览实时前 100 行 |
| SQL | SELECT/WITH 单语句、只读事务、10 秒超时、返回 1,000 行上限及截断提示、查询历史 |
| 血缘证据 | 成功执行的表到查询结果来源；SQL 散列、结构指纹、采集时间与物理结构校验时间 |
| 全局界面 | 原创登录页与 CSS 示意图、工作空间导航、菜单、按钮、表格、表单、弹窗、抽屉、桌面与窄屏布局 |

设计使用数据开发工作台的通用布局，自行实现，未复用 qData / DataWorks 的标识、图片和页面组件。没有在业务页面中植入公司电话、邮箱、远程激活、商用授权弹窗或厂商水印。

## 真实验收

- 最终接口与数据库集成验收 {len(integration['tests'])} 项通过，记录于 integration-acceptance.json。涵盖登录、CSRF、中文 CRUD、并发版本冲突、引用保护、元数据、预览、DDL、指标引用、查询和角色/空间隔离。首次执行另验证模型实际生成空表，幂等重跑保留该表，因此最终项数不将建表重复记为新执行。
- 浏览器通过真实登录表单，新增/编辑/删除中文业务文档，数据库回读 revision=2、中文说明匹配；抽屉 100 行实时预览、SQL A/B 分组各 730 条均通过。9 个功能菜单及概览、SQL、血缘页面检查无页面错误，已测请求无 HTTP 失败、无外部请求。
- 390px 窄屏检查无横向溢出，侧栏最终宽度 52px；桌面 1440px 检查无横向溢出。相关截图在 evidence 目录。
- 修改物理表结构后查询返回冲突并阻止生成来源证据；恢复结构后可查询。通过数据库函数尝试写入被只读事务阻止，实际未插入记录。返回上限及截断提示通过。
- 额外验证 CTE 与同名物理表的作用域区分，避免将真实来源误当成临时别名忽略。
- 三个新容器已实际强制重建，命名数据卷保留；重建后真实登录、建模文档、指标、资产、1,460 条验收读数均保留。之后 {persistence['observation_seconds']} 秒的短时观察中 4 次采样均健康、零重启、无 OOM。
- 两个数据库实际恢复到独立测试库，表数量和关键行数通过，临时恢复库已清理。恢复测试不声称逐行比对整个库。

| 数据库 | 已恢复验证的备份 | 字节 | 核查记录数 |
| --- | --- | --- | --- |
{backups}

备份中包含平台账户散列和已加密数据源凭据，应按私有数据保管。还需备份 local/credentials.json 中的连接加密密钥；单有数据库备份无法恢复已保存的连接密码。

## 验收数据与业务数据

专用数据库 `warehouse_lab`，Schema `validation`：日期维度 730 行（2025-01-01—2026-12-31）、读数明细 1,460 行、每日汇总 730 行、逻辑模型建成空表 0 行。数据为明确标识的非电商工程验收样本，非迁入电商数据。空间中的业务域、维度和指标同样标为验收专用。

当前真实已采集 4 张表。平台元数据与验收业务表分库保存，当前复用一个 PostgreSQL 实例；后续接电商仓库应新建独立数据源和工作空间。原 DuckDB/电商模拟数据没有迁移、覆盖或删除。

## 独立性和许可

source-and-dependency-inventory.json 记录新写源码散列及实际第三方包清单。新平台没有导入 qData 源码、JAR、初始化 SQL、图片或运行服务，原始业务表结构与 qData 无关。旧 qData 许可保留在旧环境，不通过删改其 LICENSE/NOTICE 取得本项目的独立性。

通用开源依赖（React、FastAPI、PostgreSQL、Lucide、SQLAlchemy、SQLGlot 等）仍适用各自许可。实际 npm/Python 包许可文本保留于 third-party-licenses，汇总见 THIRD_PARTY_NOTICES.md；该清单不宣称容器操作系统 SBOM 全覆盖。原创业务代码归本工作空间维护，尚未为对外发布选定公共许可证。

浏览器入口通过同源 CSP 限制脚本、连接和图片；已测路径无外联。此结论不表示操作系统、Docker 或所有第三方库经过完全审计，也不代表配置数据源后不发生用户预期的数据库连接。

## 明确边界及下一步

本次是已确认的一期本机闭环，不是 DataWorks 全量替代。当前仅 PostgreSQL 连接器；调度执行引擎、字段级和 ETL 全链路血缘、任意指标自动计算、智能问数及语义层尚未实现。业务文档 JSON 已有类型校验，但尚未建设完整模型审批与发布流水线。

当前是单机本地功能验收，尚无生产高可用、容量和长时间稳定性结论。下一阶段先按已约定接入少量电商样表，核查模型/指标/资产关系与真实 SQL 来源，再扩展全量；智能问数和语义改造后续分期。

版本升级须增加数据库迁移；不能把 create_all 当作已有表结构的迁移器。启动/停止入口已实际执行；完整开发和运维步骤见 README.md。
"""
(ROOT/"交付验收报告.md").write_text(report,encoding="utf-8")
parent=ROOT.parent
(parent/"独立平台入口.md").write_text("# 当前使用独立数据工作台\n\n入口：http://127.0.0.1:19090\n\n项目与源码：independent-platform。启动：independent-platform/start-workbench.cmd；停止：independent-platform/stop-workbench.cmd。\n\n本机账号见 independent-platform/local/本机登录信息.txt，交付结果见 independent-platform/交付验收报告.md。\n\n原 qData 环境已停止并禁止容器自动重启，文件和数据卷保留。旧 qdata-start.cmd 是恢复旧环境的入口，请勿与新平台混淆。\n",encoding="utf-8")
old_guide=parent/"部署使用说明.md"
if old_guide.exists():
    content=old_guide.read_text(encoding="utf-8")
    marker="当前入口已切换到独立平台"
    if marker not in content:old_guide.write_text("> **当前入口已切换到独立平台**：http://127.0.0.1:19090 。原 qData 服务已停止，以下为历史 qData 操作说明。当前启动入口为 start-independent.cmd，完整说明见 independent-platform/README.md。\n\n"+content,encoding="utf-8")
old_status=parent/"部署实施_20261110/deployment-state.json"
if old_status.exists():
    old=json.loads(old_status.read_text(encoding="utf-8"));old.update({"status":"SUPERSEDED_BY_INDEPENDENT_PLATFORM_OLD_SERVICES_STOPPED","platform_started":False,"superseded_at":stamp,"current_portal":manifest["portal"],"current_project":str(ROOT),"old_data_preserved":True,"next_action":"Use independent-platform; old deployment retained only for reference or explicit restoration"});old_status.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"status":manifest["status"],"url":manifest["portal"],"old_qdata_stopped":len(old_state),"new_services_healthy":len(safe),"report":str(ROOT/"交付验收报告.md")},ensure_ascii=False))
