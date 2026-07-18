# P12F-G-B 单条修订删除前端契约

模块：P12F-G-B 技术标/商务标共用自动修订单条删除前端
用途：在 P12F-G-A 已验收的物理删除后端之上，提供显式单条删除、内联二次确认、成功重载、失败保留和迟到隔离。
对接：`editorStateRevisionApi.ts`、`EditorStateRevisionPanel.tsx`、`editor-state-revision-history.spec.ts`，以及 `DELETE /api/projects/{projectId}/editor-state-revisions/{revisionId}`。
状态：2026-07-18 已完成只读审计；本文与实施计划提交推送后才冻结三文件边界并允许 Grok failure-first 实现。Grok 只实现/自测、不暂存、不提交、不推送；Codex 负责独立审查、验收、中文闭环和协作分支推送。

## 1. 审计结论

1. P12F-G-A 已提供无 query/body、成功严格空 204 的单条物理删除端点；`apiFetch<void>` 已正确处理 204 并自动为 DELETE 附加内存 CSRF，无需修改共享请求层。
2. 技术标与商务标已经共用 `EditorStateRevisionPanel`，删除不读取或写回当前 editor-state，也不需要修改两个 workspace hook。
3. 当前面板已统一处理 page/search 第一批加载、加载更多、来源/时间/关键词已应用条件和 project/session 迟到隔离。删除成功必须复用这一条加载链；搜索态重发同条件 POST，普通态重发无 cursor 的第一页 GET。
4. 删除是不可恢复写操作，必须与摘要、当前对比、单修订正文差异、双修订选择/比较、恢复、刷新、筛选、搜索和加载更多形成明确互斥，不能在旧意图或在途请求上叠加。
5. 最小范围恰好是 API、共用面板、共用 history E2E 三文件；后端、`api.ts`、workspace hook、样式系统、路由、数据库和依赖均无需变化。

## 2. 用户可见合同

每一条已加载修订增加“删除”按钮。点击只进入该条修订的内联确认态，不发送请求；固定确认文案为：

> 删除后无法恢复。当前编辑内容和检查点不会改变，确定删除这条修订吗？

确认态只显示“确认删除”和“取消”：

- 取消：零 DELETE，关闭确认态，列表、筛选、搜索、状态和当前 editor-state 不变。
- 确认：精确发送一次 DELETE；在途文案为“删除中…”，禁止重复点击、自动重试和第二个删除请求。
- 成功：显示“已删除所选修订”，清除旧摘要/比较/正文差异/双修订选择/恢复确认，按当前已应用条件重载第一批；目标不得继续显示。
- DELETE 失败：显示“删除修订失败，当前列表已保留”，不重载、不清空、不排序、不乐观移除；关闭确认态后可由用户重新发起。
- DELETE 已成功但列表重载失败：成功事实仍显示“已删除所选修订”，同时复用既有列表/搜索固定失败文案；不得把删除错误显示成恢复错误，也不得自动重试 DELETE。

确认文案和错误不得包含 revisionId、projectId、stateVersion、后端 detail/message、路径、关键词、快照、Cookie 或 CSRF。删除按钮只针对自动修订；不出现批量选择、全选、软删除、撤销或回收站。

## 3. API 封装

在 `editorStateRevisionApi.ts` 新增单一导出：

```ts
deleteEditorStateRevision(projectId: string, revisionId: string): Promise<void>
```

约束：

1. 复用 `isValidRevisionId`；非法 ID 在发请求前固定抛出内部错误。
2. 路径只允许 `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/${encodeURIComponent(revisionId)}`。
3. `apiFetch<void>` 的 init 精确只有 `method: "DELETE"`；不得传 body、query、retry、轮询、额外 header 或读取响应 JSON。
4. 不导出响应体，不添加客户端删除结果类型，不吞掉 `ApiError`，不修改 `apiFetch`。
5. 同步文件顶四字段及对接说明；既有 list/page/search/detail/diff/restore parser 字节语义不变。

## 4. 面板状态机与互斥

新增状态至少区分：待确认 revisionId、当前删除在途、删除请求代次。revisionId 只保存在 React 内存/ref，不渲染到 DOM、URL、存储、日志或错误。

### 4.1 进入确认态

点击“删除”必须：

- 作废并清除摘要、当前对比、单修订正文差异、双修订选择/结果和恢复确认；
- 清除旧删除状态文案；
- 只设置当前待确认 ID，DELETE 计数保持零；
- 同一时刻最多一个待确认；确认期间除“确认删除/取消”外，折叠、刷新、来源/时间/搜索、加载更多和所有行操作均真实 disabled。

`disabled` 属性仍只表达当前 editor-state 恢复是否安全；删除不依赖 expectedStateVersion，因此不能仅因 `disabled=true` 永久隐藏删除能力。删除仍受列表加载、加载更多、恢复或删除在途状态阻断。

### 4.2 执行删除

确认时同步关闭重复入口并捕获 `session + delete generation + projectId + revisionId`。执行期间：

- 精确一次调用 API，不并发 refresh/page/search/load-more/detail/diff/restore；
- 所有 success/catch/finally 写状态前同时校验 mounted、session、generation 和当前项目；
- 项目切换或卸载递增 generation、清空确认/忙碌/文案；旧请求即使服务端已完成，也不得污染新项目；
- 旧 finally 不得清除新项目或更新一轮删除的忙碌状态。

