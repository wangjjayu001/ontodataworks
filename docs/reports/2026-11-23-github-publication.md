# GitHub 发布验证

日期：2026-11-23（Asia/Shanghai）。项目 OntoDataWorks，应用 1.1.0，Schema 5。

本次从 independent-platform 当前 v1.1 工作树导入最新业务实现，在独立发布目录整理部署工具、文档与模块维护体系。来源见[导入清单](../releases/source-import.json)；设计与实施范围见[设计](../design/2026-11-23-github-publication-design.md)和[计划](../plans/2026-11-23-github-publication-plan.md)。

## 已完成检查

| 检查 | 实际结果 |
| --- | --- |
| 仓库文件、模块归属、生成漂移、JSON/YAML、文档链接 | 通过；八个模块；发布前共 117 个本地文档链接，后续文档增加将重新检查 |
| 后端既有离线回归 | 11 组、105 项通过；Python 3.12.14、临时 SQLite 与内存 DuckDB 1.4.2，无真实模型/数仓 |
| 前端既有测试与构建 | 32 项通过；Vite 5.4.11 构建通过，使用本机捆绑 Node/pnpm；CI 使用 Node 22 / pnpm 10.11.0 |
| Python 与 PowerShell 静态检查 | 后端/工具 compileall、管理脚本解析通过 |
| 全新隔离数据库 | 新 PostgreSQL 16 卷，v3→v4→v5 初始化通过；重复运行通过 |
| 完整容器启动 | 独立 Compose 项目 ontodataworks-publish-check；db/api/worker/pipeline-worker/web 均就绪；仅本机 19091 |
| 真实 HTTP 冒烟 | 15 项通过：Web 品牌与构建资源、Schema 5、匿名拒绝、真实 admin/viewer 登录、空业务空间、CSRF 保护、受保护写入、语义/任务列表、跨空间拒绝、viewer 写拒绝 |

原工作台 19090 与旧持久卷不参与本次检查。临时项目使用独立随机凭据，未导入业务数据或创建语义批准。验收摘要不包含 cookie、CSRF、密码或数据明细。

## 本次处理的问题

首次离线检查发现澄清测试仍 mock 旧 parse_intent 多需求接口，而实际 worker 已采用 refine_intent 单需求契约；已对齐夹具，保留身份、原筛选、时区、比较期和取消优先的断言。内存执行政策测试所需 duckdb 已登记开发依赖。没有据此改变业务执行能力。

## 未验证与实际限制

本次未调用真实模型、未执行外部业务 SQL、未重新执行完整语义召回排名或浏览器交互验收。完整模型模式和已有数仓验收需在各自配置环境重跑。生产隔离、真实权限、高级计算和 DataWorks 适配见 [TODO](../TODO.md)。

## GitHub

公开仓库：[wangjjayu001/ontodataworks](https://github.com/wangjjayu001/ontodataworks)。首次源码和工程化提交已推送；[首次 GitHub CI](https://github.com/wangjjayu001/ontodataworks/actions/runs/45122592269) 的 repository/frontend 两个 job 均通过。已配置 13 个检索 topics、八个 module 标签、CODEOWNERS、自动路径标签和 PR/Issue 模板。后续文档提交也由相同 CI 验证。

源码 Release 说明见[v1.1.0](../releases/v1.1.0.md)，[GitHub Release](https://github.com/wangjjayu001/ontodataworks/releases/tag/v1.1.0)。首次版本提供源码，不附带数据库、模型凭据或原电脑构建产物。

隔离测试容器和网络已移除，测试卷与本机随机配置保留在独立测试范围供复查；未删除旧数据卷。旧 independent-workbench 服务仍运行。
