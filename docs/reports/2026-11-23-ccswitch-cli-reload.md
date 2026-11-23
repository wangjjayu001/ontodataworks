# CC Switch 配置重载验收

日期：2026-11-23（Asia/Shanghai）。设计：[启动配置快照与重启](../design/2026-11-23-ccswitch-cli-reload-design.md)，计划：[实施步骤](../plans/2026-11-23-ccswitch-cli-reload-plan.md)。

## 修改与结果

原 Restart 复用 bridge，不保证加载当前选择；CLI 合并后的推理配置也可能与 CC Switch 中保存的 provider 配置不同。现在每次 Restart 都先停止工作台、重启 bridge、只读读取最新的 CC Switch Codex provider，再启动平台。当前 provider 的模型、推理强度、路由和鉴权环境明确传给 CLI，config/read 再核对生效配置；默认模型与推理选项跟随本次配置。

CC Switch 数据库与个人 Codex 配置不被改写。自定义 API 凭据只保留在桥接及 CLI 子进程环境，不写入参数、公开报告或 HTTP 响应；官方登录由 CLI 自行使用原认证。未安装 CC Switch 的环境继续采用原 Codex 配置路径。无数据库 Schema 或数据迁移。

## 实际检查

- 14 个 Python 离线套件共 130 项通过，其中新增 13 项 CC Switch 配置回归：设备本机选择、最新配置重读、旧快照稳定、凭据不外泄、空凭据/歧义失败、官方登录、代理端口验证、生效模型/路由校验和目录默认值。
- 仓库检查、模块标签生成、PowerShell 管理/启动脚本语法、Python compileall 与 diff 空白检查通过。前端本轮没有变更，现有 PR 的 34 项前端测试和构建由 CI 再验证。
- 隔离 loopback bridge 的真实 Codex CLI 读取当前 CC Switch provider，config/read 验证模型、推理强度、provider 路由与环境鉴权模式一致；目录返回 11 个实际模型。当前配置模型为 `gpt-6.1-sol`、推理强度 `high`。
- 真实结构化 `explain` 调用成功，使用上述模型与推理配置，工具事件为空。该输入为合成连接检查，不包含业务记录。
- 两次显式 ReloadConfig 检查 PID 均更换，目录重新成功返回；隔离 bridge 收尾停止。
- 本机用户指定的工作台已同步本轮 CLI 与管理脚本。发现其 API/worker 原镜像仍采用未版本化的旧模型协议，已从当前源码重建这两个镜像并通过 Restart 更新为 `semantic-model/1.2`；API 和 worker 读取到同一配置快照，工作台和数据库健康检查通过。重启后从 API 容器发起真实 `explain` 调用成功，回执模型、推理强度与配置来源一致，工具事件为空。

此次未验证切换任意第三方服务商或代理的真实网络行为；代理模式使用隔离配置单测，官方登录兼容分支使用单测。没有运行真实业务问数或业务 SQL；连接成功不代替该范围验收。其他部署和业务数据卷未更改。
