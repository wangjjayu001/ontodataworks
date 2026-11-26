# CC Switch 模型配置重载设计

日期：2026-11-23。状态：已实现并验证。范围：问数 Agent 的本机 CLI 启动与服务重启；实际检查见[验收报告](../reports/2026-11-23-ccswitch-cli-reload.md)。

现有管理入口在 Restart 时复用运行中的 bridge，只验证模型目录。CC Switch 选中的 Codex 服务商、模型及推理强度也可能和桌面 CLI 当前合并配置不同，目录成功不能证明本轮实际配置已生效。

桥接启动时只读 CC Switch 的本机选择和 provider 数据，生成本进程使用的配置；不修改 CC Switch 数据库或个人 Codex 配置。自定义服务商的密钥仅通过子进程环境传递，CLI 命令参数、HTTP 诊断与日志不含密钥。配置错误明确失败，不能回退为另一服务商。未安装 CC Switch 的机器继续使用本机 Codex 配置；官方登录仍由 CLI 使用已有认证。

每个 CLI session 显式使用该启动快照的服务商、默认模型和推理强度。自定义 API 服务商使用临时 CLI home，防止个人配置或旧 OAuth 覆盖当前路由。启动后通过 config/read 验证实际模型、provider 和推理配置；模型目录提供已脱敏配置来源，默认选择对齐该目录中实际存在的模型。

Restart 先有序停止工作台，再明确重启 bridge 并读取 CC Switch 最新配置，最后启动工作台并从 API/worker 检查模型目录。普通 Start 保留复用语义；用户通过 Restart 应用配置变化。进程中进行的模型调用使用同一配置快照，切换不会改变正在执行步骤的服务商。

测试覆盖当前 provider 选择、配置/凭据隔离、变更后重读、未知或缺失配置失败、真实 CLI 生效配置与结构化调用。无 Schema 或数据迁移。实现与实测见[计划](../plans/2026-11-23-ccswitch-cli-reload-plan.md)。
