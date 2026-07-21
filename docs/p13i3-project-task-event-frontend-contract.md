# P13-I3 项目任务事件前端提示契约

> 状态：已实现并通过 Codex 独立串行验收
> 日期：2026-07-21
> 前置：P13-I2 项目任务事件 SSE 与断线重放契约（冻结=`525d059`）
> 分支：实现使用 `collab/p13i3-grok-worktree`；最终由 Codex 合并到 `collab/grok-code-codex-review`

## 1. 目标与诚实语义

在技术标和商务标工作区增加项目级任务事件提示。提示只说明收到一个安全的任务状态事件，不自动请求任务详情、不刷新正文、不改变编辑器、不声称任务结果已经可用。既有单任务 `useProjectPipeline` SSE、轮询、取消和任务详情语义保持不变。

## 2. 端点与连接门控

固定地址：`/api/projects/{projectId}/task-events/stream`。

组件仅在 `AUTH_MODE=required`、`useAuthSession.phase === "authenticated"`、活动成员角色精确为 `bid_writer` 且 `projectId` 非空时创建原生 `EventSource`。必须使用 `new EventSource(url, { withCredentials: true })`，URL 只含路径，不带 query、body、自定义认证头或 `X-Workspace-Id`。disabled、未认证、非 `bid_writer`、空项目和项目切换均不得保留连接。

## 3. 事件协议与严格解析

只允许注册并处理四类命名事件：`cursor`、`task-event`、`cursor-stale`、`unavailable`。

- `cursor`：data 必须是精确单键 `eventId`，ID 必须匹配 `pte_` 加 32 位小写十六进制；只作为浏览器水位，不显示、不更新面板。
- `task-event`：`event.lastEventId` 与 data.eventId 必须相同且为合法 `pte_` ID；data 必须精确六键 `eventId/taskId/taskType/status/progress/occurredAt`。`taskId` 必须为当前任务不透明标识 `task_` 加 16 位小写十六进制；`taskType` 必须为非空、无控制字符且不超过 64 个字符；status 只能是 `pending/running/success/failed/cancelled`；progress 必须是 0 到 100 的整数；occurredAt 必须是 UTC 毫秒 `...Z`。不得接受额外键、缺失键、重复顶层键、非法 JSON 或 eventId 不一致。
- `cursor-stale`、`unavailable`：data 必须精确含 `code/message` 两键；不得展示后端 message，统一显示固定中文“项目任务提示暂不可用”，关闭连接。
- 默认 `message`、解析失败、未知可观测帧和 `onerror`：统一显示固定中文“项目任务提示暂不可用”，关闭连接。原生 EventSource 对未注册命名事件不会投递 `onmessage`，该不可观测边界必须在专项中记录，不得用猜测性 monkey patch 声称已覆盖。

重复键必须在 JSON.parse 折叠前通过结构化扫描拒绝；禁止使用简单字符串计数代替 JSON 解析。

## 4. 展示与生命周期

共享组件接收 `projectId` 与固定 `testId`，只保留内存中的一条最新安全事件。收到合法 `task-event` 后，显示固定中文任务类型标签、状态标签和进度百分比；不得显示 taskId、eventId、occurredAt、workspace/project ID、actor、message、error、result、payload、Cookie、Token、URL 或原始后端 detail。未知任务类型显示固定“其他任务”，不得把任意服务端字符串直接插入页面。

项目切换、认证/角色门控失效、卸载或组件重建时，必须先 `close()` 旧连接，再清空旧事件提示；旧项目迟到事件不得污染新项目。不得写入 localStorage、sessionStorage、URL、Cookie、console 或日志，不得自行轮询、定时重连或调用任务详情/编辑态 API。

## 5. 严格四文件白名单

1. `frontend/src/features/project-task-events/ProjectTaskEventPanel.tsx`（新建共享组件）
2. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`（薄挂载）
3. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`（薄挂载）
4. `frontend/e2e/project-task-event-update.spec.ts`（新建专项）

禁止修改 H3 组件、两个编辑 Hook、`useProjectPipeline`、共享 API、认证 Hook、后端、路由、AppShell、依赖、配置及其它测试。禁止提交、推送、暂存或清理 Codex 工件。

## 6. 验收重点

专项必须通过真实 SSE route mock 覆盖：required/authenticated/bid_writer 门控；精确 URL 和 `withCredentials`；首次 cursor 不展示；合法 task-event 的六键、ID 相等、类型/状态/进度展示；相同事件重复到达不产生额外副作用；重复键、额外键、缺键、非法 ID/status/progress/time、控制帧、默认 message、网络错误均固定不可用；项目 A→B 关闭旧流并隔离迟到事件；无任务详情请求、无 storage/URL/console/敏感字段；卸载关闭连接。

Playwright 固定 Chromium、单 worker、零重试；运行 I3 专项、H3/freshness 代表专项、lint、build、diff-check。禁止整仓 E2E、并发 Playwright、后端全量或 xdist。

## 7. 实现与验收回执

- 冻结提交：`5c63890`；功能提交：`c6dbe2e`。
- Grok B worktree `C:\Users\Administrator\biaoshu-p13i3-grok` 已完成实现、自测并由 Codex 合并；该 worktree 不再处于在途状态。
- 真实 failure-first：`1 failed / 1 passed / 3 did not run`；未将未运行项伪报为失败或通过。
- 双方确认的问题链：首轮 question=`msg_6a19689c036540b09eac00d65bbb58a7`，Grok 确认=`msg_5ebe466f38f9404b8294f42c630c6f8a`，返修 task=`msg_98272242fe8741a086c96f460e2f90ed`，最终 review=`msg_bfe30b3e23574d6291f33b9a88baddde`，result=`msg_81187e032a1245d5b566f9238a7959ab`。
- Codex 独立串行验收：I3 专项 `5 passed`、H3 `15 passed`、freshness `17 passed`；lint、build、`git diff --check` 全部通过。Playwright 使用单 worker、零重试，未运行整仓 E2E、后端全量或并发测试。
- 本包仍不提供通知、评论审批、协同光标、WebSocket、任务详情自动刷新或强制锁；下一包必须重新只读审计、冻结契约和白名单。
