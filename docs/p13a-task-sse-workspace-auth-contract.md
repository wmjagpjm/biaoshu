# P13-A 任务 SSE 工作空间鉴权闭环契约

模块：P13-A 单任务 SSE 工作空间鉴权与短会话隔离
用途：关闭 `/tasks/{taskId}/events` 未复用统一角色/成员解析、流内快照未再次按工作空间约束的越权缺口，同时保持个人版、原生 EventSource 与既有 SSE 事件合同不变。
对接：`app.api.tasks`、`app.api.deps.get_workspace_id`、`app.services.task_service`、认证中间件与 `useProjectPipeline`。
状态：2026-07-17 已冻结待 Grok 实现；Codex 独立审查、验收、文档闭环、提交并推送。

## 1. 审计结论与安全边界

普通任务列表、详情、创建和取消均通过 `get_workspace_id` 解析当前工作空间；现有 SSE 路由却自行采用 `X-Workspace-Id` 或 `DEFAULT_WORKSPACE_ID`，并在流内调用只校验 `project_id/task_id` 的 `_read_task_snapshot`。

在 `AUTH_MODE=required` 下，认证中间件仍会拦截无会话请求，因此本包不虚构“完全绕过登录”；真实缺口是：

- 已登录的 `finance/hr/bidder` 可绕过 `bid_writer` 角色限制读取已知任务；
- 已登录用户可用任意 `X-Workspace-Id` 选择非成员空间，并在已知项目/任务 ID 时读取任务快照；
- 原生 EventSource 不带自定义头时，路由忽略会话的 `activeWorkspaceId`，错误固定到默认空间；
- 连接建立后的每次短会话读取没有重新按 workspace/project/task 三层约束，后续维护时存在跨空间回归面。

P13-A 只关闭上述鉴权与资源生命周期缺口，不扩展业务功能。

## 2. 路由鉴权合同

目标路由保持不变：

```text
GET /api/projects/{projectId}/tasks/{taskId}/events
```

连接前必须复用 `get_workspace_id` 的完整现有语义：

- `AUTH_MODE=disabled`：保证默认 workspace 存在；非空 `X-Workspace-Id` 仍是个人版兼容选择器，否则使用默认空间；
- `AUTH_MODE=required`：认证中间件继续负责 401 会话闸门；路由解析必须要求当前活动成员角色精确为 `bid_writer`；
- required 且没有头时使用主体 `activeWorkspaceId`，与同源原生 EventSource 兼容；
- required 且带头时，头只能在当前用户的活动成员空间内选择；非成员固定 403 `workspace_forbidden`；
- `finance/hr/bidder` 固定 403 `role_forbidden`；不得因 owner 隐式放行；
- 已授权工作空间内项目不存在固定 404“项目不存在”，项目存在但任务不匹配固定 404“任务不存在”；
- 鉴权/成员/角色错误必须先于项目/任务探测，不得通过状态码或正文泄漏资源是否存在。

不得新增 query token、URL Cookie、Bearer 参数、临时票据、公开路由白名单或 CORS 放宽。无 Cookie 时即使附带 `?token=...` 仍由既有中间件固定 401，且不得反射 token。

## 3. 短会话与流内再校验

不能把普通 `Depends(get_db)` 或其默认 request-scope 子依赖直接挂到 StreamingResponse 上并长期持有数据库会话。实现必须满足：

1. 在 `tasks.py` 定义 SSE 专用私有依赖；它打开一个 `SessionLocal`，以显式实参调用统一 `get_workspace_id`，随后用 `task_service.get_task` 做连接前 workspace/project/task 校验；
2. 上述依赖无论成功或异常都在路径函数返回 StreamingResponse 前关闭会话，只返回已授权 `workspace_id`；
3. 路径函数和异步生成器不得捕获 Session、ORM 行或认证原始凭据；
4. `_read_task_snapshot` 改为显式接收 `workspace_id, project_id, task_id`；每次轮询新开短 Session，复用 `get_task` 做三层归属校验，`ProjectNotFoundError/KeyError` 统一返回 `None`，最后必关闭；
5. 生成器每次 `run_in_threadpool` 都必须传入连接前解析出的 workspace，不得回退默认空间，也不得只按任务主键读取；
6. 不得为此增加长事务、锁、commit/rollback/flush/refresh、全表扫描或后台线程共享 Session。

