# 升级、备份与恢复

## 升级

1. 记录当前 Git commit、Compose 项目/镜像、Schema 版本、语义部署指针，排空长任务。
2. 备份 PostgreSQL、`.env` 与 local 中的加密密钥/服务配置，存放在访问受限的备份位置。
3. 拉取目标提交，安装锁定前端依赖并构建，重建 API/worker 镜像。
4. 需要 Schema 升级时运行 Initialize，按 v3→v4→v5 顺序执行现有显式迁移；不删除数据卷，不自动导入业务数据。
5. 启动后验证登录、空间隔离、目标功能、任务状态和需要的模型链路，记录本次结果。

## 备份与恢复原则

平台数据库、业务数据源、密钥是不同对象。使用 PostgreSQL 的 pg_dump/pg_restore 对应版本工具，并通过受控二进制文件输出保存备份；在 Windows PowerShell 5.1 中避免把自定义格式二进制 dump 当文本重定向。

恢复在独立项目/卷演练，核对 Schema、用户、语义修订、任务、批次和连接解密。只恢复平台数据库无法撤销已经写入外部数据库的数据；应用 Git revert、语义指针回滚和业务数据恢复分别处理。

普通停机使用 `Manage-Workbench.ps1 -Action Stop`；普通排障不执行 `docker compose down -v`。

## 并行隔离环境

直接使用 Docker CLI 时可通过 `-p <独立项目名>` 覆盖 Compose 项目名，同时通过 override **完整替换** web 端口以及需要的资源配置。Compose 2.24.4+ 支持 `!override`，示例：

```yaml
services:
  web:
    ports: !override ["127.0.0.1:19091:80"]
```

必须核对最终合并配置、实际卷和端口再启动；不能只改项目名后占用原入口。隔离环境的 local/.env 也须独立，不挂载旧部署凭据。

## 排障

- Docker 未就绪：先核对 Linux 引擎，使用 ONTODATAWORKS_DOCKER 指定实际 CLI。
- Web 为空：确认 frontend/dist 已构建，Nginx bind mount 指向当前仓库。
- 表缺失：确认首次 Initialize 完成，不把 API health 当成完整 Schema 验收。
- 模型不可达：先检查 CLI 自身、桥接本机日志、API 和 worker 的实时目录；仅平台模式使用 WithoutModel。
- 语义无结果/执行被阻断：检查空间、固定修订、部署范围、时间/单位/币种/维度/权限，不能通过手改成功状态绕过。
