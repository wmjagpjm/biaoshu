# P13-H3 编辑状态事件前端版本提示契约

> 状态：实现已完成；原生 EventSource 未注册命名事件不可观测边界已记录，协议扩展另包裁定
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
5. failure-first 前进一步核对确认：两个编辑 Hook 只在 `stateVersionRef.current` 持有已接受版本，均未向页面导出；`versionUpdatedAt/sourceKind/actor` 不能等价替代版本相等判断。原四文件白名单因此无法诚实实现“相同版本忽略、不同版本提示”。Codex question=`msg_6889315838a447a4be37811772f2a174`，Grok 只读确认=`msg_baac83f66c214b279eb8192527beab0d`；双方确认后才允许最小扩围两个 Hook。

## 3. 生产行为

共享组件接收 `projectId`、当前已载入 `stateVersion`、刷新回调和固定 `testId`。组件生命周期绑定认证、角色和项目代次：条件失效、项目切换或卸载时立即 `EventSource.close()`，旧项目事件不得改变新项目提示。

只允许解析四类 SSE：

- `cursor`：只作为浏览器重连水位，不显示、不写状态版本。
- `editor-state`：严格验证 `event.lastEventId`、data 的 `eventId/stateVersion/sourceKind/occurredAt`；`event.lastEventId` 与 data.eventId 必须同为合法 `ese_` ID，stateVersion 必须为 `esv_` 加 32 位小写十六进制，sourceKind 只能为 H2/H1 九类来源，occurredAt 必须为 UTC 毫秒 `...Z`。
- `cursor-stale`、`unavailable`：关闭连接，显示固定不可用提示。
- 默认 `message`、缺字段、重复字段、解析异常或网络错误：按不可用处理并关闭连接，不展示后端原文。

原生 `EventSource` 只向同名 `addEventListener` 投递带 `event:` 的命名事件，没有通配监听；未注册命名事件不会进入 `onmessage`，客户端无法观察或关闭该连接。H3 保持原生 `EventSource` 与 H2 命名事件协议，不改用 `fetch` 流或猜测性 monkey patch；该不可观测边界由专项真实 SSE 用例记录，后续若要覆盖任意未知命名事件，必须另行冻结协议和实现包。

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
4. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
5. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`

两个 Hook 只允许增加并导出 `currentStateVersion`：合法 GET/PUT/既有外部写响应接受版本时与 `stateVersionRef.current` 同步；项目切换、非法版本和会清空权威版本的加载失败路径同步清空。不得改变请求次数、保存链、CAS/冲突、自动保存、阻断、重载或既有元数据语义。

测试文件：

6. `frontend/e2e/editor-state-event-update.spec.ts`

禁止修改共享 API、认证 Hook、路由、AppShell、后端、依赖、配置及其它测试；两个编辑 Hook 仅限上述版本镜像导出。若现有刷新接口或 E2E 桩仍不足，必须先发只读 question，双方确认后才能申请扩围。

## 6. 验收重点

专项必须覆盖：认证/RBAC/disabled 门控、首次 cursor 不提示、合法新版本提示、相同版本忽略、cursor/editor-state 四字段严格 parser（含重复键和合法字符串对照）、控制帧/默认 `message`/坏帧/网络错误固定不可用、用户确认单次刷新成功/失败、技术/商务项目 A→B 关闭与迟到隔离、无 storage/URL/正文写入、EventSource `withCredentials` 和精确 URL，并记录未注册命名事件不可观测边界。Playwright 固定 Chromium、`--workers=1 --retries=0` 串行运行；另跑既有 freshness 专项、lint、build、diff-check，不跑整仓 E2E 或并发测试。

## 7. 实现与验收回执

- Grok 初始实现回执：`msg_52e843e975874aafad57b902885a3112`；Codex 首轮只读问题：`msg_e1363c19078d422fa33e6df925346b31`；Grok 五项确认：`msg_cb44e9eb820044219411705642779060`。
- 第一轮返修授权：`msg_13ffc440e2cf4b05bf26ad59fa2e6574`；回执：`msg_e9809e17435c494589e7cf1f13b8262a`。A-D 已修，E 保留为原生边界。
- 第二轮 F/G 确认：`msg_4b1db4d34b6744ec9185a53a1af8bd6e`；H 确认：`msg_ac39ea4388364d70b3dd7eb8f2510852`；返修授权：`msg_8b9b65bd31d34cabbd6545644ecbf8e2`；Grok 回执：`msg_898315bea44b4cfca1435744b0cd920f`。
- Codex 独立串行验收：H3 `15 passed`、freshness `17 passed`、lint/build/diff-check 全通过；未运行整仓 E2E、后端全量或并发测试。
- 功能提交：`40aacc7`（严格六文件，中文提交信息）。
