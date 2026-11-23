# 工具入口

| 工具 | 用途 |
| --- | --- |
| prepare_local.py | 仅生成本地随机配置，不打印秘密 |
| sync_modules.py | 从 project/modules.json 生成模块说明与 labeler |
| check_repository.py | 发布文件、文档链接、模块、JSON/YAML 与常见凭据检查 |
| run_tests.py / test_*.py | 不连接真实服务的现有离线回归 |
| Start/Stop-CodexBridge.ps1 | 本地真实 CLI 桥接启停 |
| validate_*.py | 有特定夹具/空间前提的专项在线验收 |
| import_ecommerce.py 等 | 原有公开/合成数据开发工具，需另外提供外部测试数据路径 |

validate 和导入工具不是默认启动步骤，部分会写测试数据。先读[测试说明](../docs/maintenance/testing.md)与脚本前提；不要直接对真实业务空间批量执行。
