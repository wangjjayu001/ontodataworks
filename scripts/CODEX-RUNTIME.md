# 本地 Codex 模型运行

本模块真实调用本机 `codex app-server`，不是静态模型列表或规则模拟。当前是 **Windows 开发者身份、本地公开/合成数据验证**，不是完成 OS 隔离的生产模型运行器。运行能力在 `/models` 与每次推理回执中明确标记 `production_enabled=false` / `isolation_accepted_for_production=false`。

## 启停与容器接入

使用已安装 Python 3.12+ 与当前已经能够使用的 Codex CLI。在项目根目录执行：

```powershell
./scripts/Start-CodexBridge.ps1 -PythonPath 'python'
./scripts/Stop-CodexBridge.ps1
```

脚本以隐藏进程运行，默认只监听 `127.0.0.1:19191`。首次创建随机 token，保存在 Git 忽略的 `local/codex-bridge.token`；日志/PID 同样在 local 中。不复制/读取个人 auth.json，不打印 token。当前桌面 CLI 使用已有开发者认证，未来生产部署需要独立运行身份及受支持的凭据注入，不能把这个身份自动视为服务账号。

## CC Switch 配置与重启

安装 CC Switch 时，桥接启动会只读当前用户 `.cc-switch/settings.json` 的 `currentProviderCodex` 与 `cc-switch.db` 对应 Codex provider；没有本机选择时使用数据库唯一的 `is_current` 项。读取当前服务商、模型、推理强度和凭据，不修改 CC Switch 或个人 Codex 配置。自定义 CC Switch 目录可用 `ONTODATAWORKS_CC_SWITCH_HOME` 指定；指定位置无效或现有配置错误时明确失败，不能切换到另一服务商。

自定义 API 服务商通过子进程专用环境变量传递密钥，使用临时 CLI home 和明确的 provider 覆盖；参数、日志和 HTTP 响应不包含密钥或认证内容。官方无 API key 的模式继续由 CLI 使用原有登录。启用了 CC Switch 实时代理接管时，必须验证个人实时配置指向登记的本地代理端口才可使用。

在 CC Switch 切换服务商、模型或凭据后，执行 `./Manage-Workbench.ps1 -Action Restart`。该入口先有序停止工作台，再重启 bridge 读取最新配置，然后启动平台；单独重载模型可用 `./scripts/Start-CodexBridge.ps1 -ReloadConfig`。普通 `Start` 仍复用现有 bridge。bridge 在每次 CLI session 初始化后核对 config/read 的实际 provider、模型和推理强度；模型目录默认项跟随本次加载的有效模型。模型不在实际目录中或推理强度不受支持时明确报错。

`/health`、`/models` 和调用回执中的 `runtime_configuration` 仅提供配置来源、服务商名称、配置模型、推理强度与加载时间。配置在一次 bridge 生命周期内固定；重新加载会刷新目录版本，已有页面可点击“刷新目录”重新选择。没有安装 CC Switch 的机器继续读取 Codex 原有配置。

Docker Desktop 实测已有 API 容器可通过 `host.docker.internal:19191` 到达宿主 loopback，不需要监听 `0.0.0.0`。容器配置需要：

```text
CODEX_BRIDGE_URL=http://host.docker.internal:19191
CODEX_BRIDGE_TOKEN_FILE=/run/secrets/codex_bridge_token
```

将 `local/codex-bridge.token` 只读挂到上述 token 路径。文件路径可以出现在部署清单，token 内容不能进入 Git、日志或前端。后端使用 urllib 并关闭内部请求的代理转发；不把授权头发给外部代理。

## 后端入口

`backend/app/model_runtime.py` 提供：

- `list_models()`：实时 app-server `model/list`，跟随分页，返回 models、catalog_version、updated_at、cli_version 和运行范围。
- `parse_intent(text, context, model_selection, request_id, data_classification='synthetic')`：真实结构化意图；返回 `output={requirements,clarification_needed}` 和真实模型/轮次/usage 回执。每个 requirement 包含 metric_terms、dimension_terms、start/end_exclusive、timezone、time_event、filters、negative_terms、clarifications、depends_on_requirement_indexes。
- `refine_intent(text, context, model_selection, request_id)`：澄清只细化一个已有需求，返回 `output.intent`，不产生新的 requirements 数组。
- `invoke('intent'|'clarification'|'metadata_candidate'|'explain', input_payload, registered_schema, selection, request_id, data_classification)`：四个登记阶段，不开放任意 Schema/任意 RPC。桥接与后端核对 `semantic-model/1.2` 和各阶段 Schema hash。
- `cancel(request_id)`：登记模型取消；应用仍需等待真实 interrupted/failed/completed 终态并单独取消自己的查询/工具作业。

目录选择只接受实际 model/list 中的模型、supportedReasoningEfforts 和 serviceTiers；目录 hash 过期需重选，不静默换模型。`outputSchema` 只约束最终助手消息，后端再次验证小型闭合 Schema，不把输出或文字“通过”视为业务核验。

## 运行约束与测试

桥接只开放带服务 token 的 `/health /models /invoke /cancel`；拒绝浏览器 Origin、未知参数、过大请求及原始 command/fs/config/account RPC。每次模型步骤使用新临时 cwd 和 ephemeral thread，禁用已知 shell/exec/apps/plugins/browser/computer/image/multi-agent 等 feature；没有 dynamicTools，发现任何 server tool/approval 请求或工具 item 即拒绝/结束步骤。stderr 仅留内存，HTTP 错误不返回宿主路径或原始响应。

上述 feature 和回调控制不等于已经证明 OS/网络隔离。服务仅接受 synthetic/public 分类，生产/机密分类失败关闭；调用方必须核实材料类别，不能仅把真实敏感材料改标签为 synthetic。当前工作空间真实查询使用本地合成数仓，**不向模型发送结果明细、结果数值、数据源凭据或用户文件**。模型仅解析问句；语义回源、规划、授权和执行在后端进行。

```powershell
python scripts/validate_model_runtime.py
```

真实验收回执位于 `evidence/v1.0/model-runtime-acceptance.json`，覆盖实时目录、未认证、原始执行端点缺失、不支持配置、目录过期、生产分类阻断、输出契约、地区/状态意图以及实际 turn 取消。本次不证明语义排名、SQL 结果或生产隔离，后者由各自验收承担。CLI 升级后需重新核验协议与已知 feature，不能复用旧版本结论。
