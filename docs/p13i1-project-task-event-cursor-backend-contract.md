# P13-I1 项目任务事件游标后端契约

> 状态：两轮实现审查完成；认证错误 no-store 与三项反假绿缺口已双确认，十一文件范围修订待返修
> 日期：2026-07-21
> 前置：P13-A 任务 SSE 工作空间鉴权、P13-H1/H2 editor-state 事件账本与 SSE
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`

## 1. 目标与诚实语义

为项目内多个异步任务提供持久、脱敏、可游标读取的状态事件基础。事件只表达某任务公开状态发生了变化，不包含任务结果、错误原文、请求 payload、正文、文件内容、actor、clientId 或内部异常。

本包不是 SSE，不改造现有 `GET /projects/{projectId}/tasks/{taskId}/events`，不提供前端总线、不做 WebSocket、通知、评论、审批或跨项目事件。后续包才能在本包只读 API 之上接项目级 SSE 与断线重放。

## 2. 只读审计结论

1. 现有单任务 SSE 只读取一个 `project_tasks` 行，事件由快照签名临时推导，没有历史游标，不能复用为多任务总线。
2. `task_service` 的创建、进度、成功、失败、取消和进程中断写点均使用独立 Session；事件必须在同一事务中随任务状态提交，不能靠定时扫描补事件。
3. `project_tasks` 会被任务查询和未来清理复用，不能把任务行当事件日志；事件必须独立表并有项目级保留上限。
4. 任务 `message/error/result/payload` 可能含文件名、路径、模型或业务正文，事件 API 只允许固定状态元数据。
5. 生产代码另有两条绕过 `task_service` 的真实任务创建链：个人 `parse_callback` 与一次性票据 `local_parser_ticket_service` 均直接创建 `success/100` 的 parse 任务并对外返回 taskId；若不在同一事务写事件，项目任务总线会永久漏掉这些任务。
6. required 模式未登录请求在路由前由 `AuthMiddleware` 返回 401；公共 `_error_response` 若不设置 no-store，路由内的响应头逻辑不可达，因此必须把该统一认证错误出口纳入本包最小范围。

## 3. 严格作用域

- 仅 `AUTH_MODE=required`、当前活动 workspace、活动成员角色精确 `bid_writer` 可读。
- 任意 `X-Workspace-Id`（含空值）固定拒绝；workspace 只能来自认证主体的活动空间。
- 项目必须属于当前 workspace；不存在、跨空间和非法项目统一固定 404，不回显项目 ID。
- 路由只接受 GET，不接受 body、Cookie 令牌、URL token、外网 URL、浏览器存储或任务结果查询替代。

## 4. 事件表与事务

新增 `project_task_events`，字段固定：

| 字段 | 语义 |
| --- | --- |
| `id` | 服务端生成不透明 `pte_` + 32 位小写十六进制游标 ID |
| `workspace_id` | 作用域，非空并建索引 |
| `project_id` | 作用域，非空并建索引 |
| `task_id` | 任务标识，非空并建索引；只允许同项目任务 |
| `task_type` | 任务类型固定字符串；不接受客户端新增类型 |
| `status` | `pending|running|success|failed|cancelled` |
| `progress` | 0..100 整数 |
| `occurred_at` | 服务端 UTC 时间 |

约束与索引：

- `id` 主键；`workspace_id/project_id/task_id` 非空；复合索引 `(workspace_id, project_id, occurred_at, id)`。
- 不存 `message`、`error`、`result_json`、`payload_json`、正文、文件名、actor_user_id、clientId、URL 或异常原文。
- 每项目最多保留最近 200 条事件，按 `occurred_at DESC, id DESC` 连续裁剪；事件写入、任务更新和裁剪共用调用方 Session，任一 flush/commit 失败整事务回滚。
- 不回填上线前任务历史；旧任务只能通过既有任务 GET/SSE 查询，不伪造事件。

写入规则固定为：

1. `create_task_record` 为真实新任务写入一条 `pending` 事件。
2. 个人 `parse_callback` 与一次性票据回传直接创建终态任务时，只诚实写入一条 `success/100` 事件；不得伪造从未发生的 `pending` 或 `running` 历史。
3. 任务公开状态或进度真实变化时写入一条事件；完全相同的 `(status, progress)` 不重复写入。
4. `message`、`error`、`result_json` 单独变化不产生事件，避免把敏感文本带入总线。
5. `cancel_task`、失败终态、版本冲突失败和启动时进程中断必须使用同一事件辅助函数；取消后的 worker 迟到提交不得追加非取消事件。
6. 事件不得自行 `commit/rollback/refresh`，由任务状态调用方统一控制事务；测试不得直接插入事件作为成功证据。

## 5. 只读 API

新增：`GET /api/projects/{projectId}/task-events`。

查询参数仅允许：

- `after`：可空不透明事件 ID；缺失表示从当前最新位置开始，不回放旧历史。
- `limit`：可选整数，默认 50，范围 1..50；非法、重复或未知参数固定脱敏 422。

成功响应必须精确四个顶层键：

```json
{
  "items": [
    {
      "eventId": "pte_<32位小写十六进制>",
      "taskId": "task_<不透明标识>",
      "taskType": "parse",
      "status": "running",
      "progress": 50,
      "occurredAt": "2026-07-21T12:34:56.000Z"
    }
  ],
  "nextCursor": "pte_<32位小写十六进制>",
  "hasMore": false
}
```

- `items` 按 `(occurred_at ASC, id ASC)` 返回，最多 `limit` 条；无结果返回空数组、`nextCursor=null`、`hasMore=false`。
- `after` 指向已保留事件时只返回其后的事件；指向已裁剪、未知、跨项目或跨 workspace 的事件固定返回脱敏 409 `project_task_event_cursor_stale`，不得猜测位置或从任务表补洞。
- 无 `after` 且已有事件时返回空 `items`、`hasMore=false`，并把当前最新事件 ID 作为 bootstrap `nextCursor`；有 `after` 时只有 `hasMore=true` 才返回页尾游标。
- 成功和业务错误均 `Cache-Control: no-store`；包括 `AuthMiddleware` 在路由前返回的 401/403/503 认证错误，禁止用条件断言豁免缺失响应头；禁止返回项目/空间内部 ID、actor、client、message、error、result、payload 或 SQL/异常原文。
- 不支持 POST/PUT/PATCH/DELETE、SSE、`Last-Event-ID`、WebSocket 或 query token。

## 6. 失败优先与验收

新后端专项必须在无生产实现时真实失败，至少覆盖：

1. 创建、进度、成功、失败、取消、版本冲突失败和进程中断真实写链各产生预期事件；相同状态/进度不重复，旧 worker 不污染取消终态。
2. 个人 `parse_callback` 与一次性票据公开回传必须通过真实 HTTP/服务写链各产生且只产生一条 `success/100` 事件；响应 taskId 与事件 taskId 精确一致，禁止测试直接插入事件冒充覆盖。
3. 事件与任务更新、200 条裁剪绑定同一事务；两条直接终态回传必须分别覆盖事件 flush 故障与最终 commit 故障，commit 钩子内先证明对应事件已进入同一 Session，失败后任务、事件及同事务业务写入均不残留；票据仍可重试。
4. 精确响应键、字段格式、顺序、limit 1/50、空结果和连续分页；首次 bootstrap tip 不回放旧历史。
5. 游标指向保留事件可继续读取；必须保存真实早期事件 ID、触发 200 条裁剪并确认该行已删除后，再证明裁剪游标固定 409；伪造和跨项目游标同样固定 409 且不回显。
6. 必须创建真实第二 workspace、第二空间项目并通过 `task_service` 产生事件；活动空间 A 查询 B 项目固定 404，A 项目使用 B 游标固定 409，均不回显输入。
7. 未登录、非 `bid_writer`、非活动 workspace、任意 workspace 头、跨项目统一固定拒绝；未登录 401 必须无条件断言 no-store。
8. 响应无 message/error/result/payload/actor/client/异常原文，成功和错误均 no-store。
9. 既有个人 callback、一次性票据、认证、单任务 SSE、任务创建/取消、P13-H1/H2、P13-F1/F2、P13-G1/G2 代表回归保持通过。

严格禁止宽状态断言、仅非零计数、假事务、测试直接写事件表、绕过真实任务写点、把 `project_tasks` 当事件日志或把敏感任务快照投影到事件。

## 7. 严格十一文件白名单

1. `backend/app/models/entities.py`：新增事件实体。
2. `backend/app/models/__init__.py`：导出实体。
3. `backend/app/services/task_service.py`：真实任务状态事务事件写入与裁剪钩子。
4. `backend/app/api/parse_callback.py`：个人兼容回传直接终态任务的同事务事件写入。
5. `backend/app/services/local_parser_ticket_service.py`：一次性票据回传直接终态任务的同事务事件写入。
6. `backend/app/services/project_task_event_service.py`：严格游标查询服务。
7. `backend/app/api/schemas.py`：新增精确响应模型。
8. `backend/app/api/auth_middleware.py`：统一认证错误响应固定 `Cache-Control: no-store`。
9. `backend/app/api/project_task_events.py`：只读路由、参数和脱敏错误映射。
10. `backend/app/main.py`：注册实体与路由。
11. `backend/tests/test_p13i1_project_task_events.py`：failure-first、真实回传、事务、作用域、游标和隐私专项。

禁止修改前端、共享 `api.py`、认证路由与服务、既有单任务 SSE 路由、配置、依赖、迁移脚本和其它测试；`auth_middleware.py` 仅允许为统一 `_error_response` 增加 no-store，不得改公开路径、认证、会话或 CSRF 语义。Grok 不得写文档、暂存、提交、推送或清理产物。

## 8. 后续明确拆分

P13-I2 才能在本包只读 API 之上增加项目级 SSE、`Last-Event-ID` 和断线重放；前端任务总线、通知、评论审批、协同光标、WebSocket 和强制锁仍须另行冻结。
