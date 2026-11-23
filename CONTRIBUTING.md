# 贡献与 PR 规范

## 找到维护模块

先查[模块表](docs/maintenance/modules.md)和 [project/modules.json](project/modules.json)。每个模块有固定 scope、源码路径、测试及接口边界。共享文件可以同时属于多个模块；GitHub 会按路径自动打多个 `module:*` 标签。

## 提交改动

1. 从最新 `main` 创建 `feat/<module>-<topic>`、`fix/<module>-<topic>` 或 `docs/<topic>` 分支。
2. 一个 PR 解决一个可验证问题。涉及架构、Schema、公开 API、部署行为或跨模块契约的中大型改动，先写 `docs/design/YYYY-MM-DD-<topic>-design.md`，再写同主题 plan。
3. 在同一个 PR 更新对应模块、契约、文档和必要测试。不得把研究方案写成已实现能力。
4. 运行[适用验证](docs/maintenance/testing.md)，PR 说明列明触发条件、前后行为、模块、验证结果和数据/部署影响。
5. 提交使用 Conventional Commits：`fix(agent): preserve clarified filters`、`feat(semantic): validate binding revision`。纯文档使用 `docs(...)`。
6. 已确认但未修复的问题写入 [docs/TODO.md](docs/TODO.md)，注明日期、现状、下一步及来源。

## 评审和合并

PR 模板要求作者说明真实验证范围；离线测试不能替代真实数据库/模型验收。共享查询、权限、迁移和语义 Schema 变更需要关联模块共同评审。CODEOWNERS 当前由仓库所有者承担，增加协作者后可按模块配置不同负责人。

优先 squash merge，使一个 PR 成为一个可回退提交；删除已合并分支。`main` 保留发布基线，后续修复通过 PR。当前 CI 不自动部署或发布镜像。

## 文件和数据

执行[文件管理规范](docs/maintenance/repository.md)。凭据、`.env`、local、备份、日志、数据库、原始 HTTP 快照、node_modules 和 dist 不进入提交。公开样例必须标明 public/synthetic、来源及局限。

新增公共授权协议须由有权许可的所有者明确选择；本仓库目前保留原 [LICENSE](LICENSE)，第三方许可不随项目品牌变更。
