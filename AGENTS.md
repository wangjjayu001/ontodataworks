# OntoDataWorks 仓库约定

- 发布源码基于 React 18 / FastAPI / PostgreSQL，实际入口见 README。保留模块化单体和既有 backend/frontend/scripts 路径。
- 按 project/modules.json 和 modules/ 的八个模块维护；改路径后运行 scripts/sync_modules.py，提交生成的 labeler 与模块说明。
- 中大型变更先写 docs/design/YYYY-MM-DD-topic-design.md，再写同 topic 的 docs/plans/YYYY-MM-DD-topic-plan.md。
- 用户的部署 README 与功能手册分开维护；能力声明必须与代码和验收证据一致。
- 语义执行只以 AssetRevision + CatalogSnapshot 为权威；技术快照不等于生产批准；模型只提供结构化建议，不能直接决定 SQL/权限。
- API/Schema/运行时/迁移/部署变化同 PR 更新契约、文档和必要测试；跨层改动尽可能补隔离环境全流程验收。
- 运行 scripts/check_repository.py、scripts/run_tests.py 和适用前端测试/构建；未运行的真实数据库、模型及 UI 验收明确说明。
- 不导入 local、.env、凭据、备份、数据或原始 HTTP 记录。不得操作原电脑其它部署的容器或卷。
- docs/TODO.md 是唯一已确认遗留项入口；完成后移到已完成并链接结果。
- 不创建未被用户要求的子代理或聊天，不自动改变原创和第三方许可。
