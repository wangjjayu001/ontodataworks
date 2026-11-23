# 本体语义资产与取数 Agent

## 语义权威

本体通过 domain、term、entity、dimension、relation、metric、binding、source、claim、evidence 十类资产表达业务语义。Metric 描述业务计算契约；Binding 将指标修订绑定到确切 CatalogSnapshot 中的表、字段、时间、粒度和允许维度/连接。模型和别名不具有直接改写权威的权限。

机器约束集中在[三份 Schema](../../backend/app/contracts/README.md)。维护前端、后端核验和确定性编译读取同一版本引用；不靠预置 SQL sidecar 作为执行权威。

## 治理和召回

```text
AssetRevision + CatalogSnapshot
          ↓ 核验、职责审核、依赖闭包
       Release
          ↓ 重建与验证
     IndexSnapshot
          ↓ 显式选择完整二元组
      Deployment
          ↓ 运行创建时固定
   RetrievalBundle → QueryFrame
```

草稿、技术核验、业务批准、索引可用与部署是不同状态。审批绑定确切内容及依赖散列；编辑需要新修订。资产的来源附件保留原文字节和散列，来源未知和未审核材料保持未知/草稿。

现有召回组合为 Exact、BM25 和依赖图补链；Vector 关闭。召回结果必须保留选中的 Metric/Binding、目录引用和范围，不能把语义相近当成可执行资格。生产治理和本地 TechnicalSnapshotManifest 分离，本地快照不创建业务批准。

## 取数运行

1. 后端固定工作空间、身份、模型选择及语义部署。
2. 本地 Codex bridge 解析结构化意图，复合问题形成 RequirementItem。
3. 缺时间、指标、事件、比较方向等信息时逐项澄清；重新解析保留用户已确认的条件，不静默丢弃过滤或排除项。
4. 用户确认目标，再由版本化召回选出候选并确认口径。
5. 后端确定性规划和 SQL 编译生成 QueryFrame；模型输出不作为执行 SQL。
6. 执行 Gate 核对物理结构、时间覆盖、允许维度、单位/币种、质量、新鲜度及权限。
7. 用户对当前预览进行一次性授权；内容改变后旧预览和授权失效。
8. 只读查询返回实际数据，记录查询、版本和来源；结果未知时进入核对路径，不能自动重放。

未命中已发布指标时，后端仍在固定发布与 CatalogSnapshot 范围内，按用户现有表字段权限搜索中文目录。Agent 只依据实际返回的表、字段与结构描述提出一个单表临时口径；用户确认统计方式、时间字段、筛选和单位后，后端才编译只读 SQL。目录证据、语义命中排名、模型提议和用户确认是不同信息；候选不新增、不发布，也不作为业务审批。字段、权限或口径不能唯一确定时，继续澄清或说明阻断原因。

成功结果分开展示返回明细、中文执行校验、已确认的临时口径/正式语义修订、实际物理表和字段、数据批次、固定目录快照及可展开的来源原文/证据回执。当前身份的读取权限每次重验。没有有效记录时明确显示“无数据”；`COUNT(*)` / `COUNT(DISTINCT ...)` 的聚合零值不会被误报为有效数据。

口径确认不等于执行授权。取消需等待实际状态确认。前端保留零值、false、NULL、业务列名及错误原因；不在预览阶段生成模拟结果冒充真实执行。

## 实现与维护

- [semantic 模块](../../modules/semantic/README.md)：权威、治理、召回、Binding 编译。
- [agent 模块](../../modules/agent/README.md)：对话、澄清、规划、授权与 worker。
- [模型桥接说明](../../scripts/CODEX-RUNTIME.md)：真实 app-server 目录和调用范围。
- [测试规范](../maintenance/testing.md)：离线状态/编译回归与真实模型验收分开。

当前同比/环比的双期计算规划不支持，会返回 `COMPARISON_PLANNING_UNSUPPORTED`；不能以两年合计代替比较结果。生产 Agent 仍禁用。
