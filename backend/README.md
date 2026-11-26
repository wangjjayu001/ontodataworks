# 后端开发

FastAPI / SQLAlchemy / PostgreSQL，应用 1.2.0、Schema 5。API、Agent worker、pipeline worker 复用 app 目录，通过 compose.yml 独立启动。

模块定位见[模块表](../docs/maintenance/modules.md)，权威与引用见[架构](../docs/architecture/README.md)。首次数据库初始化使用 `python -m app.bootstrap`（在已配置环境的 api 容器中），不在正常请求里隐式执行升级。

依赖为 requirements.txt，开发测试依赖见根 requirements-dev.txt。根目录运行 `python scripts/run_tests.py`，在线专项验收另见[测试说明](../docs/maintenance/testing.md)。

数据地图只读表/字段血缘接口见[功能与 API 契约](../docs/features/data-map-lineage.md)。
