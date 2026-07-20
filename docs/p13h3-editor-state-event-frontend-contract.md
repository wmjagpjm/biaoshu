# P13-H3 编辑状态事件前端版本提示契约

> 状态：契约冻结，待 Grok 实现与 Codex 独立验收
> 日期：2026-07-21
> 前置：P13-H2 项目级 editor-state SSE（实现=`c19bf94`）
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`

## 1. 目标与诚实语义

在 P13-H2 SSE 之上，为技术标和商务标工作区提供项目级“版本有变化”保守提示。提示只说明收到一个不同的 editor-state 版本，不承诺远端最新、实时、在线或自动合并。用户必须明确点击后才读取既有 editor-state；刷新失败仍由既有工作区固定错误语义处理。

本包不自动覆盖正文、不自动 PUT editor-state、不解析或展示正文/章节/任务结果，不做评论审批、通知、协同光标、WebSocket、多任务总线或强制锁。

## 2. 只读审计结论

1. H2 地址固定为 `/api/projects/{projectId}/editor-state-events/stream`，不接受 query；原生 `EventSource` 使用 `withCredentials: true`，认证仍由同源 Cookie 完成。
2. 技术标 `useTechnicalPlanEditors.reloadFromApi({ blocking: true })` 与商务标 `useBusinessBidWorkspace.refreshFromApi()` 都返回单次真实 `Promise<boolean>`，适合作为用户确认后的唯一刷新动作。
3. 两个工作区已有 `useAuthSession` 可提供 `phase`、`authRequired`、活动成员角色；项目 ID 来自当前路由。disabled、未认证、非 `bid_writer`、无项目 ID 均不得建立连接。
4. `EditorStateVersionFreshness` 必须继续纯展示、零副作用；新逻辑独立组件并由页面薄挂载。

## 3. 生产行为

共享组件接收 `projectId`、当前已载入 `stateVersion`、刷新回调和固定 `testId`。组件生命周期绑定认证、角色和项目代次：条件失效、项目切换或卸载时立即 `EventSource.close()`，旧项目事件不得改变新项目提示。

只允许解析四类 SSE：

- `cursor`：只作为浏览器重连水位，不显示、不写状态版本。
- `editor-state`：严格验证 `event.lastEventId`、data 的 `eventId/stateVersion/sourceKind/occurredAt`；`event.lastEventId` 与 data.eventId 必须同为合法 `ese_` ID，stateVersion 必须为 `esv_` 加 32 位小写十六进制，sourceKind 只能为 H2/H1 九类来源，occurredAt 必须为 UTC 毫秒 `...Z`。
- `cursor-stale`、`unavailable`：关闭连接，显示固定不可用提示。
- 其它事件、缺字段、重复字段、解析异常或网络错误：按不可用处理并关闭连接，不展示后端原文。

收到合法 `editor-state` 且 stateVersion 与当前已载入版本不同，显示固定提示“检测到远端版本变化，请确认后重新载入”。相同版本、cursor 或非法帧不显示刷新提示。提示只保留内存状态，不写正文、URL、storage、Cookie、console 或日志。

用户点击“重新载入远端内容”后仅调用页面传入的刷新函数一次；调用期间按钮禁用，成功清除提示，失败保留既有页面错误语义并显示固定重载失败提示。组件不得自行轮询、重连计时、调用 editor-state PUT 或改变编辑器字段。

## 4. 作用域、隐私与失败边界

- 仅 `AUTH_MODE=required`、认证阶段 `authenticated`、活动角色精确 `bid_writer` 且存在项目 ID 时连接。
- 不发送 `X-Workspace-Id`、query、body 或自定义认证头；不读取 Cookie/Token。
- 页面不展示 event ID、project ID、workspace ID、actor、client、正文、任务或后端 detail。
- EventSource 的 `onerror` 只产生固定不可用提示并关闭，禁止浏览器无限重连。
- 项目切换必须先关闭旧连接并清空旧提示；迟到事件、迟到刷新结果不得污染新项目。

## 5. 严格白名单

生产文件：

1. `frontend/src/features/editor-state-collaboration/EditorStateEventUpdatePanel.tsx`
2. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
3. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`

测试文件：

4. `frontend/e2e/editor-state-event-update.spec.ts`

禁止修改共享 API、认证 Hook、既有编辑 Hook、路由、AppShell、后端、依赖、配置及其它测试；若现有刷新接口或 E2E 桩不足，必须先发只读 question，双方确认后才能申请扩围。

## 6. 验收重点

专项必须覆盖：认证/RBAC/disabled 门控、首次 cursor 不提示、合法新版本提示、相同版本忽略、四字段严格 parser、控制帧/坏帧/网络错误固定不可用、用户确认单次刷新成功/失败、项目 A→B 关闭与迟到隔离、无 storage/URL/正文写入、EventSource `withCredentials` 和精确 URL。Playwright 固定 Chromium、`--workers=1 --retries=0` 串行运行；另跑既有 freshness 专项代表用例、lint、build、diff-check，不跑整仓 E2E 或并发测试。

