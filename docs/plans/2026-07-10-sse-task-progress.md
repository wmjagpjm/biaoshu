# SSE 任务进度实施计划

> **给 Codex 与 Grok：** Codex 按任务顺序实现和验证；Grok 在实现前审查协议、实现后审查 diff 与回归结果。

**目标：** 用单任务 SSE 状态流替代技术标和商务标的一秒轮询，同时在流连接失败时保留 GET 单任务查询回退。

**架构：** 后端以 SQLite 中 `project_tasks` 行为唯一状态源，SSE 每次读取使用独立短生命周期 Session，推送完整任务快照、心跳和终态后关闭。前端仅改共用的 `useProjectPipeline`：创建任务后优先订阅 EventSource，异常时以两秒 GET 轮询直到终态或原有十分钟总超时。

**技术栈：** FastAPI、Starlette `StreamingResponse`、SQLAlchemy、SQLite、React、TypeScript、EventSource、pytest、Vite。

---

## 已冻结的 v1 语义

- SSE 地址：`GET /api/projects/{project_id}/tasks/{task_id}/events`。
- 事件：`snapshot`（连接后立即推送）、`task`（状态变化）、`heartbeat`（15 秒）。`data` 都是 JSON；任务事件与现有 `task_to_dict` 同构。
- 终态：`success`、`failed`、`cancelled`。推送终态快照后自然关闭流。
- 前端任务总超时维持 10 分钟；超时不自动取消后端任务。
- EventSource 失败且尚未收到终态时，立即 GET 一次；仍在运行则以 2 秒 GET 轮询到终态或超时，不无限重连 SSE。
- SSE v1 只使用默认工作空间；页面没有 `X-Workspace-Id` 需求。自定义工作空间、Bearer 鉴权、事件游标和断点续传留待多用户项目。
- 不引入 Redis、Celery、WebSocket、项目级总线、WAL 改造或页面信息架构改动。

## 任务 1：编写后端 SSE 失败测试

**文件：**

- 新建：`backend/tests/test_task_sse.py`
- 参考：`backend/tests/test_async_and_callback.py`
- 参考：`backend/tests/test_task_cancel.py`

**步骤 1：** 先写齐文件顶“模块 / 用途 / 对接 / 二次开发”注释。

**步骤 2：** 为同步完成任务请求 `/events`，断言响应类型为 `text/event-stream`、先收到 `snapshot`，快照字段与 GET 单任务字段兼容，并在终态后结束。

**步骤 3：** 为已取消任务请求 `/events`，断言快照是 `cancelled` 且流结束；为未知项目或任务断言 404。

**步骤 4：** 运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python -m pytest tests\test_task_sse.py -q
```

**预期：** 实现前因路由不存在而失败。

## 任务 2：实现后端单任务 SSE 流

**文件：**

- 修改：`backend/app/api/tasks.py`
- 修改：`backend/app/services/task_service.py`
- 测试：`backend/tests/test_task_sse.py`

**步骤 1：** 先更新两个触达模块的文件顶四字段，写清 SSE、短 Session 和默认工作空间限制。

**步骤 2：** 在 `task_service.py` 增加私有快照读取和 SSE 帧序列化函数；每次读取都新建并关闭 `SessionLocal()`，不复用 HTTP 请求或 worker Session。

**步骤 3：** 在 `tasks.py` 增加薄路由：连接前验证项目和任务归属，生成器中以非阻塞等待读取短会话快照，首次发 `snapshot`，变化时发 `task`，15 秒无变化发 `heartbeat`，终态后关闭。

**步骤 4：** 生成器检查客户端断开和 11 分钟硬上限；断开、任务不存在或超限后释放会话并结束，不影响后台 worker。

**步骤 5：** 运行新测试及既有异步/取消测试：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python -m pytest tests\test_task_sse.py tests\test_async_and_callback.py tests\test_task_cancel.py -q
```

**预期：** SSE 测试通过，GET 轮询和取消契约不回归。

