# 已确认待办

## 待完成

- **2026-11-23 · DataWorks 适配**：当前仅实现对应能力体系，未接入阿里云 DataWorks OpenAPI/SDK、MaxCompute 或 OpenDataWorks MCP。后续按连接器身份、权限、元数据和实例状态契约设计独立适配及真实验收。来源：[能力映射](features/dataworks-capabilities.md)。
- **2026-11-23 · 生产模型与数据执行**：现有 bridge 使用本地开发者 CLI，production Agent 禁用。需要独立服务身份、OS/网络隔离、真实行列政策、资源预算和受控数据端到端验收。来源：[配置范围](deployment/configuration.md)、[发布设计](design/2026-11-23-github-publication-design.md)。
- **2026-11-23 · 高级计算与检索**：单表目录口径已支持多期分别计算及固定基期变化；跨表临时计算、复杂派生公式尚未支持。Vector 关闭，需独立设计可重建向量索引与验收。来源：[语义与 Agent](features/semantic-agent.md)。
- **2026-11-23 · 公共许可证选择**：源码沿用原 LICENSE，未选择公共开源再分发授权。由有权许可的所有者决定授权后单独变更，不覆盖第三方声明。来源：[LICENSE](../LICENSE)。
- **2026-11-23 · 共享入口进一步拆分**：main.py、db.py、main.jsx 仍承担多个业务域。已有模块路径归属和 CI；后续有实际变更需求时先补行为测试，再按接口做小 PR，避免一次性大搬迁。来源：[模块维护](maintenance/modules.md)。

## 已完成

- **2026-11-23 · 无语义资产取数闭环与取消时区设置**：独立元数据作业、口径与字段澄清、真实可用性核对、单表多期 SQL、中文缺口和重查；真实源库五月/六月评价记录数及 ROI 缺费用分支通过，原运行已恢复。153 项后端、41 项前端及实测范围见[验收报告](reports/2026-11-23-query-metadata-completion.md)。

- **2026-11-23 · 问数识别进度及多时间段确认**：真实连接/识别/校验阶段、耗时与取消入口；按独立日期区间确认、修改、交换基期，保留后续澄清约束。139 项后端、39 项前端检查和实际模型/浏览器交互范围见[验收报告](reports/2026-11-23-agent-progress-time.md)。后续单表多期计算进展见下方目录闭环验收。
- **2026-11-23 · CC Switch 模型配置重载**：Restart 明确重新创建 bridge，读取本机当前 Codex provider 的模型、推理强度和凭据；CLI 实际配置及调用回执核对通过，API/worker 镜像同步为新协议。配置回归和真实合成连接测试见[验收报告](reports/2026-11-23-ccswitch-cli-reload.md)。
- **2026-11-23 · 智能问数模型契约、目录回退与来源展示**：统一 Codex CLI bridge/backend `semantic-model/1.2` 及阶段 Schema 指纹校验；无发布语义命中时按权限回查固定目录并支持澄清、用户确认与单表参数化只读预览；问数结果中文结构化展示检查、明细和可展开来源证据；补齐聚合空集与零值区分。设计与离线回归见[实现设计](design/2026-11-23-agent-metadata-recall-results-design.md)、[实施计划](plans/2026-11-23-agent-metadata-recall-results-plan.md)和[验收报告](reports/2026-11-23-agent-metadata-recall-results.md)。
- **2026-11-23 · 发布工程化**：独立导入当前工作树，拆分部署/功能文档，建立模块登记、PR/Issue 模板和检查流程。实际验证及 GitHub 状态见[发布报告](reports/2026-11-23-github-publication.md)。
