# 模块划分与 PR 对应关系

| scope / 标签 | 维护职责 | 入口 |
| --- | --- | --- |
| platform / module:platform | 登录、空间、身份、权限、审计与共享装配 | [平台](../../modules/platform/README.md) |
| catalog / module:catalog | 数据源、采集、中文释义、目录与来源 | [目录](../../modules/catalog/README.md) |
| modeling / module:modeling | 业务域、分层建模、DDL 与物理发布 | [建模](../../modules/modeling/README.md) |
| pipeline / module:pipeline | 同步、加工、调度、批次、质量与异常 | [数据工程](../../modules/pipeline/README.md) |
| semantic / module:semantic | 本体、Metric/Binding、治理、索引与召回 | [语义](../../modules/semantic/README.md) |
| agent / module:agent | 意图、澄清、规划、授权、worker 与桥接 | [Agent](../../modules/agent/README.md) |
| consumption / module:consumption | 查询、分析、导出、服务与消费授权 | [消费](../../modules/consumption/README.md) |
| infrastructure / module:infrastructure | 初始化、迁移、Compose、测试与 CI | [工程](../../modules/infrastructure/README.md) |

当前拆分采用业务模块和既有 Python/React 文件边界；没有为了目录形式重写运行系统。`main.py`、`db.py`、`warehouse.py`、`main.jsx` 等共享文件允许多归属，变更时自动提示所有关联 scope。

[project/modules.json](../../project/modules.json) 是唯一归属配置。`python scripts/sync_modules.py` 生成各模块 README 与 `.github/labeler.yml`；`--check` 和 CI 检查漂移。新增模块或业务文件需在该配置登记，检查器拒绝未归属的源码。

示例：修改“澄清后保留筛选”使用 `fix/agent-clarification-filters`，提交 `fix(agent): preserve clarified filters`，涉及 agent worker、语义规划与 query 前端时同时关联 semantic/agent，附离线回归与模型/数据库验收范围。

跨模块 PR 以用户行为为边界，不强制将一个完整修复切成无法独立工作的前端/后端 PR。大型新增功能先有统一设计，再按可独立验证的接口分 PR；不要在多个模块保存同一份权威字段定义。
