# OntoDataWorks · 本体数语

**DataWorks 能力体系的数据平台 × 本体语义取数 Agent。**

集成数据接入、数仓建模、SQL 开发、调度运行、质量治理、目录血缘与数据服务，通过本体语义资产、指标物理映射、版本化召回和受控 NL2SQL，把业务口径连接到可追溯的查询执行。

[功能与应用介绍](docs/features/README.md) · [本体语义与 Agent](docs/features/semantic-agent.md) · [架构](docs/architecture/README.md) · [模块与 PR](docs/maintenance/modules.md) · [English](README.en.md)

本 README 专注源码部署与验证。当前基于 React 18、FastAPI、PostgreSQL；DataWorks 指能力体系参考，阿里云 DataWorks OpenAPI/SDK、MaxCompute 接口尚未接入，详见[能力映射](docs/features/dataworks-capabilities.md)。

## 1. 环境要求

- Windows 10/11、PowerShell、Docker Desktop Linux 引擎及 Docker Compose 2.24.4+。
- Python 3.12；Node.js 22 LTS、pnpm 10.11.0。
- 完整模型模式另需已安装、已登录并能独立使用的 Codex CLI；仅平台模式无需模型认证。

默认入口仅监听 `127.0.0.1:19090`，API/数据库不映射宿主端口。现有版本是本地技术验证工作台；真实生产执行与隔离条件见[配置及运行边界](docs/deployment/configuration.md)。

## 2. 获取源码与准备配置

```powershell
git clone https://github.com/wangjjayu001/ontodataworks.git
cd ontodataworks
python -m venv .venv
.\.venv\Scripts\python.exe scripts/prepare_local.py
corepack enable
corepack prepare pnpm@10.11.0 --activate
pnpm --dir frontend install --frozen-lockfile --config.node-linker=hoisted
pnpm --dir frontend build
```

配置生成器只使用 Python 标准库，创建独立随机密码、Fernet 密钥、桥接令牌与被 Git 忽略的 `.env` / `local/`。已有配置不覆盖。管理员账号为 `admin`，密码在本机 `local/本机登录信息.txt` 查看。

Docker 应在 PATH；使用自定义安装目录时，在当前终端设置：

```powershell
$env:ONTODATAWORKS_DOCKER = 'D:\Docker\resources\bin\docker.exe'
```

## 3. 首次初始化数据库

先启动 Docker Desktop 的 Linux 引擎，再执行：

```powershell
.\Manage-Workbench.ps1 -Action Initialize
```

该步骤构建 API 镜像并在数据库就绪后建立基础表，再顺序执行显式 v4/v5 增量迁移。不会导入原电脑的电商数仓、自动批准语义资产或开启生产 Agent。已有部署升级前先[备份](docs/deployment/operations.md)。

## 4. 启动

仅使用数据平台、建模、语义维护等功能：

```powershell
.\Manage-Workbench.ps1 -Action Start -WithoutModel
.\Manage-Workbench.ps1 -Action Verify -WithoutModel
```

完整模型模式：先确认 `codex --version` 与 CLI 认证可用，然后登记桥接所用解释器并启动。

使用 CC Switch 时，启动桥接会读取当前选中的 Codex 服务商、模型与推理强度。以后在 CC Switch 切换配置，只需执行 `Manage-Workbench.ps1 -Action Restart`；重启会重新加载，细节见[CC Switch 配置说明](scripts/CODEX-RUNTIME.md#cc-switch-配置与重启)。

```powershell
.\scripts\Start-CodexBridge.ps1 -PythonPath "$PWD\.venv\Scripts\python.exe"
.\Manage-Workbench.ps1 -Action Start
.\Manage-Workbench.ps1 -Action Verify
```

打开 [http://127.0.0.1:19090](http://127.0.0.1:19090)。仅平台模式不会验证模型或受理真实问数的完整模型链路；完整模式会从 API 和 Agent worker 分别验证实时模型目录。目录就绪不等于一次取数成功。

首次工作空间为空；请按[功能手册](docs/features/README.md)登记数据源、采集目录、维护模型和语义资产。已有冻结 metadata seed 仅用于专项开发回归，不自动导入默认空间。

## 5. 日常管理

```powershell
.\Manage-Workbench.ps1 -Action Status
.\Manage-Workbench.ps1 -Action Build
.\Manage-Workbench.ps1 -Action Restart -WithoutModel
.\Manage-Workbench.ps1 -Action Stop
```

前端修改后重新执行 `pnpm --dir frontend build`；后端修改后重建并更新 api、worker、pipeline-worker。停止保留卷。Compose 项目名沿用 `independent-workbench`，持久卷为 `independent-workbench_platform-postgres`；品牌调整不会自动迁移或重命名现有数据卷。并行部署需要独立项目名与端口，详见[运维](docs/deployment/operations.md)。

## 6. 开发验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/check_repository.py
.\.venv\Scripts\python.exe scripts/run_tests.py
pnpm --dir frontend test
pnpm --dir frontend build
```

CI 自动执行上述离线检查。真实 PostgreSQL、cookie/CSRF、模型调用与取数须在隔离环境单独验收；详见[测试说明](docs/maintenance/testing.md)。本次实际发布检查见[发布报告](docs/reports/2026-11-23-github-publication.md)。

## 7. 目录与贡献

```text
backend/app/            平台、目录、建模、数据工程、语义和 Agent 业务代码
frontend/src/           React 页面及交互模型
infra/                 Nginx 与数据库结构参考
scripts/               初始化、模型桥接、离线测试与专项验收
project/modules.json   八个模块的文件归属及 GitHub 标签
modules/               各模块维护入口
docs/features/         功能与应用介绍
docs/deployment/       配置、升级、备份与恢复
docs/maintenance/      PR、文件管理与测试规范
docs/design/           中大型变更设计
docs/plans/            与设计配对的实施计划
docs/reports/          验证报告
docs/TODO.md           唯一已确认待办入口
third-party-licenses/  第三方许可原文
```

从 [CONTRIBUTING.md](CONTRIBUTING.md) 开始，一个 PR 对应明确模块和验收结果。当前源码公开可见，原创代码沿用 [LICENSE](LICENSE) 的授权说明，尚未选择公共开源许可证；第三方组件保留[各自许可](THIRD_PARTY_NOTICES.md)。
