# P13-F1 项目在线租约后端基础契约

> 状态：已冻结，待 Grok 实现与 Codex 独立验收
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 协作：Grok 负责受限后端实现与自测；Codex 负责规划、范围冻结、审查、双确认返修门、独立验收、中文文档闭环和 Git

## 1. 目标与阶段边界

为技术标/商务标项目提供最小、可过期、项目级 presence 租约协议，回答“最近 45 秒内哪些严格标书制作者仍在给本项目续租”。P13-F1 只交付后端表、服务和 HTTP 接口；P13-F2 才能另行实现浏览器内存 clientId、15 秒心跳、离开和可见 UI。

本包的 presence 是服务端观察到的短租约，不等于人员真实在线、正在输入、当前焦点或最后活跃历史。任务 SSE 的 `heartbeat` 仅是流保活，不得复用或改名成成员 presence。

## 2. 唯一 HTTP 协议

### 2.1 心跳

- `POST /api/projects/{projectId}/presence/heartbeat`
- 请求体精确 `{ "clientId": "<22..64 位 base64url/UUID 安全文本>" }`。
- 只接受 JSON 键 `clientId`；`client_id`、缺失、额外键、非字符串、首尾空白、控制字符或不匹配 `[A-Za-z0-9_-]{22,64}` 固定走 422，禁止 trim/NFKC/别名接受。
- 成功 200，响应顶层精确四键：`leaseExpiresAt/refreshAfterSeconds/members/truncated`。
- `refreshAfterSeconds` 固定 15；租约固定 45 秒。`leaseExpiresAt` 为服务端 UTC 时间，不接受客户端时间。
- `members` 每项精确两键：`username/isSelf`；`isSelf` 仅由可信会话 actor 比较得出。

### 2.2 离开

- `POST /api/projects/{projectId}/presence/leave`
- 请求体与心跳相同，成功固定 204 空 body；不存在、已过期或已离开仍幂等 204。
- 只能删除当前可信 actor、当前活动 workspace、当前 project、当前 clientId 摘要对应的租约；不得按 body/header 指定用户。

所有成功响应必须 `Cache-Control: no-store`。不新增 GET、SSE、WebSocket、长轮询、批量接口或查询参数。

## 3. 身份、角色与作用域

1. 仅 `AUTH_MODE=required`、已登录、当前会话 `activeWorkspaceId` 精确命中的启用成员且角色精确为 `bid_writer` 可用；所有者标记不能替代角色。
2. disabled、finance、hr、bidder、停用用户/成员、无会话统一沿用固定认证/角色拒绝，不创建或读取租约。
3. presence 只认活动工作空间。任何 `X-Workspace-Id` 请求头（含空值）都固定拒绝，不允许借成员头切换 presence 作用域。
4. 项目必须真实属于当前活动 workspace；不存在、跨空间或已删除统一 404，不泄漏项目名称、实际 workspace、用户或租约信息。
5. actor user id 只取认证中间件注入的可信 principal；禁止从 path、body、query、header、Cookie 原文或 clientId 推断。

## 4. 租约存储与时钟

新增 `project_presence_leases`：

- `id`：服务端不透明主键，最多 64 字符；不返回客户端。
- `workspace_id/project_id/user_id`：分别绑定工作空间、项目与本机用户，项目/用户删除级联清理。
- `client_digest`：只存规范 clientId 的 SHA-256 十六进制摘要，禁止落库或日志记录原始 clientId。
- `last_seen_at/expires_at`：均由服务端 UTC 时钟生成；`expires_at=now+45s`。
- 唯一键：`workspace_id+project_id+user_id+client_digest`；索引至少覆盖 `workspace_id+project_id+expires_at` 和当前用户活动租约计数。

同一 client 心跳必须原子 upsert，不新增第二行；更新 `last_seen_at/expires_at`。每次心跳机会性删除全表已过期行。每个用户在单项目最多 8 条未过期 client 租约；达到上限时已有 client 仍可续租，新 client 固定 429 `presence_client_limit` 且零新增。

## 5. 成员快照

