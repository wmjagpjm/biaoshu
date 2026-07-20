# P13-H2 editor-state 事件 SSE 与断线重放契约

> 状态：已只读审计并冻结，待 Grok failure-first 与受限实现
> 日期：2026-07-20
> 审计基线：`7e5e02efb9e4c460b5e71ab7a05f41290e8c35fb`
> 前置：P13-H1 事件账本与项目级 GET 游标（冻结=`da2537a`、实现=`4255823`）
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`

## 1. 目标与诚实语义

在 P13-H1 的持久、脱敏事件账本上增加项目级 SSE，使同一项目的其它客户端能收到 editor-state 版本变化，并能用浏览器自动维护的 `Last-Event-ID` 重放仍在 200 条保留窗口内的后续事件。

本包只交付后端事件流，不修改前端，不自动刷新 editor-state，不广播正文、章节、actor、clientId 或任务结果。它不是 WebSocket、多任务总线、协同光标、评论审批、通知或强制锁；P13-H3 才能接前端保守版本提示。

## 2. 只读审计结论

1. P13-H1 已把九类真实 after 版本变化写入独立 `editor_state_events`，并提供严格项目作用域、tip bootstrap、正序增量和 stale 409，可作为唯一事件源；禁止复用可删除修订历史。
2. 现有单任务 SSE 持有项目/任务语义、输出完整任务快照并在终态关闭，不能改造成项目 editor-state 总线；H2 必须使用 H1 路由与服务模块内的独立端点。
3. 原生 `EventSource` 首次连接不能由应用代码自定义 `Last-Event-ID`。若服务端只在内存中记录 tip 而不发送 `id:`，无事件连接关闭后重连可能丢失水位，因此首次已有历史时必须发送公开 tip 的 `cursor` 锚点帧，但不得把旧事件冒充新变化回放。
4. 长连接不得捕获 request-scope Session 或 ORM 行；连接前用短 Session 完成项目/游标预检，流内每轮在线程池中新建并关闭短 Session。

## 3. 严格作用域与请求

新增：`GET /api/projects/{projectId}/editor-state-events/stream`。

- 仅 `AUTH_MODE=required`、当前活动 workspace、活动成员角色精确 `bid_writer` 可连接；owner 不替代角色。
- 任意 `X-Workspace-Id`（含空值）固定 403；workspace 只来自认证 principal 的活动空间。
- 项目不存在、跨空间或非法项目固定脱敏 404；Cookie 会话是唯一认证来源，不接受 URL token。
- 不允许任何 query 参数或非空 body；未知 query、重复/非法 `Last-Event-ID`、非空 body 固定脱敏 422。
- `Last-Event-ID` 只能缺失或出现一次；非空值必须精确匹配 `ese_` + 32 位小写十六进制。伪造、已裁剪、跨项目或跨空间游标统一固定 409 `editor_state_event_cursor_stale`，不得回显原值。
- 只允许 GET；其它方法保持框架精确 405，不新增写路径或 CSRF 例外。

## 4. 连接水位与重放

连接前短 Session 固定完成：

1. 校验活动 workspace、项目归属与可选 `Last-Event-ID`。
2. 有 `Last-Event-ID`：保留该游标为水位，流启动后按 `(occurred_at,id)` 正序发送其后的所有仍保留事件。
3. 无 `Last-Event-ID` 且已有事件：不回放历史，以最新 tip 为水位，并首先发送一次 `cursor` 锚点帧，让浏览器建立可自动回传的 Last-Event-ID。
4. 无 `Last-Event-ID` 且表为空：从空水位等待；首次出现的仍保留事件必须作为 `editor-state` 发送，不能只吸收为 tip。

流内每页最多读取 50 条，积压时连续排空页面，空闲时每 250ms 轮询；每发一条成功事件才推进内存水位。若水位在连接中因 200 条裁剪而失效，发送固定 `cursor-stale` 控制帧并关闭；客户端后续必须重新读取当前 editor-state/H1 tip，服务端不得猜测缺口或从修订表补洞。

## 5. SSE 帧与响应头

首次已有历史但无 Last-Event-ID 时，锚点帧精确为：

```text
id: ese_<32位小写十六进制>
event: cursor
data: {"eventId":"ese_<同一ID>"}

