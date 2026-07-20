# P13-H1 editor-state 事件账本与游标后端实施计划

> 执行要求：Grok 必须使用 `executing-plans` 工作流逐项执行，先红后绿；Codex 独立审查、验收和提交。
>
> 完成状态：已实现并通过 Codex 独立验收；两项审查问题经 Grok 只读确认后完成最小返修，待本次提交推送。

**目标：** 在九类真实 editor-state 写链的同一事务中记录脱敏事件，并提供严格、可裁剪、可检测游标失效的只读 API。

**架构：** 新增独立 `editor_state_events` 表，不复用可删除/固定的修订历史。由 `record_editor_state_transition` 只在真实 after 修订插入时追加一条事件并裁剪；新增 required strict bid_writer 项目级 GET 读取事件，暂不实现 SSE 或前端。

**技术栈：** FastAPI、SQLAlchemy、SQLite、Pydantic、pytest。

**契约：** `docs/p13h1-editor-state-event-cursor-backend-contract.md`。

---

## 1. 冻结与边界

基线=`83c2c4a`，分支固定 `collab/grok-code-codex-review`。严格八文件：七个生产文件和一个新后端专项测试，禁止扩围。Grok 先只写新测试做 failure-first，再实现；不得写文档、Git 或清理工件。

## 2. 任务一：failure-first 事件与 API 测试

**文件：** 仅创建 `backend/tests/test_p13h1_editor_state_events.py`。

1. 使用真实 SQLite、认证会话、项目和既有 editor-state 写服务，不直接插入事件表作为成功证据。
2. 覆盖九类来源中至少浏览器 PUT、task、revise、local_parser、content_fuse、checkpoint/revision restore 的真实 after 事件；覆盖 before 补账、同版本和异常回滚零事件。
3. 覆盖 API 精确四键、时间/版本/来源边界、limit 1/50、空结果、200 条裁剪、游标连续读取和 stale 409。
4. 覆盖未登录、非 bid_writer、非活动 workspace、任意 `X-Workspace-Id`、跨项目、未知 query/body 与隐私零泄漏。
5. 先运行新测试并记录真实失败数字、首个业务失败和生产七文件不存在/哈希基线；不得创建其余生产文件。

运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
pytest -q tests/test_p13h1_editor_state_events.py
```

## 3. 任务二：事件 ORM 与导出

**文件：** `backend/app/models/entities.py`、`backend/app/models/__init__.py`。

1. 新增固定字段、CHECK/索引和级联关系；ID 使用 `ese_` + 32 位小写十六进制。
2. 保持无快照、无 actor/client 字段；由 `Base.metadata.create_all` 创建新表，禁止修改旧表迁移。
3. 导出实体并让 `main.py` 可注册元数据；验证重复启动幂等。

## 4. 任务三：事务事件写入与裁剪

**文件：** `backend/app/services/editor_state_revision_service.py`。

1. 增加独立事件 ID、严格 source 校验和事件模型写入辅助函数。
2. 仅在真实 after 修订插入后写一条事件；before 补账、同版本和异常不写事件。
3. 事件写入与现有修订裁剪共享调用方 Session；事件每项目最多 200 条、按时间和 ID 连续删除旧前缀。
4. 任何事件 flush/裁剪失败都抛固定内部错误，让调用方原事务回滚；不 commit/rollback/refresh。

## 5. 任务四：只读事件查询服务

**文件：** 新建 `backend/app/services/editor_state_event_service.py`。

1. 严格解析 `after` 格式和 `limit` 1..50，禁止 query/body 原文进入异常。
2. 在 workspace/project 三重作用域下按 `(occurred_at,id)` 正序查询，使用 `limit+1` 判断 `hasMore`。
3. 游标不存在、已删除、跨项目或跨空间统一 `editor_state_event_cursor_stale`；不得从修订表补洞。
4. 无 `after` 时不回放历史；已有事件则仅返回最新 tip 作为 bootstrap `nextCursor`，无事件时为 `null`。普通 `after` 分页仍只在 `hasMore=true` 时返回页尾游标。
5. 返回只含 eventId/stateVersion/sourceKind/occurredAt 和 nextCursor/hasMore，禁止快照、actor、项目/空间 ID。

## 6. 任务五：API schema、路由与注册

**文件：** `backend/app/api/schemas.py`、新建 `backend/app/api/editor_state_events.py`、`backend/app/main.py`。

1. 路由只接受 GET；使用专用 required strict bid_writer 依赖，任何 `X-Workspace-Id` 固定 403，未登录/非角色固定脱敏。
2. 项目不存在或跨空间固定 404；query 未知参数、重复参数、非法 limit/after、body 均固定 422，不回显输入。
3. 成功/业务错误设置 `Cache-Control: no-store`，响应模型禁止额外键；注册到 `/api` 下 `/projects/{projectId}/editor-state-events`。

## 7. 任务六：串行自测与反假绿

按顺序运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
pytest -q tests/test_p13h1_editor_state_events.py
pytest -q tests/test_editor_state_revisions.py tests/test_p13d1_revision_actor_ledger.py
python -m compileall -q app tests/test_p13h1_editor_state_events.py
cd ..
git diff --check
```

禁止并发 pytest、xdist、后端全量、整仓 E2E 或无关前端测试。Codex 后续独立复跑新专项、受影响 editor-state/认证代表节点，必要时再运行精确 `py_compile`。

## 8. Codex 独立审查与提交门

1. 核对严格八文件、事件表/修订表边界和失败事务零写证据。
2. 核对真实 after 事件数量、before 补账不产事件、200 裁剪和 stale cursor 语义。
3. 核对 required strict bid_writer、活动 workspace、`X-Workspace-Id`、跨项目和脱敏错误。
4. 核对响应精确键、no-store、无快照/actor/client/内部 ID 泄漏；检查测试无直接事件表造假、宽状态或恒真断言。
5. 疑似问题先发只读 question，Grok 独立确认后才可下发返修授权；确认前禁止修改。
6. 通过后精确暂存八文件，中文提交并推送；随后更新交接、路线图和联调清单。

## 9. 完成记录

failure-first=`msg_ee84a231060941049177cce0f05f501a`，真实 **25 failed / 3 passed**。Grok 初版 review=`msg_4ce83deca5954672951a2337f73d4de2`，专项/回归 **28/90 passed**。Codex 只读问题=`msg_83b6e26440c44662a84e91767747e0c4`，Grok 确认=`msg_532b006202a4472a99c8220fa0a8a618`；双方确认公开 API 缺少 bootstrap tip、未登录和非 GET 使用宽状态断言两项问题真实存在后，才授权最小返修 `msg_695b8f5301a44e0d9d4132c1d6a4ca7b`。

最终 Grok review=`msg_80212cd30e6546f3b651a2ddb0ad7510`。Codex 独立串行通过专项 **28 passed**、`editor_state_revisions`/P13-D1 回归 **90 passed**、`compileall` 和 `git diff --check`；仅有既有 Starlette/httpx 弃用警告。未运行后端全量、前端、整仓 E2E、xdist 或并发 pytest。
