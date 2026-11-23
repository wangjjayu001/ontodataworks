# 部署基础设施

compose.yml 是运行入口；nginx.conf 为同源本机 Web/API 代理；schema-v*.sql 为历史结构参考，实际建表和升级由 ORM 及 migrate_v4/migrate_v5 执行。

新环境使用[部署 README](../README.md)，升级和持久化说明见[运维](../docs/deployment/operations.md)。当前数据库 Schema 5 不通过手工按顺序导入这些参考 SQL 来重建。