1. 快照只读当前 workspace/project 且 `expires_at > now` 的租约，并重新联表校验用户启用、同 workspace 成员启用且角色仍为 `bid_writer`；角色/成员/用户变化立即收敛，不等旧租约到期。
2. 同一用户多个 client 只输出一次。用户名必须通过 P13-D2 同等级安全文本门：原生字符串、1..100 Unicode 码点、无首尾空白，拒绝 C0/C1/DEL、U+2028/U+2029 和双向控制字符；坏用户名整用户隐藏，不回显占位或原文。
3. 最多输出 50 个安全用户，当前 actor 优先，其余按用户名大小写折叠后稳定排序；候选超限固定 `truncated=true`。不得返回 userId、memberId、leaseId、clientId/digest、角色、owner、时间、Cookie、CSRF、会话或项目内部字段。
4. 空快照仍 200，`members=[]`、`truncated=false`；心跳成功后当前 actor 的安全用户名通常应出现，但坏/停用数据必须保守隐藏。

## 6. 事务、并发与错误

- 项目重验、过期清理、限额判断、upsert 与快照读取须在服务定义的唯一事务边界内完成；service 不在中途 commit，成功由一次 commit 闭环，失败 rollback。
- 两个相同 client 并发心跳不得产生重复行、500 或越过唯一约束；不同 client 的上限判断不得静默无限增长。
- 429 只返回固定 code/message；数据库异常由路由回滚并返回脱敏固定 500，不得泄漏 SQL、表/列名、路径、摘要、ID 或异常类型。
- leave 失败必须完整 rollback；不能误删同用户其它 client、其它用户、项目或 workspace 租约。

## 7. 安全与数据边界

- 所有 POST 继续使用现有 Cookie 会话与 CSRF 中间件；不新增 URL token、Authorization 旁路、Cookie 读取或自定义 workspace 头。
- `clientId` 是浏览器单页随机关联符，不是身份、密钥或用户 ID；P13-F2 只能保存在 React/页面内存，不得落 localStorage/sessionStorage/IndexedDB/URL/日志。
- 后端禁止 console/打印、审计正文、外网、后台 timer 或常驻清理线程；过期只靠查询过滤与心跳机会清理。
- 不改 editor-state、CAS、修订账本、任务 SSE、认证会话 `last_seen_at`、成员列表或业务写链。

## 8. 双确认返修门

Codex 发现疑似问题后必须先向 Grok 下发只读独立确认；只有双方明确确认存在，Codex 才另发独立修复授权。确认消息不是修复任务；有分歧时保持代码不动补证据，仍无共识交用户裁定。若确认前误触文件，立即冻结，不继续、不清理、不提交。所有发现、确认、返修与 review_request 消息 ID 写入闭环。

## 9. 严格修改范围

生产仅六个文件：

- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/api/schemas.py`
- `backend/app/services/project_presence_service.py`（新）
- `backend/app/api/project_presence.py`（新）
- `backend/app/main.py`

测试仅一个文件：

- `backend/tests/test_p13f1_project_presence.py`（新）

禁止修改认证中间件、`deps.py`、`auth_service.py`、项目/editor-state/task 服务、已有测试、前端、依赖、配置或其它文件。确需扩围时 Grok 必须停下，以 status 报告真实失败、必要性和最小文件名，等待 Codex 授权。

## 10. failure-first 与验收

1. 四个既有生产文件哈希不变且两个新生产文件不存在时，先写新专项真实调用接口，取得路由 404/表或行为缺失 red；不得用源码字符串、签名、`hasattr` 或恒真断言冒充。
2. 专项覆盖：成功精确 shape/no-store、同 client 续租、双 client 聚合、两用户可见、自身标记、45/15 常量、过期过滤/清理、leave 幂等/隔离、8 client 上限、并发同 client、角色/disabled/跨空间/跨项目/X 头/CSRF、坏请求矩阵、安全用户名、敏感字段零出口、数据库失败 rollback、表约束/索引/级联。
3. Grok 串行运行新专项、`test_auth_rbac.py`、`test_health_and_projects.py`、`test_p13a_task_sse_workspace_auth.py`、py_compile 与 `git diff --check`；不得并行 pytest、xdist 或后端全量。
4. Codex 独立审查事务、并发、作用域、摘要、限额、响应预算与反假绿，再按信号串行复验；不机械运行后端全量或前端测试。

## 11. 明确不做

- 不做 P13-F2 前端 clientId、心跳、离开、在线成员 UI、轮询或可见性恢复。
- 不做 SSE/WebSocket 广播、Last-Event-ID、游标重放、多任务总线或断线补发。
- 不做协同光标、选区、正在输入、章节锁/租约、冲突自动合并、评论、审批或通知。
- 不做在线历史、最后活跃时间、设备/IP/浏览器识别、管理员监控或完整审计。
- 不做 Redis/Celery/PostgreSQL 专用实现、后台清理任务、Alembic 或外部 presence 服务。
