# P13-C 当前已载入版本修订来源可见性契约

> 日期：2026-07-20  
> 状态：已冻结，等待 Grok 实现  
> 基线：`e836eb0`（P13-B 已闭环）  
> 分支：`collab/grok-code-codex-review`

## 1. 目标

在技术标与商务标工作区标题区，为当前客户端已经接受的 `editor-state` 版本显示其修订账本来源，例如“浏览器保存”“任务写入”“智能修订”。本包复用既有九类服务端修订来源，不新增表、列、迁移、轮询或独立前端请求。

本包是协作可见性的快速第一版：回答“当前已载入版本在修订账本中由哪类流程形成”，不回答“具体是谁修改”，也不声称当前内容是远端实时最新版本。

## 2. 冻结语义

### 2.1 新响应字段

`GET|PUT /api/projects/{projectId}/editor-state` 在既有响应上增加：

```json
{
  "currentRevisionSourceKind": "browser_put"
}
```

字段必须存在，可为 `null`；非空时只能是既有九类固定来源：

- `browser_put`
- `task`
- `revise`
- `callback`
- `local_parser`
- `content_fuse_apply`
- `content_fuse_consume`
- `checkpoint_restore`
- `revision_restore`

### 2.2 权威判定

服务端仅查询当前 workspace/project 最新一条修订元数据，排序固定为 `created_at DESC, id DESC`，SQL 只投影 `state_version` 与 `source_kind`，禁止加载 `snapshot_json`。

- 最新修订的 `state_version` 与本次响应 `stateVersion` 精确相等，且来源属于九类白名单：返回其 `source_kind`。
- 无修订、最新版本不匹配、来源损坏、响应版本非法：返回 `null`。
- 禁止向更旧历史中搜索“碰巧同版本”的行；只认最新一条，避免把断链账本伪装成当前来源。
- 查询不创建、不修复、不裁剪修订，不提交事务，不写审计。
- GET 读取状态后若发生并发写，版本不匹配必须保守返回 `null`，不能返回新修订来源配旧正文。
- PUT 成功响应只描述该 PUT 已接受的版本；来源查询发生并发漂移时同样保守返回 `null`。

### 2.3 前端接受规则

技术标与商务标 hook 仅在既有合法 `stateVersion` 已通过当前项目会话、写入代次与迟到隔离后，才同时接受同一响应的 `updatedAt` 与 `currentRevisionSourceKind`。

- 来源仅接受九类精确字符串；缺失、`null`、非字符串、大小写变化、首尾空白或未知值一律存为 `null`。
- 项目切换立即清空时间与来源。
- GET/PUT 失败、409、非法 `stateVersion`、旧项目回调、旧写入代次回调不得覆盖当前来源。
- 检查点恢复、修订恢复、内容融合等外部版本化写仍以其后既有唯一 GET 作为接受来源；禁止旁路现有 runner。
- 来源不得参与 CAS、保存队列、矩阵版本、缓存键、localStorage 或请求体。

### 2.4 展示

扩展既有 `EditorStateVersionFreshness`，在原时间下方显示：

- 已知：`当前版本来源：浏览器保存`
- 未知：`当前版本来源：来源未知`

中文映射必须复用 `editorStateRevisionApi.ts` 既有 `REVISION_SOURCE_LABELS` / `formatRevisionSourceLabel`；禁止另建第二套标签。技术标和商务标继续使用各自既有 freshness `data-testid`，来源增加稳定子节点或独立稳定 `data-testid`，测试不得依赖 CSS。

## 3. 明确非目标

- 不记录或展示用户姓名、用户 ID、设备、IP、任务操作者。
- 不新增 actor 列、数据库迁移、用户归因回填。
- 不实现 presence、SSE/WebSocket 协作广播、在线成员、远端实时最新提示或自动刷新。
- 不改变九类来源、修订写入、去重、固定、裁剪、搜索、排序、游标或恢复语义。
- 不改变 `stateVersion` 13 键算法；新字段不得进入快照与哈希。
- 不改任务、callback、解析器、融合、检查点或修订恢复写路径。

## 4. 实现白名单

生产代码最多修改以下九个文件：

1. `backend/app/services/editor_state_revision_service.py`
2. `backend/app/api/schemas.py`
3. `backend/app/api/projects.py`
4. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
5. `frontend/src/features/editor-state-collaboration/EditorStateVersionFreshness.tsx`
6. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
7. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
8. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
9. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`

测试允许新增/修改：

10. `backend/tests/test_p13c_current_revision_source.py`
11. `frontend/e2e/editor-state-version-freshness.spec.ts`

发现必须修改其它文件时停止，通过消息箱向 Codex 申请扩围；不得自行扩围。

## 5. 必验收行为

### 后端

- 无账本返回精确 `null`；GET 零写。
- 九类来源均能在最新版本匹配时原样返回。
- 最新版本不匹配时返回 `null`，不得回扫旧同版本。
- 最新来源非法时返回 `null`，不得泄漏异常或响应 500。
- 浏览器 PUT 真实内容变更后返回 `browser_put`，随后 GET 一致。
- no-op PUT 的返回值与账本权威最新行一致，不臆造“发生内容变更”。
- 跨 workspace / 不存在项目继续既有固定 404，不泄漏来源。
- SQL 证据证明只投影两列、三重作用域、倒序、`LIMIT 1`、无 snapshot。
- 409/422/commit 失败合同与零写语义不变。

### 前端

- 技术标与商务标均展示九类共享中文标签；未知值显示固定未知。
- 初始 GET、普通 PUT、立即 PUT、矩阵合并 PUT、显式重载/外部写后 GET均遵守“合法版本同响应接受”。
- 项目切换立即清空；旧 GET/PUT 成功与失败回调不能污染新项目。
- 409、网络失败、非法 `stateVersion` 不得伪更新来源。
- 不新增 editor-state 之外的 HTTP 请求，不增加轮询/定时器/storage。

## 6. 验收策略

Grok 先写真实 failure-first 测试并报告红测数量，再实现；只跑 P13-C 专项和直接受影响测试。Codex 独立审查 diff、白名单、测试真实性，并独立运行定点后端与 P13-C E2E；只有定点失败指向共享合同，或出现跨域风险时才扩大测试，不机械重复整仓全量。

Grok 禁止 `git add/commit/push`；Codex 验收通过后使用中文提交信息，更新交接、路线图、联调清单并推送协作分支。
