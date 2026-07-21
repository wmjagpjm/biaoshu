# P13-I2 项目任务事件 SSE 与断线重放契约

> 状态：实现、双确认返修与 Codex 独立验收完成
> 日期：2026-07-21
> 前置：P13-I1 项目任务事件账本与游标 GET（功能=`f0d6d75`）
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`

## 1. 目标与诚实语义

在 P13-I1 的持久、脱敏任务事件账本之上增加项目级 SSE，使客户端能够接收同一项目后续任务状态事件，并用浏览器自动维护的 `Last-Event-ID` 重放仍在 200 条保留窗口内的事件。

本包只交付后端事件流，不自动刷新任务详情、不广播任务正文/结果/错误、不接前端任务总线、通知、评论审批、协同光标、WebSocket 或强制锁。现有单任务 `GET /projects/{projectId}/tasks/{taskId}/events` 语义保持不变。

## 2. 只读审计结论

1. I1 的 `project_task_events` 是唯一任务事件源，已按 `(occurred_at,id)` 提供正序游标、200 条裁剪和 stale 409；I2 不得从 `project_tasks` 补洞或重新推导事件。
2. 现有单任务 SSE 会返回完整任务快照、消息和结果，不满足任务总线的隐私边界；I2 必须使用独立端点和 I1 脱敏投影。
3. H2 已验证原生 `EventSource` 首次连接不能自定义 `Last-Event-ID`；I2 在无 header 且已有历史时必须发送公开 tip 的 `cursor` 锚点，但不得把旧任务事件冒充新变化回放。
4. 长连接不得捕获 request-scope `Session` 或 ORM 行；连接前用短 `SessionLocal` 预检，流内每轮在线程池中新建并关闭短 Session。

## 3. 端点与严格作用域

新增：`GET /api/projects/{projectId}/task-events/stream`。

- 仅 `AUTH_MODE=required`、认证主体当前活动 workspace、活动成员角色精确 `bid_writer` 可连接；owner 不替代角色。
- 任意 `X-Workspace-Id` 头（含空值）固定 403；workspace 只来自认证主体活动空间。
- 项目不存在、跨 workspace 或非法项目固定脱敏 404；Cookie 会话是唯一认证来源。
- 不允许任何 query 参数或非空 body；未知 query、重复/非法 `Last-Event-ID` 固定脱敏 422。
- `Last-Event-ID` 只能缺失或出现一次；非空值必须精确匹配 `pte_` + 32 位小写十六进制。伪造、已裁剪、跨项目或跨 workspace 游标统一固定 409 `project_task_event_cursor_stale`，不得回显原值。
- 只允许 GET；其它方法保持框架精确 405，不新增写路径或 CSRF 例外。

## 4. 连接水位与重放

连接前短 Session 固定完成项目与游标预检：

1. 有 `Last-Event-ID`：确认游标仍保留后，以该游标为水位，流启动后按 `(occurred_at,id)` 正序发送其后的事件。
2. 无 `Last-Event-ID` 且已有事件：不回放历史，以最新 tip 为水位，首先发送一次 `cursor` 锚点帧，让浏览器建立可自动回传的水位。
3. 无 `Last-Event-ID` 且事件表为空：从空水位等待；首条新事件必须作为 `task-event` 发送，不能被 bootstrap 吸收。

流内每页最多读取 50 条，积压时连续排空，空闲时每 250ms 轮询；每发一条成功事件才推进内存水位。若水位在连接中因 200 条裁剪而失效，发送固定 `cursor-stale` 控制帧并关闭；不得猜测缺口或从任务表补洞。

## 5. SSE 帧与响应头

首次已有历史但无 `Last-Event-ID` 时，锚点帧精确为：

```text
id: pte_<32位小写十六进制>
event: cursor
data: {"eventId":"pte_<同一ID>"}

```

每条真实任务事件精确为：

```text
id: pte_<32位小写十六进制>
event: task-event
data: {"eventId":"pte_<同一ID>","taskId":"task_<不透明标识>","taskType":"parse","status":"running","progress":50,"occurredAt":"2026-07-21T12:34:56.000Z"}

