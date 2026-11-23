# 智能问数目录回退与来源展示实施计划

日期：2026-11-23。设计：[智能问数目录回退与来源展示](../design/2026-11-23-agent-metadata-recall-results-design.md)。

## 实施步骤

1. 固定 Codex CLI bridge/backend 契约版本，逐阶段核对 Schema 散列、请求输出和具体错误。
2. 沿现有澄清、目标确认、检索与口径确认状态机，增加 permission-scoped 固定目录字段回退。模型只可使用提供的候选 ID；后端编译单表临时 SELECT 与参数，不产生批准或正式语义资产。
3. 对临时候选与正式语义资产统一补齐固定字段来源、业务校验项、语义来源、可查看原文证据、快照和批次；中文分层并在历史读取时重验访问权限。
4. 为所有聚合候选生成按同一时间范围和筛选计算的源记录观测 SQL，验证 COUNT_ROWS/COUNT_DISTINCT/SUM/AVG 的无数据语义。
5. 补澄清→候选→口径确认/SQL 预览、契约桥接、空集/零值及 UI 回归；运行全量离线及实际可用的集成检查。
6. 按贡献规范创建 `fix/agent-query-metadata-provenance` 修复分支，以 PR 范围和实测验证写入 Conventional Commit；推送 GitHub 并检查 CI。实际结果写入报告。

## 数据影响与回退

不引入迁移或正式语义写入。执行仍采用原部署固定版本、现时读取权限、受控 SQL 编译、只读 Gate 与一次性授权。如需回退，仅回退此修复提交；既有来源证据、运行与数据卷保持原样。

## 验证报告

检查结果及没有运行的真实数据库/模型/UI 环节见[验收报告](../reports/2026-11-23-agent-metadata-recall-results.md)。