## 4. SSE 兼容合同

本包不得改变：

- 首帧为完整 `snapshot`；状态签名变化发 `task`；约 15 秒无变化发 `heartbeat`；终态后关闭；
- 客户端断开和 11 分钟服务端超时语义；
- `text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`；
- 任务响应与 SSE 载荷现有公开字段，不得加入 workspace、用户、角色、Cookie、CSRF、内部 expected/stateVersion 或异常原文；
- 前端 EventSource 失败后先 GET 一次、再约 2 秒轮询的回退路径；
- disabled 模式下默认空间和合法 `X-Workspace-Id` 的现有个人版行为。

不修改前端，因为同源 EventSource 自动携带 HttpOnly 会话 Cookie；required 模式不带自定义头时统一解析活动工作空间即可。

## 5. 三文件白名单

Grok 只允许修改：

1. `backend/app/api/tasks.py`
2. `backend/app/services/task_service.py`
3. `backend/tests/test_p13a_task_sse_workspace_auth.py`（新建）

禁止修改 `deps.py`、认证中间件、配置、模型、schema、数据库/迁移、普通任务 REST、前端/E2E、依赖、启动脚本或文档；不得 `git add/commit/push`。

## 6. Failure-first 与专项测试

Grok 必须先只新建测试文件，在两个生产文件仍与冻结提交一致时运行真实业务红测。至少出现以下旧行为失败：

- required 下 `finance/hr/bidder` 读取已知终态任务得到 200，而期望固定 403；
- required 下成员通过非成员 `X-Workspace-Id` 读取该空间已知任务得到 200，而期望固定 403；
- required 下已切换到第二成员空间的 bid_writer 使用无头原生 EventSource 路径得到默认空间 404，而期望活动空间 200。

不得用收集、导入、fixture、依赖、语法、超时或错误 URL 冒充 failure-first。红测报告必须给出命令、失败/通过数字、首个业务断言和生产文件未修改证据。

最终专项至少覆盖：

- required 无会话 401 `auth_required`，URL token 无效且不反射；
- required `finance/hr/bidder` 均为 403 `role_forbidden`，响应无任务载荷、ID、Cookie、CSRF 或角色外业务数据；
- required 非成员头为 403 `workspace_forbidden`，且先于资源 404；
- required 默认空间 bid_writer 成功；切换第二成员空间后无头请求成功；成员内显式头选择成功；
- 已授权空间内跨空间项目/任务统一 404，零快照泄漏；
- disabled 默认空间与合法显式工作空间仍成功；
- 连接前 Session 在首个流内读取前已经关闭，生成器不持有请求 Session；
- 生成器把授权 workspace 精确传给每次 `_read_task_snapshot`；该函数直测跨空间返回 `None`、同空间返回公开快照且每次 Session 关闭；
- 既有 snapshot/heartbeat/task/terminal/cancel/unknown 语义回归通过；
- `py_compile`、`git diff --check`、精确三文件和空暂存区。

## 7. Codex 独立验收门

Grok 只发送 review_request，并如实列出红/绿数字、会话关闭证据、三文件清单、风险和未做边界。Codex 至少独立运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p13a_task_sse_workspace_auth.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_task_sse.py tests\test_auth_rbac.py tests\test_p12b_delayed_writer_fences.py
.\.venv\Scripts\python.exe -m pytest -q
```

并检查 `py_compile`、`git diff --check`、冻结提交到工作区的精确三文件、空暂存区和未跟踪非白名单。全部通过后才由 Codex 提交实现、推送，再完成中文文档闭环。

## 8. 明确未做

本包不做 SSE 事件游标、`Last-Event-ID`、重放、多任务总线、WebSocket、在线成员/presence、跨标签页保活、轮询改造、前端工作空间切换 UI、query token、审计事件、任务字段扩展、数据库迁移、修订历史搜索/删除或任何 P12F 延伸功能。
