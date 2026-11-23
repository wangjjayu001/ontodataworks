# 已确认待办

## 待完成

- **2026-11-23 · DataWorks 适配**：当前仅实现对应能力体系，未接入阿里云 DataWorks OpenAPI/SDK、MaxCompute 或 OpenDataWorks MCP。后续按连接器身份、权限、元数据和实例状态契约设计独立适配及真实验收。来源：[能力映射](features/dataworks-capabilities.md)。
- **2026-11-23 · 生产模型与数据执行**：现有 bridge 使用本地开发者 CLI，production Agent 禁用。需要独立服务身份、OS/网络隔离、真实行列政策、资源预算和受控数据端到端验收。来源：[配置范围](deployment/configuration.md)、[发布设计](design/2026-11-23-github-publication-design.md)。
- **2026-11-23 · 高级计算与检索**：同比/环比双期规划不支持；Vector 关闭。分别设计计算语义/黄金结果及可重建向量索引后再开放。来源：[语义与 Agent](features/semantic-agent.md)。
- **2026-11-23 · 公共许可证选择**：源码沿用原 LICENSE，未选择公共开源再分发授权。由有权许可的所有者决定授权后单独变更，不覆盖第三方声明。来源：[LICENSE](../LICENSE)。
- **2026-11-23 · 共享入口进一步拆分**：main.py、db.py、main.jsx 仍承担多个业务域。已有模块路径归属和 CI；后续有实际变更需求时先补行为测试，再按接口做小 PR，避免一次性大搬迁。来源：[模块维护](maintenance/modules.md)。

## 已完成

- **2026-11-23 · 发布工程化**：独立导入当前工作树，拆分部署/功能文档，建立模块登记、PR/Issue 模板和检查流程。实际验证及 GitHub 状态见[发布报告](reports/2026-11-23-github-publication.md)。
