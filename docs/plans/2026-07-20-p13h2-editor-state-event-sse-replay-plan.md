# P13-H2 editor-state 事件 SSE 与断线重放实施计划

> **执行要求：** Grok 必须使用 `executing-plans` 工作流逐项执行，先红后绿；Codex 独立审查、验收和提交。

**目标：** 在 P13-H1 项目事件账本上提供严格 Cookie 鉴权、Last-Event-ID 重放和无历史回放的项目级 SSE。

**架构：** 复用 H1 路由和查询服务，不修改任务 SSE。连接前短 Session 固定项目与游标水位；生成器在线程池内逐轮创建短 Session，按 H1 `(occurred_at,id)` 顺序排空保留事件，并输出带 `id:` 的 cursor/editor-state 帧。

**技术栈：** FastAPI `StreamingResponse`、SQLAlchemy、SQLite、SSE、pytest。

**契约：** `docs/p13h2-editor-state-event-sse-replay-contract.md`。

---

## 1. 冻结与边界

基线=`7e5e02e`，分支固定 `collab/grok-code-codex-review`。严格三文件：两个既有 H1 生产文件和一个新专项测试；禁止修改 main/schema/实体/transition、任务 SSE、认证公共层、前端或其它测试。Grok 先只创建新专项做 failure-first，发送真实 status 后才可修改生产。

## 2. 任务一：failure-first SSE 专项

**文件：** 仅创建 `backend/tests/test_p13h2_editor_state_event_stream.py`。

1. 写 SSE 解析器，必须保留每帧 `id/event/data/comment`，精确校验同一 eventId 与 UTF-8 JSON，不能只抽取 data。
2. 通过真实 H1 transition/浏览器 PUT 产生事件；不得直接插入 `editor_state_events` 作为主要成功证据。
3. 覆盖已有 tip 的 cursor 锚点、空表首事件、真实 after 顺序、Last-Event-ID 重连、51 条跨页、心跳与 11 分钟关闭的可控时钟/常量。
4. 覆盖连接前 stale 409、连接中 stale 控制帧、短 Session 关闭、required/角色/活动空间/头/跨项目/请求语法精确状态与隐私零泄漏。
5. 运行新专项并记录真实 failed/passed、首个业务失败、生产两文件 SHA-256 与空暂存；不得把已有 H1 路由 404/405 等基线通过冒充新能力通过。

运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.venv\Scripts\python.exe -m pytest -q tests/test_p13h2_editor_state_event_stream.py --tb=short
```

## 3. 任务二：流式事件页服务原语

**文件：** 修改 `backend/app/services/editor_state_event_service.py`。

1. 抽取/复用严格 cursor 定位、`(occurred_at,id)` 正序和精确四键投影，保持 H1 `list_editor_state_events` 行为与响应不变。
2. 新增仅供 H2 短 Session 调用的流页函数：有 after 时返回其后最多 50 条；after 为 `None` 仅表示路由已预检为空后的起始页，按最早保留事件读取。
3. 每页返回 items/has_more；调用方以最后实际发送 eventId 推进水位，不能依赖 H1 在末页固定为 null 的 next_cursor。
4. 未知/裁剪/跨项目 cursor 继续统一 `EditorStateEventError(409)`；脏来源/内部异常固定脱敏，不从修订表补洞。

## 4. 任务三：严格 SSE 路由

**文件：** 修改 `backend/app/api/editor_state_events.py`。

1. 保持 H1 GET 原路由行为不变；新增 `/stream`，复用 required 活动 workspace strict bid_writer 依赖，但不得捕获 request-scope DB。
2. 严格解析原始请求头：Last-Event-ID 缺失或唯一合法；重复、空、大小写/空白不合约固定 422。拒绝所有 query、非空 body 和任意 X-Workspace-Id。
3. 返回 StreamingResponse 前用 `SessionLocal` 短 Session 校验项目和 cursor 并关闭；无 header 时得到 tip 或空水位。
4. 无 header 且有 tip 时先输出精确 cursor 锚点；随后每轮 `run_in_threadpool` 新建/关闭 Session，连续排空 50 条页面，空闲才 sleep。
5. editor-state 帧固定 `id/event/data`；心跳仅注释；连接中 stale/unavailable 固定无 id 控制帧并关闭。最大时限到达安静关闭。
6. 响应头固定 `text/event-stream; charset=utf-8`、`Cache-Control: no-cache, no-store`、`X-Accel-Buffering: no`；禁止 Cookie/CSRF、项目/空间/任务或异常原文进入帧。

## 5. 任务四：串行自测与反假绿

按顺序运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.venv\Scripts\python.exe -m pytest -q tests/test_p13h2_editor_state_event_stream.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_p13h1_editor_state_events.py tests/test_p13a_task_sse_workspace_auth.py tests/test_task_sse.py --tb=short
.venv\Scripts\python.exe -m compileall -q app tests/test_p13h2_editor_state_event_stream.py
cd ..
git diff --check
```

禁止并发 pytest、xdist、后端全量、前端或整仓 E2E。Grok 必须报告每组真实数字和耗时、严格三文件哈希、空暂存、未运行项与风险；不得提交或推送。

## 6. Codex 独立审查与提交门

1. 核对三文件白名单、H1 GET 行为不变、任务 SSE 零修改和 request-scope Session 未进入生成器。
2. 核对首次 cursor 锚点确实建立浏览器 Last-Event-ID，且不是历史 editor-state 回放；空表首事件不丢。
3. 核对积压跨页、同时间 ID 次序、断线重放、每发一条才推进水位和 stale 两阶段语义。
4. 核对 HTTP 401/403/404/409/422/405 精确断言，SSE 帧 exact shape、心跳无 id、所有错误脱敏且 no-store。
5. 检查测试不使用直接事件插表、宽状态、仅计数、源码扫描、无意义 sleep、skip/xfail 或 monkeypatch 掉核心行为。
6. 疑似问题先发只读 question，Grok 独立确认后才可下发最小返修授权；确认前禁止修改。
7. 通过后精确暂存三文件，中文提交并推送；再更新契约、计划、交接、路线图和联调清单。
