# P13-I2 项目任务事件 SSE 与断线重放实施计划

> 执行要求：Grok 先运行真实 failure-first，再按三文件白名单实现并自测；Codex 独立审查、双确认返修和最终验收。
> 状态：已完成只读审计与契约冻结，待 Grok failure-first
> 契约：`docs/p13i2-project-task-event-sse-replay-contract.md`
> 分支：`collab/grok-code-codex-review`

## 1. 目标

在不改变 P13-I1 游标 GET 和既有单任务 SSE 的前提下，为项目任务事件账本增加严格 SSE、`Last-Event-ID` 重放和连接中 stale 控制帧。事件只投影 I1 已公开六键，不产生任何写入或正文结果。

## 2. 实施顺序

1. 只创建 `backend/tests/test_p13i2_project_task_event_stream.py`，使用真实 SQLite、认证会话、项目和 `task_service` 写链做 failure-first；生产两文件未改前不得先改实现。
2. 在 `project_task_event_service.py` 增加短 Session 流页和连接前预检，复用 I1 的 workspace/project 作用域、游标校验、排序和 200 条保留窗口；不得 commit/rollback/写表。
3. 在 `project_task_events.py` 增加 `/api/projects/{projectId}/task-events/stream`，严格解析 query/body/重复 `Last-Event-ID`，连接前和流内均使用短 Session，帧和错误完全脱敏。
4. 新专项真实覆盖历史锚点、连续重放、空水位、51 条跨页、断线后续、裁剪 stale、鉴权、Session 生命周期、隐私和断开/超时。
5. 只运行新专项、I1 专项、单任务 SSE/P13-A、P13-H1/H2、认证代表回归、`compileall` 和 `git diff --check`；禁止后端全量、并发 pytest、xdist、前端或整仓 E2E。

## 3. Codex 审查门

- 核对严格三文件白名单；实体、Schema、`main.py`、I1 写链、认证公共层和既有任务 SSE 必须零差异。
- 核对事件只从真实 I1 `task_service` 写链产生；测试不得直接插入事件表冒充主要成功证据。
- 核对首次 tip 锚点不回放历史、空表首事件不丢、`Last-Event-ID` 正序重放和跨页无重复。
- 核对连接前/流内短 Session 关闭、workspace/project 双谓词、stale/unavailable 控制帧和 11 分钟安静关闭。
- 核对 six-key data、no-store/no-cache、无敏感字段和所有认证/请求错误脱敏。
- 发现疑似问题先发只读 question；双方确认后才发新的返修 task；确认前不改实现。
- 通过后精确暂存三文件，中文提交并推送，再更新交接、路线图和联调清单。

## 4. 预期命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
pytest -q tests/test_p13i2_project_task_event_stream.py
pytest -q tests/test_p13i1_project_task_events.py
pytest -q tests/test_task_sse.py tests/test_p13a_task_sse_workspace_auth.py
pytest -q tests/test_p13h1_editor_state_events.py tests/test_p13h2_editor_state_event_stream.py
pytest -q tests/test_auth_rbac.py
python -m compileall -q app tests/test_p13i2_project_task_event_stream.py
cd ..
git diff --check
```

## 5. 当前冻结记录

只读审计确认：I1 端点已注册于 `main.py`，无需修改入口；既有单任务 SSE 输出完整任务快照，不能复用；H2 已验证短 Session、cursor 锚点和 stale 控制帧模式可参考但不得改动 H2 文件。当前唯一待执行动作是先发送三文件 failure-first 任务，认证成功后由 Grok 实现并自测。
