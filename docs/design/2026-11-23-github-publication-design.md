# OntoDataWorks 项目发布与模块维护设计

日期：2026-11-23。状态：已完成；实际结果见[发布报告](../reports/2026-11-23-github-publication.md)。

## 现状与问题

工作区保存平台选型、语义规范、验证记录及 OpenDataWorks/OpenDataAgent 研究副本。实际定制实现位于独立的 `independent-platform` 项目，React 18 / FastAPI / PostgreSQL，应用 1.1.0、Schema 5。最新维护和澄清修复尚未全部提交，不能只导出 Git HEAD。

现有部署脚本依赖原电脑的相邻 Docker 目录；README 混合部署、历史交付与功能描述；未配置面向 GitHub 的模块标签、PR 模板及持续检查。

## 范围与名称

新建独立发布目录 `ontodataworks`，品牌 OntoDataWorks（本体数语）。导入当前工作树业务源码，保留来源基线与第三方许可。原运行目录、数据库、分支及未提交修改保持可继续使用。

对外描述突出 DataWorks 能力体系的数据平台与 ontology semantic data query agent。当前为自研 PostgreSQL 工作台；阿里云 DataWorks OpenAPI/SDK、MaxCompute、OpenDataWorks MCP 均未在此实现中接通，不以研究副本冒充集成能力。

## 模块与接口

采用现有模块化单体，保留 `backend/`、`frontend/` 与 `scripts/` 路径。按 platform、catalog、modeling、pipeline、semantic、agent、consumption、infrastructure 八个维护模块登记源码、测试、责任和 PR scope；共享文件允许多个模块同时命中。`project/modules.json` 是路径与标签的单一维护入口，生成 GitHub labeler 配置并检查漂移。

语义权威为 AssetRevision + CatalogSnapshot，Release/IndexSnapshot/Deployment 为独立控制面。Agent 只引用固定版本，通过受控查询执行；平台 pipeline worker 与 Agent worker 维持各自状态机。本次不重写运行链路或改变数据库表。

## 文档与发布

根 README 负责安装、初始化、启动和验证；功能说明位于 `docs/features/`；架构、模块维护、测试、版本与恢复分别归档。设计与计划成对，`docs/TODO.md` 保存确认后的遗留项。原交付数字仅作历史背景，不转成首次公开版本的验收结论。

GitHub 使用一个仓库与八个模块标签、CODEOWNERS、Issue/PR 模板及最小权限 CI。首次导入和工程化修改分为独立提交；后续使用模块分支和 PR。公开源码不自动变更原有公共许可证授权，保留当前 LICENSE 声明。

## 部署适配与验证

Docker 使用 PATH 或显式路径配置，首次初始化以独立命令建立 v3 基线并顺序执行 v4/v5；不自动导入本机数仓或业务批准。镜像与现有 Compose 项目/卷名保留，避免误切持久卷。提供仅平台启动与完整模型模式，CLI 模型仍遵循现有本地验证范围。

验证包括导入清单、文档链接、模块归属、发布文件检查、前后端既有测试、构建、隔离 Compose 空库初始化，以及 GitHub 推送与 CI 状态。隔离环境不挂载旧凭据/数据，不调用真实模型或外部业务源。未运行的验收明确记录。

首次全量离线检查发现现有澄清测试仍 mock 旧 parse_intent 多需求接口，而实际 worker 已切到 refine_intent 单需求契约；另缺少内存 SQL 政策测试依赖 duckdb。发布范围包括对齐测试夹具和登记开发依赖，保持现有业务行为与约束断言。

## 取舍

保留现有代码路径有利于稳定导入与后续小 PR；共享 main.py、db.py 仍较集中，后续按有测试保护的业务接口逐步拆分。当前保留原许可，后续若选择公共开源许可证，应由所有者明确决定。首次发布不复制原始数据、登录状态、备份、HTTP 验收快照及无关上游仓库。