```

每条真实版本变化精确为：

```text
id: ese_<32位小写十六进制>
event: editor-state
data: {"eventId":"ese_<同一ID>","stateVersion":"esv_<64位小写十六进制>","sourceKind":"browser_put","occurredAt":"2026-07-20T12:34:56.000Z"}

```

- `editor-state` data 精确四键，值与 H1 GET 投影一致；`id:` 必须与 data.eventId 相等。
- 空闲 15 秒发送注释心跳 `: heartbeat\n\n`，不得带 `id`、`event`、`data`、时间、用户或项目字段。
- 连接中游标失效发送无 `id` 的 `cursor-stale`，data 只含固定 `code/message`；内部异常发送无 `id` 的固定 `unavailable` 控制帧并关闭，禁止异常原文。
- 响应必须是 `text/event-stream; charset=utf-8`，并带 `Cache-Control: no-cache, no-store`、`X-Accel-Buffering: no`。
- 单连接最多 11 分钟，届时安静关闭，让原生 EventSource 使用最后成功 `id` 自动重连；不得发送伪造事件 ID。

## 6. 事务、隐私与失败边界

- H2 全程只读，不新增表、迁移、事件写入、commit/rollback 或裁剪逻辑；事件仍只由 H1 transition 同事务产生。
- 每轮必须重新按 workspace/project 查询，不得只按事件主键读取或跨连接共享 Session。
- SSE 不返回 snapshot、正文、章节、标题、actor/user/workspace/project/client/task ID、任务结果、URL、Cookie/CSRF、SQL、路径或异常类型。
- 连接前错误必须是普通 HTTP 错误并带 `no-store`，不得先返回 200 再把可预检错误藏在流中。
- H2 不承诺重放已超出 200 条保留窗口的事件；stale 是显式重新同步信号，不得伪装无变化。

## 7. 严格三文件白名单

1. `backend/app/services/editor_state_event_service.py`：增加流内短 Session 使用的保留事件页读取原语，支持经预检确认的空水位。
2. `backend/app/api/editor_state_events.py`：增加严格 SSE 路由、预检、帧格式化、短 Session 轮询与控制帧。
3. `backend/tests/test_p13h2_editor_state_event_stream.py`：failure-first、重放、锚点、空水位、鉴权、失效和隐私专项。

禁止修改实体、revision transition、schema、main、任务 SSE、公共 deps/auth、中间件、前端、P13-F/G、H1 测试、依赖、配置、迁移脚本和其它测试。Grok 不得写文档、暂存、提交、推送或清理 Codex 工件。

## 8. 失败优先与验收

新专项必须先在生产两文件未改时真实失败，至少覆盖：

1. 无 Last-Event-ID 的已有历史只发一个 cursor 锚点，不回放旧 editor-state；连接后真实写入按顺序发送。
2. 有 Last-Event-ID 精确重放其后仍保留事件，SSE `id` 与四键 data.eventId 一致；51 条以上积压跨页无丢失、重复或乱序。
3. 空表连接后的第一条及连续事件会发送，不被 bootstrap 吸收；注释心跳不改变水位。
4. 断线后以最后收到 ID 重连只收到后续事件；未知/裁剪/跨项目游标连接前固定 409，连接中 stale 固定控制帧后关闭。
5. required 未登录精确 401；非 bid_writer、非活动 workspace、任意 workspace 头精确 403；跨项目精确 404；非法请求精确 422；非 GET 精确 405。
6. 连接前和流内 Session 都关闭；不捕获 request-scope Session/ORM；短 Session 查询始终带 workspace/project。
7. 所有成功帧、心跳、控制帧和错误均无正文、快照、actor/client/task/项目空间 ID、认证材料或内部异常泄漏。
8. H1 GET、P13-A/既有 task SSE 代表回归保持通过；禁止并发 pytest、xdist、后端全量、前端或整仓 E2E。

严格禁止宽状态断言、仅非零计数、源码字符串冒充行为、直接插入事件表作为主要成功证据、睡眠后不校验顺序、跳过测试或把测试超时当成断线重放证据。

## 9. 后续明确拆分

P13-H3 才能让技术标/商务标页面建立 EventSource、严格解析 `cursor`/`editor-state`/控制帧，并在版本变化时显示保守提示或按用户动作读取既有 editor-state。跨标签自动覆盖、正文合并、评论审批、通知、协同光标、WebSocket、多任务总线和强制锁仍不在 H2/H3。