折叠按钮在确认与删除在途期间真实 disabled，避免把不可撤销请求伪装成已取消；外部项目切换仍必须安全隔离。

### 4.3 成功后的唯一重载

DELETE 成功且仍属当前会话后，先记录固定成功事实，再调用既有第一批加载链：

- `appliedSearch != null`：精确一次 POST search，body 继续为 query→可选 sourceKind→可选 createdFrom→可选 createdBefore；无 cursor。
- 非搜索态：精确一次 GET page，保留已应用来源/时间，禁止 cursor，已加载第二页被第一批替换。
- 草稿值和已应用值保持原状；不生成新 revisionId/stateVersion，不请求 editor-state，不触发 PUT/restore/checkpoint。
- 重载成功后目标消失，其它条目仍按服务端顺序；重载失败沿用既有固定失败和空态策略，不能再次 DELETE。

## 5. E2E 探针和反假绿要求

在现有 history E2E 内扩展 DELETE 探针，至少记录 arrived 与 complete 两阶段：projectId、revisionId、method、path、query、postData；提供 ok、hold、HTTP error 三种模式。204 必须以空 body fulfill，成功才从探针的 `revisions` 和 `details` 删除目标；失败不得修改探针状态。

新增三个互相独立、不得用 serial 前例失败跳过的 P12F-G-B 用例：

1. **技术标确认、成功、失败与重载**：默认/点击/取消均零 DELETE；确认精确一次 DELETE、无 query/body；成功普通页和搜索态分别只重载第一批且保留已应用条件；目标消失、其它顺序不变、五类写旁路为零；404/500 固定失败、列表保值、零重载/重试。
2. **技术标互斥与 arrived/complete 迟到隔离**：确认前清除已有摘要/比较/body-diff/pair/restore 意图；确认/在途所有控制真实 disabled；A 删除挂起后切 B，再发 B 删除，释放 A 的 success/catch/finally 不污染 B、不中止 B loading；B 完成后状态与列表正确；旧删除后的迟到 page/search 重载也不能污染新项目。
3. **商务标共用入口与数据最小化**：同一按钮/确认/失败/成功语义；搜索+来源+时间条件下成功只重发原 search；editor-state GET/PUT、restore、checkpoint create、外网、console、URL/存储/Cookie 均无 ID/关键词/快照/CSRF 泄漏。

每项关键断言必须是精确计数、精确路径/方法/body/query、精确文案和真实 DOM 状态。禁止 `.or(...)`、`>= 1`、宽状态、条件断言、固定 sleep、skip/xpass、吞异常、只等 arrived 不等 complete、route fallback 假成功，或用 `force: true` 点击 disabled 控件。

Failure-first 阶段只允许修改 E2E：两个生产文件哈希必须仍为冻结值；三个新用例必须真实进入页面后因删除按钮/API/状态机缺失而失败，不得以导入错误、语法错误、登录失败、服务未启动或前例跳过冒充红测。

## 6. 三文件白名单与冻结哈希

Grok 只允许修改：

1. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
2. `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
3. `frontend/e2e/editor-state-revision-history.spec.ts`

冻结前 SHA-256：

- API：`4EB053C284A6F4059D559842B3A6C5C0AF829BDF08E26A8528E0760B0B02D433`
- 面板：`524D5AC6D494736492E4A18385DEE74C7F7547129888E322808548A17F8F81FF`
- history E2E：`D7BFAE7EDD61747DE790FDC188E9C61959E93529AA1093F514E1B6BBCC7D63BB`

禁止修改任何后端、其它前端/E2E、`frontend/src/shared/lib/api.ts`、技术标/商务标 hook、文档、样式系统、配置、依赖/锁文件或 Git 历史。Grok 不得 `git add/commit/push`。

## 7. 串行验收门

Grok 至少逐条串行运行：

1. `npx --no-install playwright test e2e/editor-state-revision-history.spec.ts --grep "P12F-G-B" --workers=1 --retries=0`
2. `npx --no-install playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0`
3. `npx --no-install playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0`
4. `npx --no-install playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0`
5. `npx --no-install playwright test e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0`
6. `npm run lint`
7. `npm run build`

Codex 独立复核以上分组，并额外串行运行前端全量 `npx --no-install playwright test --workers=1 --retries=0`。最后执行 `git diff --check`、精确三文件、空暂存区、哈希、AST/禁区/弱断言扫描。所有 Playwright 共用 SQLite 重置库，禁止并行启动多个命令。

Grok 的 `review_request` 必须报告真实红测 failed/passed/did-not-run、首个业务失败、最终每组结果、DELETE arrived/complete 证据、普通页/搜索重载证据、迟到隔离、零旁路、精确文件和 SHA-256、风险与未做项。

## 8. 明确未做

不做多选/批量/范围删除、软删除/墓碑、撤销/回收站、自动清理、命名/固定/标签、检查点删除、当前 editor-state 删除/恢复、删除审计报表、导出/分享、跨项目历史、多人协作、SSE/WebSocket、客户端缓存、离线队列、数据库/迁移/依赖/配置变化。