```

- `task-event` data 精确六键 `eventId/taskId/taskType/status/progress/occurredAt`；`id:` 必须与 data.eventId 相等。
- 空闲 15 秒发送注释心跳 `: heartbeat\n\n`，不得带 `id`、`event`、`data`、时间或身份字段。
- 连接中游标失效发送无 `id` 的 `cursor-stale`，data 只含固定 code/message；内部异常发送无 `id` 的固定 `unavailable` 控制帧并关闭。
- 响应必须是 `text/event-stream; charset=utf-8`，并带 `Cache-Control: no-cache, no-store`、`X-Accel-Buffering: no`。
- 单连接最多 11 分钟，安静关闭，让原生 EventSource 使用最后成功 id 自动重连；不得发送伪造事件 ID。

## 6. 事务、隐私与失败边界

- I2 全程只读，不新增表、迁移、事件写入、commit/rollback 或裁剪逻辑；事件仍只由 I1 真实任务写链产生。
- 每轮必须重新按 workspace/project 查询，不得只按事件主键读取或跨连接共享 Session。
- SSE 不返回消息、错误、结果、payload、正文、文件名、actor、user、workspace、project、client、Cookie、CSRF、SQL、路径或异常原文；`taskId` 仅作为 I1 已公开六键中的任务关联字段。
- 连接前错误必须是普通 HTTP 错误并带 `no-store`，不得先返回 200 再把预检错误藏在流中。
- I2 不承诺重放超出 200 条保留窗口的事件；stale 是显式重新同步信号，不得伪装无变化。

## 7. 严格三文件白名单

1. `backend/app/services/project_task_event_service.py`：增加流内短 Session 使用的保留事件页读取原语与 tip/游标预检。
2. `backend/app/api/project_task_events.py`：增加严格 SSE 路由、重复头解析、帧格式化、短 Session 轮询和控制帧。
3. `backend/tests/test_p13i2_project_task_event_stream.py`：failure-first、锚点、空水位、重放、裁剪 stale、鉴权、Session 生命周期和隐私专项。

禁止修改实体、Schema、`main.py`、I1 任务写链、现有单任务 SSE、认证公共层、中间件、前端、P13-F/G/H、依赖、配置、迁移脚本和其它测试。Grok 不得写文档、暂存、提交、推送或清理 Codex 工件。

## 8. 失败优先与验收

新专项必须在生产两文件未改时真实失败，至少覆盖：

1. 无 header 的已有历史只发一个 cursor 锚点，不回放旧任务事件；连接后真实任务写链按顺序发送。
2. 有 `Last-Event-ID` 精确重放其后仍保留事件；51 条以上积压跨页无丢失、重复或乱序，`id` 与六键 data.eventId 一致。
3. 空表连接后的首条真实事件会发送，不能被 bootstrap 吸收；注释心跳不改变水位。
4. 断线后以最后收到 ID 重连只收到后续事件；未知/裁剪/跨项目游标连接前固定 409，连接中 stale 固定控制帧后关闭。
5. required 未登录精确 401；非 bid_writer、非活动 workspace、任意 workspace 头精确 403；跨项目精确 404；非法请求精确 422；非 GET 精确 405。
6. 连接前和流内 Session 都关闭；不捕获 request-scope Session/ORM；短 Session 查询始终带 workspace/project。
7. 所有成功帧、心跳、控制帧和错误均无 I1 未公开字段、认证材料或内部异常泄漏。
8. I1 GET、P13-A/既有单任务 SSE、P13-H1/H2 代表回归保持通过；禁止并发 pytest、xdist、后端全量、前端或整仓 E2E。

严格禁止宽状态断言、仅非零计数、源码字符串冒充行为、直接插入事件表作为主要成功证据、睡眠后不校验顺序、跳过测试或把测试超时当成断线重放证据。

## 9. 后续明确拆分

I2 仍不提供前端 EventSource 接入、任务详情自动刷新、通知、评论审批、协同光标、WebSocket、强制锁或跨项目事件；这些能力须另行只读审计和冻结。

## 10. 实现与验收回执

- 冻结提交=`525d059`，功能提交=`03fb90e`，严格三文件未扩围。
- Grok failure-first=`msg_d83ad4841dab4cdb9b57ec4aaf6721a8`，真实 **15 failed / 0 passed**；初始实现 review_request=`msg_186855fc4b18450e89bf71162cae8279`。
- Codex 只读 question=`msg_e3f6751a53c14bb8b08e4bb32c713f1e`，Grok 确认=`msg_63b808eadc244154afdca692874a27f8`：连接中 stale、unavailable、request-scope `get_db` 反假绿和跨 workspace 游标四项均为真实验收证据缺口。双方确认后才授权 test-only 返修；控制帧唯一性再次经 `msg_8830175702d24e99955a1a2d8824f6ba` / `msg_be93334deb8846d6ae6a999796223b85` 确认并收紧。
- 返修属于既有生产语义的 evidence-only 补强，新增用例首次即绿，未制造红测；最终 Grok 回执=`msg_f4a26b03cfb04055ae2f09b6c449f441`。
- Codex 独立串行通过 I2 专项 **17 passed**、I1/单任务 SSE/P13-A/H1/H2/认证代表回归 **125 passed**，合计 **142 passed**；`compileall`、`git diff --check`、严格三文件边界与 SHA-256 门通过。未运行后端全量、xdist、前端或整仓 E2E。
