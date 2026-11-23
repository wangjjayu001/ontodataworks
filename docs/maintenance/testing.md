# 验证规范与测试入口

## 离线检查

```powershell
python -m pip install -r requirements-dev.txt
python scripts/check_repository.py
python scripts/run_tests.py
pnpm --dir frontend test
pnpm --dir frontend build
```

`run_tests.py` 逐文件启动隔离进程，使用现有 `scripts/test_*.py` unittest，并设置临时 SQLite 与测试密钥，不连接本机业务数据库或真实模型。独立进程可避免 ORM 注册与测试环境状态串扰。tests 位于 scripts 是保留的既有路径；新测试沿用 `test_<module>_<topic>.py`，无需多份测试实现。

前端使用 Node 内置 test runner，覆盖来源原文字节、语义维护、发布范围、查询值和结果投影；Vite build 检查实际组件可构建。

## 真实集成检查

`scripts/validate_*.py` 是针对特定已配置空间和数据的在线验收，不属于通用空库 CI。运行前检查脚本的 workspace/data assumptions；多份脚本会写入合成夹具、运行、证据和结果文件。不能对生产空间直接批量执行。数据导入工具还需要通过环境变量提供公开测试数据位置。

- 平台/鉴权：真实 HTTP 登录、cookie/CSRF、跨空间拒绝、API 就绪。
- pipeline/quality：真实 PostgreSQL 同步、事务提交、质量回滚、UNKNOWN 对账与批次依赖。
- semantic：新修订、核验、独立审核、索引/部署 CAS、引用及权限回查。
- agent：真实模型解析、逐项澄清、固定语义、QueryFrame、单次授权、只读结果、取消及终态持久化。
- UI：涉及交互变化时实际浏览器检查目标页面及不同宽度。

每份报告注明环境、数据分类、是否真实模型、是否真实数仓、通过/失败/跳过与未验证项。不能把模型目录、健康检查、离线编译或历史 acceptance 文件当成当前全链路通过。
