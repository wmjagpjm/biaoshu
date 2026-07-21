# P13-I1 项目任务事件游标后端实施计划

> 执行要求：Grok 必须先运行真实 failure-first，再按白名单实现并自测；Codex 独立审查、双确认返修和最终验收。
> 状态：契约冻结，待实现
> 契约：`docs/p13i1-project-task-event-cursor-backend-contract.md`
> 分支：`collab/grok-code-codex-review`

## 1. 目标

在不改变既有单任务 SSE 的前提下，为项目任务状态建立独立事件表、真实任务写链事务钩子和严格游标 GET。事件只投影 taskId、taskType、status、progress 和 UTC 时间，不携带任务文本或结果。

## 2. 实施顺序

1. 只创建 `backend/tests/test_p13i1_project_task_events.py`，使用真实 SQLite、认证会话、项目和任务服务做 failure-first；不得先创建生产实现。
2. 在 `entities.py` 与 `models/__init__.py` 增加 `project_task_events`，只用 `create_all` 建新表，不改旧表迁移。
3. 在 `task_service.py` 增加窄范围 `_record_task_event`，由创建、进度/状态更新、取消、失败和进程中断真实写点调用；事件与任务更新共享 Session，不得在 helper 内提交或回滚。
4. 新建 `project_task_event_service.py`，严格解析 `after/limit`，按 `(occurred_at,id)` 实现 bootstrap tip、连续分页、stale 409 和 200 条裁剪读取。
5. 在 `schemas.py`、新路由和 `main.py` 注册精确 GET；沿用现有 required 活动 workspace + strict `bid_writer` 门控，但不复用单任务 SSE 的 request-scope 长连接代码。
6. 只运行专项、任务 SSE/认证代表回归、`compileall` 和 `git diff --check`；禁止后端全量、并发 pytest、整仓 E2E。

## 3. 预期命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
pytest -q tests/test_p13i1_project_task_events.py
pytest -q tests/test_task_sse.py tests/test_p13a_task_sse_workspace_auth.py
python -m compileall -q app tests/test_p13i1_project_task_events.py
cd ..
git diff --check
```

## 4. Codex 审查门

- 核对严格八文件白名单、事件实体字段和索引，无敏感任务字段。
- 核对创建/状态/进度/取消/失败/中断真实写链和同事务零残留，旧 worker 不得污染取消。
- 核对状态/进度去重、200 条裁剪、bootstrap tip、连续游标和 stale 409。
- 核对 required、活动 workspace、strict `bid_writer`、任意 `X-Workspace-Id`、跨项目和 no-store。
- 发现疑似问题先发只读 question；双方明确确认后才发返修授权；确认前不改实现。
- 通过后精确暂存八文件，中文提交并推送，再更新交接、路线图和联调清单。
