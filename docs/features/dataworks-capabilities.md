# DataWorks 能力体系与集成范围

名称中的 DataWorks 指数据开发治理能力体系。当前项目是独立实现，未调用 qData 服务；工作区内的 OpenDataWorks、OpenDataAgent 和其他平台研究源码未合入此仓库。

| 能力领域 | OntoDataWorks 对应实现 | 接入状态 |
| --- | --- | --- |
| 数据集成 | PostgreSQL 连接、采集、批量同步、水位与提交回执 | 原生已实现；更多连接器待扩展 |
| 数据开发 | SQL 加工、任务修订、依赖、业务日期 | 原生已实现，限支持的 SQL 和事务边界 |
| 运维调度 | 持久运行、Cron/时区、attempt、取消和 UNKNOWN 对账 | 原生已实现 |
| 数据质量 | 版本化规则、批次门禁、失败回滚与治理待办 | 原生已实现 |
| 数据地图 | 实际表字段、中文释义、结构指纹、来源与血缘 | 原生已实现，血缘范围受解析能力限制 |
| 数仓与指标 | 分层模型、DDL 预检/发布、语义 Metric/Binding | 原生已实现，建模发布和语义发布相互独立 |
| 数据服务 | 参数化服务、契约版本、消费身份与调用限制 | 原生已实现 |
| 本体问数 | 治理、混合召回、意图/澄清、确定性规划与受控执行 | 本地技术验证；生产执行禁用 |
| 阿里云 DataWorks | OpenAPI/SDK、工作空间/任务/实例同步 | 尚未接入 |
| MaxCompute / ODPS | 元数据/SQL/权限/成本预算适配 | 尚未接入 |
| OpenDataWorks MCP | 普通 HTTP MCP 与 odw_* 工具 | 仅调研参考，当前未接入 |
| OpenDataAgent | 独立 FastAPI/Vue/Pi 运行时 | 仅调研参考，当前 Agent 使用本项目 React/FastAPI/CLI 状态机 |

后续添加连接器或 DataWorks 适配时，单独提交 platform/catalog/pipeline 关联 PR：连接身份和凭据归数据源模块；采集产生不可变目录快照；任务实例、权限和成本限制由适配器明确映射；不能将远端任务成功直接视为语义批准。每项适配需真实接口契约和隔离环境验收，参照[模块规范](../maintenance/modules.md)。
