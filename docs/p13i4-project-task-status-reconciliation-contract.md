# P13-I4 项目任务状态安全对账契约

> 状态：只读审计后冻结，等待 Grok A/B failure-first
> 日期：2026-07-21
> 前置：P13-I3 项目任务事件前端提示（功能=`c6dbe2e`）
> 分支：Grok A/B 各自独立 worktree；最终由 Codex 合并到 `collab/grok-code-codex-review`

## 1. 目标与诚实语义

在 I3 安全任务事件提示之上，对当前浏览器通过既有任务流水线发起的那一个任务做一次状态对账。对账只更新内存中的 `status` 与 `progress`，不读取或展示原始任务详情，不刷新正文，不改变 editor-state，不把其它成员的任务当作当前任务。

事件本身仍是触发提示；状态接口是一次只读确认，不是轮询、重试或后台保活。事件丢失、接口失败或任务不匹配时保留 I3 的固定提示语，不显示后端错误。

## 2. 后端安全状态接口

新增：`GET /api/projects/{projectId}/tasks/{taskId}/status`。

- 沿用现有项目任务路由的 `get_workspace_id` 鉴权和项目归属校验；required 模式要求活动 workspace 的 `bid_writer`，disabled 模式保留既有个人版兼容语义。
- 请求不得带 query、body 或 URL token；响应必须 `Cache-Control: no-store`。
- 响应严格为三键：`taskId/status/progress`。`taskId` 仅用于调用方内存中的匹配，不进入页面；status 只能为 `pending/running/success/failed/cancelled`，progress 为 0 到 100 的整数。
- 不得返回或查询后投影 `message`、`error`、`result`、`payload`、`actor_user_id`、workspace/project 内部字段、Cookie、CSRF 或异常原文。服务可在同一事务中读取任务行，但响应层必须使用独立安全投影。
- 不存在项目或任务、跨项目、跨 workspace、非成员和非 `bid_writer` 必须沿用既有固定错误优先级；错误响应不得泄漏 ID、路径、SQL 或栈。

## 3. 前端触发与状态边界

- `ProjectTaskEventPanel` 继续使用 I3 的四类事件严格解析；合法 `task-event` 只在内存中把不透明 taskId 交给回调，不显示它。
- 页面把回调接到 `useProjectPipeline` 的当前任务状态对账函数。仅当事件 taskId 与当前浏览器最近一次 `runTask` 的 taskId 相同，且任务仍为 pending/running 时才发起一次状态 GET。
- 同一项目、同一 taskId 在请求未完成时不得重复发起；项目切换、任务切换、卸载或回调失效必须作废迟到响应。禁止并发请求、定时器、自动重试和轮询。
- 成功响应只更新匹配任务的 status/progress；不得覆盖本地 message/result/error，不得触发 editor-state GET/PUT、正文重载、任务详情 GET、文件列表刷新或 URL/storage/console 写入。
- 终态或接口失败均关闭本次对账状态；失败固定保留 I3 文案，禁止展示服务端 message/error。

## 4. 严格实现白名单

### Grok A：后端

1. `backend/app/api/tasks.py`
2. `backend/app/api/schemas.py`
3. `backend/app/services/task_service.py`
4. `backend/tests/test_p13i4_project_task_status.py`（新建）

### Grok B：前端

1. `frontend/src/features/project-task-events/ProjectTaskEventPanel.tsx`
2. `frontend/src/features/technical-plan/hooks/useProjectPipeline.ts`
3. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
4. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
5. `frontend/e2e/project-task-status-reconciliation.spec.ts`（新建）

禁止修改 I1/I2 后端事件账本与 SSE、认证公共层、编辑 Hook、共享 API、数据库模型/迁移、Playwright 配置、依赖或其它测试。Grok 不得暂存、提交或推送。

## 5. 验收重点

- failure-first 必须先证明状态路由、严格三键、鉴权/作用域和前端单飞回调真实缺失；未运行项不得伪报。
- 后端覆盖 required/disabled、非 writer、非成员、跨项目/跨 workspace、非法 query/body、no-store、状态/进度边界和敏感字段不出响应。
- 前端使用真实 route mock：匹配当前 task 才请求；其它 task、重复事件、项目 A→B、卸载和迟到响应均零副作用；只改 status/progress，保留本地 message/result/error。
- 对账失败、控制帧和网络错误不得出现后端原文；必须证明无任务详情、editor-state、文件列表或额外轮询请求。
- 后端 pytest 与 Playwright 均串行；Playwright 固定 `--workers=1 --retries=0`，不同 worktree 使用独立 SQLite 目录。

## 6. 未交付边界

本包不提供任务结果自动展示、正文自动刷新、通知、评论、审批、协同光标、WebSocket、强制锁、多人任务列表或历史时间线；这些能力必须另行只读审计和冻结。
