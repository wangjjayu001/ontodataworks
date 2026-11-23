# 前端开发

React 18 / Vite，Node 22 LTS / pnpm 10.11.0。根目录运行：

```powershell
pnpm --dir frontend install --frozen-lockfile --config.node-linker=hoisted
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend dev
```

开发代理把 `/api` 转发到本机 19090；静态部署构建产物由 Nginx 挂载。页面分别在 main.jsx、SemanticAssets/SemanticWorkbench、SmartQuery、PipelineWorkspace/PipelineV11、ModelPublishing 中维护，详见[模块表](../docs/maintenance/modules.md)。

UI 不持有平台或数仓密钥；模型目录为后端实时获取，文件中的历史目录不是运行时能力证明。
