# P13-H1 editor-state 事件账本与游标后端契约

> 状态：已完成只读审计，等待冻结提交后下发 Grok failure-first
> 日期：2026-07-20
> 审计基线：`83c2c4ad54d6d7c515221c6af23228314098528a`
> 前置：P13-A 任务 SSE 工作空间鉴权、P12C/P12F editor-state 修订账本、P13-D1/D2 可信操作者元数据
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`

## 1. 目标与诚实语义

为技术标和商务标的跨客户端刷新准备持久、脱敏、可游标读取的 editor-state 事件基础。事件只表达“某项目的 editor-state 版本发生了成功变化”，不包含正文、章节、任务结果、用户 ID 或在线状态。

本包不是 SSE 流、不是前端自动刷新、不是 WebSocket，也不是强制锁。后续包才可在本包只读 API 之上接 SSE `Last-Event-ID`、断线重放与页面提示；本包先保证事件和真实 editor-state 写入同事务，避免成功写入没有事件或失败事务泄漏事件。

## 2. 只读审计结论

1. 现有 `project_tasks/{task_id}/events` 只服务单任务，明确禁止多任务总线与事件游标，不能复用。
2. `record_editor_state_transition` 是浏览器 PUT、task、revise、callback、local_parser、content_fuse apply/consume、checkpoint/revision restore 九类写链的共同无提交原语，适合在同一 Session 事务中插入事件。
3. `editor_state_revisions` 会被固定保护裁剪和显式 DELETE，不能作为可靠事件日志；事件必须独立表、独立保留上限、无正文快照。
4. P13-D1/D2 已有 actor 可信账本，但事件 API 不返回 actor；前端需要用户名时继续走既有当前版本严格解析，避免扩大隐私面。

## 3. 严格作用域

- 仅 `AUTH_MODE=required`、当前活动 workspace、活动成员角色精确 `bid_writer` 可读。
- 任意 `X-Workspace-Id`（含空值）固定拒绝；workspace 只能来自认证 principal 的活动空间。
- 项目必须属于当前 workspace；不存在、跨空间和非法项目统一固定 404，不回显项目 ID。
- 新路由只读，不接受 body、Cookie 读取、外网 URL、浏览器存储或任务 token。

## 4. 事件表与事务

新增 `editor_state_events`，字段固定：

| 字段 | 语义 |
|---|---|
| `id` | 服务端生成不透明 `ese_` + 32 位小写十六进制游标 ID |
| `workspace_id` | 作用域，级联项目/空间删除 |
| `project_id` | 作用域，级联项目删除 |
| `state_version` | 成功 after 的规范 editor-state 版本 |
| `source_kind` | 既有九类来源枚举 |
| `occurred_at` | 服务端 UTC 时间 |

约束与索引：

- `id` 主键；`workspace_id/project_id` 非空并分别建索引；复合索引 `(workspace_id, project_id, occurred_at, id)`。
- 不存 `snapshot_json`、章节正文、标题、任务结果、actor_user_id、clientId、URL 或错误原文。
- 每项目最多保留最近 200 条事件，按 `occurred_at DESC, id DESC` 连续裁剪；事件表裁剪与当前写事务同一 Session，失败必须整事务回滚。
- 不回填历史修订；功能上线前的旧版本只能在首次连接时由既有 editor-state GET 得到，不能伪造事件。

写入规则固定为：

1. `before_state` 与 `after_state` 经过现有完整规范校验。
2. `before_ver == after_ver` 或 after 已是当前账本版本时不新增事件。
3. 只有真实插入 after 修订行时插入一条事件；账本缺口补入的 before 行不产生事件。
4. 事件插入、修订写入、修订裁剪及调用方 editor-state/任务/项目写入共享同一事务；任一 flush/commit 失败均零事件、零事件表部分写。

## 5. 只读 API

新增：`GET /api/projects/{projectId}/editor-state-events`。

查询参数仅允许：

- `after`：可空不透明事件 ID；缺失表示从当前最新位置开始，不回放旧历史；
- `limit`：可选整数，默认 50，范围 1..50；非法、重复或未知参数固定脱敏 422。

成功响应必须精确四个顶层键：

```json
{
  "items": [
    {
      "eventId": "ese_<32位小写十六进制>",
      "stateVersion": "esv_<64位小写十六进制>",
      "sourceKind": "browser_put",
      "occurredAt": "2026-07-20T12:34:56.000Z"
    }
  ],
  "nextCursor": "ese_<32位小写十六进制>",
  "hasMore": false
}
```

- `items` 按时间正序返回，最多 `limit` 条；无结果返回空数组、`nextCursor=null`、`hasMore=false`。
- `after` 指向已保留事件时只返回其后的事件；指向已裁剪/未知事件固定返回脱敏 409 `editor_state_event_cursor_stale`，不得猜测位置或从旧修订表补洞。
- `nextCursor` 仅在 `hasMore=true` 时为最后一条事件 ID，否则为 `null`。
- 响应与业务错误均 `Cache-Control: no-store`；不返回内部异常、SQL、项目/空间/用户 ID、正文或快照。
- 不支持 `GET` 之外方法、body、SSE、`Last-Event-ID` 或 WebSocket；这些留给后续包。

## 6. 失败优先与验收

新后端专项必须先在无生产实现时真实失败，至少覆盖：

1. 九类真实写链各产生恰好一条 after 事件；before 补账、同版本、失败事务零事件。
2. 事件与 editor-state/任务/项目事务绑定，flush/commit 故障整次回滚。
3. 200 精确响应、严格游标顺序、limit 1/50 边界、空结果与连续 200 条裁剪。
4. 游标指向保留事件可继续读取；指向裁剪/伪造/跨项目事件固定 409，不能回显输入。
5. required 未登录、非 bid_writer、非活动 workspace、任意 workspace 头、跨项目均固定拒绝。
6. 事件响应无 snapshot、正文、章节、actor/client/digest/任务结果；成功/错误 `no-store`。
7. 现有 editor-state revision、task SSE、P13-G1/G2、P13-F2 与认证代表回归保持通过。

严格禁止宽状态断言、仅非零计数、假事务、测试直接写事件表、绕过真实写链、读取完整快照或把旧修订表当事件源。

## 7. 严格八文件白名单

1. `backend/app/models/entities.py`：新增事件表。
2. `backend/app/models/__init__.py`：导出实体。
3. `backend/app/services/editor_state_revision_service.py`：真实 after 事件写入与裁剪钩子。
4. `backend/app/services/editor_state_event_service.py`：新增严格事件查询服务。
5. `backend/app/api/schemas.py`：新增精确请求/响应模型。
6. `backend/app/api/editor_state_events.py`：新增只读路由与脱敏错误映射。
7. `backend/app/main.py`：注册实体与路由。
8. `backend/tests/test_p13h1_editor_state_events.py`：failure-first、事务、作用域、游标和隐私专项。

禁止修改前端、共享 `api.py`、auth/router、任务 SSE、P13-F1/F2/G1/G2、既有修订历史 API、配置、依赖、迁移脚本和其它测试。Grok 不得写文档、暂存、提交、推送或清理产物。

## 8. 后续明确拆分

P13-H2 才能在本包 API/事件表之上增加 SSE `Last-Event-ID` 与断线重放；P13-H3 才能增加前端当前版本提示或按事件触发既有 editor-state GET。评论、审批、通知、协同光标、WebSocket、多任务总线和完整审计仍不在本包。
