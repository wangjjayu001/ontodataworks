# 配置与运行范围

| 配置 | 用途 | 来源 |
| --- | --- | --- |
| DATABASE_PASSWORD | 平台 PostgreSQL 密码 | prepare_local.py 本地随机生成 |
| ENCRYPTION_KEY | 加密数据源凭据的 Fernet 密钥 | 本地生成；恢复数据库时必须同时恢复 |
| INITIAL_ADMIN_PASSWORD | 空库首次 admin 密码 | 本地生成；已有用户不因修改此值而重设 |
| ONTODATAWORKS_DOCKER | 自定义 Docker CLI 完整路径 | 可选进程环境；默认从 PATH 查找 |
| ONTODATAWORKS_DOCKER_DESKTOP | 需要自动启动时的 Docker Desktop 可执行文件 | 可选；默认标准安装路径 |
| CODEX_BRIDGE_URL | 容器访问宿主模型桥接地址 | Compose 默认 host.docker.internal:19191 |
| CODEX_BRIDGE_TOKEN_FILE | 容器内 token 文件路径 | local/codex-bridge.token 只读挂载 |

`.env.example` 只说明格式；推荐运行生成器，不直接使用占位值。local 目录包含本机管理员信息、桥接解释器/PID/Token，不进入 Git。

## 两种已知运行环境

`-WithoutModel` 用于未配置 Codex 的数据平台环境，启动 API、数据库、两个 worker 与 Web，但不启动/验证模型桥接。模型相关请求会按现有失败路径报告不可达。

完整模式使用 Windows Python 3.12+ 启动桥接，默认只监听 loopback 19191。API/Agent worker 需要在当前 Docker Desktop 上确认 `host.docker.internal` 可达；不同网络环境不应直接扩大监听地址。CLI 版本升级后重新运行模型协议和取消验证。

当前 bridge 仅接受 public/synthetic 材料，禁用生产执行。feature 开关、工具拒绝和临时 cwd 不是 OS/网络隔离证明。模型只解析问题；实际数据源凭据和查询结果明细不发送给模型。独立生产服务身份、隔离 runner、真实行列政策和生产验收列于 [TODO](../TODO.md)。

## 资源与持久化

compose.yml 固定镜像 digest、容器内存上限、私有网络、健康检查和只读 API 根文件系统。数据库/API 不暴露宿主端口，Web 仅本机 19090。服务无需 Redis；不要混用研究副本的 MySQL/Redis/Pi 部署说明。

保留旧 Compose 名 `independent-workbench` 和对应卷是为了显式兼容现有运行数据。新项目品牌、GitHub 仓库名和数据库项目名分别维护。
