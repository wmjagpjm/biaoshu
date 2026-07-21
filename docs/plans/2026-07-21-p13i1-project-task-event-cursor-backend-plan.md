# P13-I1 项目任务事件游标后端实施计划

> 执行要求：Grok 必须先运行真实 failure-first，再按白名单实现并自测；Codex 独立审查、双确认返修和最终验收。
> 状态：两轮实现审查完成；认证错误 no-store 与三项反假绿缺口已双确认，十一文件范围修订待 Grok 返修
> 契约：`docs/p13i1-project-task-event-cursor-backend-contract.md`
> 分支：`collab/grok-code-codex-review`

## 1. 目标

在不改变既有单任务 SSE 的前提下，为项目任务状态建立独立事件表、真实任务写链事务钩子和严格游标 GET。事件只投影 taskId、taskType、status、progress 和 UTC 时间，不携带任务文本或结果。

## 2. 实施顺序

1. 只创建 `backend/tests/test_p13i1_project_task_events.py`，使用真实 SQLite、认证会话、项目和任务服务做 failure-first；扩围返修也必须先增加两条真实回传失败用例，不得先修改新增生产文件。
2. 在 `entities.py` 与 `models/__init__.py` 增加 `project_task_events`，只用 `create_all` 建新表，不改旧表迁移。
3. 在 `task_service.py` 增加窄范围 `_record_task_event`，由创建、进度/状态更新、取消、失败和进程中断真实写点调用；事件与任务更新共享 Session，不得在 helper 内提交或回滚。
4. 在 `parse_callback.py` 与 `local_parser_ticket_service.py` 复用同一无提交事件辅助，在各自直接创建 `success/100` 任务后、调用方唯一 commit 前写一条同事务事件；不得补造 `pending/running`。
5. 新建 `project_task_event_service.py`，严格解析 `after/limit`，按 `(occurred_at,id)` 实现 bootstrap tip、连续分页、stale 409 和 200 条裁剪读取。
6. 在 `schemas.py`、新路由和 `main.py` 注册精确 GET；沿用现有 required 活动 workspace + strict `bid_writer` 门控，但不复用单任务 SSE 的 request-scope 长连接代码。
7. 在 `auth_middleware.py` 的统一 `_error_response` 上固定 `Cache-Control: no-store`，只补响应头，不修改公开路径、认证、会话、CSRF 或错误体语义。
8. 专项必须真实覆盖已裁剪游标、第二 workspace 项目/游标、两条回传各自的事件 flush 与最终 commit 故障；禁止条件 no-store、同空间跨项目冒充跨空间或仅函数名声称 commit。
9. 只运行专项、个人 callback、一次性票据、认证、任务 SSE/认证代表回归、`compileall` 和 `git diff --check`；禁止后端全量、并发 pytest、整仓 E2E。

## 3. 预期命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
pytest -q tests/test_p13i1_project_task_events.py
pytest -q tests/test_async_and_callback.py tests/test_local_parser_callback_tickets.py
pytest -q tests/test_auth_rbac.py
pytest -q tests/test_task_sse.py tests/test_p13a_task_sse_workspace_auth.py
python -m compileall -q app tests/test_p13i1_project_task_events.py
cd ..
git diff --check
```

## 4. Codex 审查门

- 核对严格十一文件白名单、事件实体字段和索引，无敏感任务字段；认证中间件只改统一错误响应头。
- 核对创建/状态/进度/取消/失败/中断及两条直接终态回传真实写链和同事务零残留，旧 worker 不得污染取消。
- 核对状态/进度去重、200 条裁剪、bootstrap tip、连续游标和 stale 409。
- 核对 required、活动 workspace、strict `bid_writer`、任意 `X-Workspace-Id`、跨项目和 no-store。
- 核对真实裁剪游标、真实第二 workspace、未登录无条件 no-store，以及两条回传的 flush/commit 双故障证据。
- 发现疑似问题先发只读 question；双方明确确认后才发返修授权；确认前不改实现。
- 通过后精确暂存十一文件，中文提交并推送，再更新交接、路线图和联调清单。

## 5. 审查与范围修订记录

- Grok 初始 task=`msg_30f8314af94745a4913e656ea56999e7`，failure-first=`msg_d146ec5ed8eb4275a67f5277aea9aac8`，真实 **17 failed / 0 passed**。
- Grok 首轮 review=`msg_3b59ab461ad5422e9907c84b0448e6ae`：专项 **17 passed**，单任务 SSE/P13-A **18 passed**；Codex 独立复跑结果相同，compileall 与 diff-check 通过。
- Codex 只读问题=`msg_c60ffa9f89334940b6ab39eee85fb5c1`，Grok 确认=`msg_c4b13a10f3ec490f983a0c03f4f9d262`：个人 callback 与一次性票据两条真实 `success/100` 任务创建链均漏写事件，双方确认属于契约缺口。
- 首次范围从八文件扩大为十文件；直接终态任务只记录一条 `success/100`，并补真实写链及事务回滚证据，不修改其它生产文件或既有测试。
- 第二轮 Codex question=`msg_85b188d2dabe4f12be6c62d3dde1850f`，Grok 确认=`msg_0e9da620244d4ead96a46f815131306c`：未登录 401 缺 no-store、已裁剪游标、真实跨 workspace、两条最终 commit 故障四项均确认存在。
- 第二次范围仅从十文件扩大为十一文件，新增 `auth_middleware.py` 的统一错误响应头；其它三项仅补原专项真实证据，不扩大生产范围。
