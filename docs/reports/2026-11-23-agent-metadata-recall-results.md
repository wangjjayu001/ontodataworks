# 智能问数目录回退与结果来源验收

日期：2026-11-23（Asia/Shanghai）。分支：`fix/agent-query-metadata-provenance`。关联 PR 内容：模型桥接契约一致性、无语义命中后的目录澄清回退、中文检查/结果/来源明细、聚合空集判断。

实施范围及设计边界见[设计](../design/2026-11-23-agent-metadata-recall-results-design.md)与[计划](../plans/2026-11-23-agent-metadata-recall-results-plan.md)。

## 行为变化

- Bridge/backend 在模型目录和每个已登记输出阶段校验同一 `semantic-model/1.2` 版本与 Schema 指纹，并保留可读的契约错误。
- 无唯一发布 Metric/Binding 时，按当前权限在运行固定的 Release/CatalogSnapshot 中召回可读中文表和字段。澄清解析后，模型只能在这些字段 ID 内提出一张表的临时口径；用户分别确认需求和计算口径后，后端才以固定元数据、确定性 SELECT 编译和参数绑定生成预览。
- 查询 Gate 返回中文分项校验、数据空值与排除统计；成功结果包含表、实际字段、固定快照、来源批次，以及语义修订/原文证据入口。历史结果按当前读取权限重新检查。
- 所有临时候选操作都有观测 SQL。源记录集合为空时不将聚合器产生的 `0` 当成查询结果；存在合格记录时保留实际零值。候选从不创建已批准的业务语义资产。

## 验证

`python scripts/run_tests.py` 全部 13 个后端离线套件通过，共 117 项；覆盖澄清状态转换、结构化模型桥接契约、固定元数据字段权限回查、真实 SQLite 语义无命中召回及目录候选、用户确认后参数化 SQL 预览、DuckDB 空结果/实际值区分。

`pnpm --dir frontend test` 的 34 项前端测试通过；`pnpm --dir frontend build` 的 Vite 生产构建通过。`python scripts/check_repository.py` 的链接、模块归属、生成漂移和可发布文件检查通过。Codex bridge PowerShell 语法解析和后端/脚本 `compileall` 通过。

在仅监听 `127.0.0.1:19193` 的隔离 bridge 实际调用 Codex CLI：11 个模型可见，bridge/backend 契约为 `semantic-model/1.2`；澄清和目录候选都通过已登记结构校验。候选只用给定的 `dwd.orders` 表和 `order_id` 字段返回 `COUNT_DISTINCT`；两阶段未调用工具。

FastAPI/SQLite 的智能问数命令回归通过：澄清后的需求目标确认进入口径确认，口径确认生成 `COUNT(DISTINCT ...)` 参数化只读 SQL 预览。语义召回与固定目录回退另外使用真实检索函数和完整隔离语义 Release/Index/Catalog fixture 验证。

未验证真实 PostgreSQL 工作数据源上的完整 Agent HTTP 执行，也未运行本次页面的浏览器交互验收；离线 SQL 执行使用临时 DuckDB。现有 `independent-workbench` 数据卷及其他部署保持未改动。本地测试不替代生产验收。
