# 第三方组件与许可保留

原创业务实现与第三方组件分别维护。以下记录实际安装的前端构建依赖及后端依赖，不将其版权替换为本项目版权。完整许可文本保存在 third-party-licenses；运行容器也保留各发行包本身的许可文件。

| 生态 | 软件包 | 版本 | 声明许可 |
| --- | --- | --- | --- |
| npm | baseline-browser-mapping | 2.11.35 | Apache-2.0 |
| npm | browserslist | 4.29.5 | MIT |
| npm | caniuse-lite | 1.0.30001823 | CC-BY-4.0 |
| npm | convert-source-map | 2.0.0 | MIT |
| npm | debug | 4.4.3 | MIT |
| npm | electron-to-chromium | 1.5.478 | ISC |
| npm | esbuild | 0.21.5 | MIT |
| npm | escalade | 3.2.0 | MIT |
| npm | gensync | 1.0.0-beta.2 | MIT |
| npm | js-tokens | 4.0.0 | MIT |
| npm | jsesc | 3.1.0 | MIT |
| npm | json5 | 2.2.3 | MIT |
| npm | loose-envify | 1.4.0 | MIT |
| npm | lru-cache | 5.1.1 | ISC |
| npm | lucide-react | 0.468.0 | ISC |
| npm | ms | 2.1.3 | MIT |
| npm | nanoid | 3.3.20 | MIT |
| npm | node-releases | 2.0.63 | MIT |
| npm | picocolors | 1.1.1 | ISC |
| npm | postcss | 8.5.32 | MIT |
| npm | react | 18.3.1 | MIT |
| npm | react-dom | 18.3.1 | MIT |
| npm | react-refresh | 0.14.2 | MIT |
| npm | rollup | 4.68.1 | MIT |
| npm | scheduler | 0.23.2 | MIT |
| npm | semver | 6.3.2 | ISC |
| npm | source-map-js | 1.2.2 | BSD-3-Clause |
| npm | update-browserslist-db | 1.3.4 | MIT |
| npm | vite | 5.4.11 | MIT |
| npm | yallist | 3.1.1 | ISC |
| npm | @babel/code-frame | 7.29.7 | MIT |
| npm | @babel/compat-data | 7.29.7 | MIT |
| npm | @babel/core | 7.29.13 | MIT |
| npm | @babel/generator | 7.29.13 | MIT |
| npm | @babel/helper-compilation-targets | 7.29.13 | MIT |
| npm | @babel/helper-globals | 7.29.7 | MIT |
| npm | @babel/helper-module-imports | 7.29.7 | MIT |
| npm | @babel/helper-module-transforms | 7.29.13 | MIT |
| npm | @babel/helper-plugin-utils | 7.29.7 | MIT |
| npm | @babel/helper-string-parser | 7.29.13 | MIT |
| npm | @babel/helper-validator-identifier | 7.29.7 | MIT |
| npm | @babel/helper-validator-option | 7.29.7 | MIT |
| npm | @babel/helpers | 7.29.13 | MIT |
| npm | @babel/parser | 7.29.13 | MIT |
| npm | @babel/plugin-transform-react-jsx-self | 7.29.7 | MIT |
| npm | @babel/plugin-transform-react-jsx-source | 7.29.7 | MIT |
| npm | @babel/template | 7.29.7 | MIT |
| npm | @babel/traverse | 7.29.13 | MIT |
| npm | @babel/types | 7.29.11 | MIT |
| npm | @esbuild/win32-x64 | 0.21.5 | MIT |
| npm | @jridgewell/gen-mapping | 0.3.13 | MIT |
| npm | @jridgewell/remapping | 2.3.5 | MIT |
| npm | @jridgewell/resolve-uri | 3.1.2 | MIT |
| npm | @jridgewell/sourcemap-codec | 1.6.0 | MIT |
| npm | @jridgewell/trace-mapping | 0.3.31 | MIT |
| npm | @rollup/rollup-win32-x64-gnu | 4.68.1 | MIT |
| npm | @rollup/rollup-win32-x64-msvc | 4.68.1 | MIT |
| npm | @types/babel__core | 7.20.5 | MIT |
| npm | @types/babel__generator | 7.27.0 | MIT |
| npm | @types/babel__template | 7.4.4 | MIT |
| npm | @types/babel__traverse | 7.28.0 | MIT |
| npm | @types/estree | 1.0.10 | MIT |
| npm | @vitejs/plugin-react | 4.3.4 | MIT |
| python | pip | 25.0.1 | MIT |
| python | sqlglot | 26.33.0 | 完整声明见对应许可文件 |
| python | pydantic_core | 2.50.4 | MIT |
| python | h11 | 0.16.0 | MIT |
| python | pycparser | 3.12 | BSD-3-Clause |
| python | starlette | 0.41.3 | BSD-3-Clause |
| python | psycopg-binary | 3.2.3 | GNU Lesser General Public License v3 (LGPLv3) |
| python | pydantic | 2.14.3 | MIT |
| python | typing-inspection | 0.4.5 | MIT |
| python | annotated-types | 0.8.0 | MIT |
| python | greenlet | 3.5.6 | MIT AND PSF-2.0 |
| python | cffi | 2.1.1 | MIT-0 |
| python | idna | 3.20 | BSD-3-Clause |
| python | anyio | 4.16.0 | MIT |
| python | psycopg | 3.2.3 | GNU Lesser General Public License v3 (LGPLv3) |
| python | fastapi | 0.115.6 | See distribution license files |
| python | click | 8.5.1 | BSD-3-Clause |
| python | uvicorn | 0.34.0 | BSD-3-Clause |
| python | cryptography | 44.0.0 | Apache-2.0 OR BSD-3-Clause |
| python | SQLAlchemy | 2.0.36 | MIT |
| python | typing_extensions | 4.16.0 | PSF-2.0 |

PostgreSQL 16 使用 PostgreSQL License；Nginx 使用 BSD 类许可；Python 运行时、Debian/Alpine 系统及 psycopg-binary 内含原生库另有各自声明，分发镜像时应一并保留其发行版中的版权文件。前述包的清单不替代容器操作系统的完整 SBOM。

镜像 digest 固定于 compose.yml 与 backend/Dockerfile，容器实际镜像 ID 记录在持久化验收文件。

没有对 qData 的 LICENSE/NOTICE 做重写、去重或替换；qData 安装目录属于保留的旧环境。本项目不导入或运行其业务代码。