## 任务 3：前端切换为 SSE 优先、GET 回退

**文件：**

- 修改：`frontend/src/features/technical-plan/hooks/useProjectPipeline.ts`

**步骤 1：** 修改前先将文件顶和公开 hook 注释补齐四字段，更新原有“轮询”说明。

**步骤 2：** 增加 hook 内私有 EventSource 等待器，使用 `getApiBase()` 和完整事件路径；监听 `snapshot`、`task`、`heartbeat`，每个任务快照更新 `lastTask`。

**步骤 3：** 在收到终态时关闭 EventSource 并返回任务；连接异常且未终态时关闭流，立即 GET 一次，再按 2 秒间隔复用现有终态判断逻辑回退。

**步骤 4：** 组件卸载、任务结束和总超时时清理 EventSource；用户取消后保留流直至收到 `cancelled` 或 GET 回退确认，不能提前把取消视为成功。

**步骤 5：** 确保 `BusinessBidWorkspace` 无需改动，因其已经调用共享 `pipeline.runTask`。

**步骤 6：** 运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run build
```

**预期：** TypeScript 构建通过，技术标和商务标共用同一 SSE 逻辑。

## 任务 4：联调、Grok 复审与交接

**文件：**

- 修改：`docs/HANDOFF-next.md`
- 修改：`docs/integration-checklist.md`

**步骤 1：** 启动 `Start-Biaoshu-Dev.bat`，技术标运行 parse 或 analyze，确认进度无需一秒轮询也会更新。

**步骤 2：** 商务标运行一项 `biz_*` 任务，确认进度、成功回填和取消按钮不回归。

**步骤 3：** 停止或阻断 SSE 流，确认前端改走 GET 回退；服务器恢复后任务仍可查到终态。

**步骤 4：** 运行后端全量测试和前端构建：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python -m pytest -q

cd ..\frontend
npm run build
```

**步骤 5：** 将 `git diff`、测试输出和手工联调结果交给 Grok 做只读审查；Codex 只采纳可复现的并发、资源释放、取消、兼容或安全问题。

**步骤 6：** 更新 `docs/HANDOFF-next.md` 的已完成/未完成项、测试基线和注释齐备表，并把 SSE 手工验收写入 `docs/integration-checklist.md`。

**验收：** 不提交 `.env`、真实 Key、数据库、上传文件或本机绝对路径；本轮新增或大改文件全部满足四字段注释要求。

---

## 执行结果（2026-07-10）

- Codex 完成单任务 SSE 路由、独立短 Session 快照读取、终态关流和共用 `useProjectPipeline` 的 SSE 优先/GET 回退实现；技术标与商务标复用同一 hook。
- Grok 完成协议审查与两轮差异审查。它发现项目切换旧流、取消后滞后 active 帧以及取消请求跨项目写回 UI 的竞态；均已通过项目会话、任务序号、终态单调性和当前项目/任务 id 守卫修复。最终复核结论为“未发现阻塞问题”。
- 新增 `backend/tests/test_task_sse.py`，覆盖终态快照、已取消快照、动态心跳/成功、连接中取消和 404；pytest 夹具改用忽略的文件型 SQLite，避免内存库 `StaticPool` 在后台线程与请求并发时竞争同一连接。
- 自动化验收：`backend/.venv/Scripts/python -m pytest -q` 为 **56 passed, 1 warning**；`frontend npm run build` 通过；`git diff --check` 通过。
- 浏览器联调：无用户 API Key 的 Markdown 解析任务可由 SSE 更新至 `success / 100%`，控制台无应用错误；临时阻断 `/events` 后，记录到先请求 SSE、再请求单任务 GET，页面仍成功完成，证明回退链路有效。联调临时项目均已删除，网络阻断已恢复。
- 边界保持不变：v1 仅支持默认工作空间；不引入 Redis、Celery、WebSocket、项目级总线、事件游标或持久重放。多工作空间鉴权与断点续传留给后续独立立项。
