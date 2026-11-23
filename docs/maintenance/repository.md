# 项目文件管理

## 放置规则

| 文件 | 固定位置 | 维护规则 |
| --- | --- | --- |
| 源码 | backend/app、frontend/src | 按模块职责和既有入口维护 |
| 语义机器契约 | backend/app/contracts | 唯一 Schema 源；运行 DTO 不混入权威资产 |
| 物理部署 | compose.yml、infra、backend/Dockerfile | 修改需更新部署 README |
| 工具与测试 | scripts、frontend/tests | test 为离线，validate 为需说明条件的专项验收 |
| 产品说明 | docs/features | 说明用户价值、操作流程和实际范围 |
| 操作部署 | README、docs/deployment | 说明可执行步骤、配置、升级和恢复 |
| 长期维护 | docs/maintenance、modules、project | 归属、PR、测试与生成规则 |
| 设计/计划 | docs/design、docs/plans | 日期和 topic 成对，先设计后计划 |
| 验证报告 | docs/reports | 日期、环境、检查、结果与未验证项 |
| 历史归档 | docs/archive | 仅历史背景，不作为当前使用入口 |
| 待办 | docs/TODO.md | 唯一确认后续入口，不散落多份 TODO |
| 本机秘密与运行物 | local、.env、backups、evidence | Git 忽略，不作公开交付物 |

文件名优先稳定英文路径；中文放标题和正文。相对链接用于仓库文档，禁止把原电脑盘符写成可部署路径。历史资料、第三方许可和来源散列保持可定位。新增公开报告用合成身份与摘要，不复制登录态、请求头或未经审查的结果明细。

## 自动检查

`scripts/check_repository.py` 检查当前 Git 跟踪/未忽略文件中的文档链接、模块归属、生成漂移、配置语法、敏感路径与常见私钥/Token 格式。新增秘密应使用 local 文件或受支持的部署 secret；检查器不替代人工核对公开材料。

CI 使用固定的 Python/Node 主版本和前端锁文件；运行数据、完整数仓和模型凭据不参与 CI。标签生成工作流只运行可信 base 上的 action，不检出 PR 代码。
