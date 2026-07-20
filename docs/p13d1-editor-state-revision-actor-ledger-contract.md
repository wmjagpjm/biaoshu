# P13-D1 editor-state 修订操作者账本契约

> 状态：已冻结，待实现与独立验收
> 日期：2026-07-20
> 前置：P10A 身份底座、P12C 九类修订写链、P13-C 当前修订来源可见性
> 后续：P13-D2 当前版本操作者用户名展示

## 1. 目标

为 `editor_state_revisions` 的真实变更后修订保存服务端可信、可空的 `actor_user_id`，并让浏览器 PUT、后台任务、revise、两类解析、融合写入/恢复、检查点恢复和任意修订恢复九类写链都能把操作者传到同一原子事务。P13-D1 只建立可信后端账本，不提前承诺前端可见性；P13-D2 再基于当前最新且版本匹配的修订解析用户名。

本包必须解决“谁真正产生了该版本”，不能把首次建账时补入的旧 `before` 快照、无变化操作或客户端自报身份误记为操作者。

## 2. 数据模型与迁移

### 2.1 `editor_state_revisions.actor_user_id`

- 新增 `VARCHAR(64) NULL`；新库由 ORM `create_all` 创建，旧 SQLite 由 `ensure_schema_columns` 幂等补列。
- 不建外键、不建索引：修订需要在用户将来停用或删除后保留不可变身份引用；本包不按 actor 搜索，禁止为未使用查询增加索引。
- 旧行统一保持 `NULL`，禁止回填、猜测或把当前用户套到历史修订。
- 客户端不得在任何 body、query、header 或任务 payload 中控制该列。

### 2.2 `project_tasks.actor_user_id`

- 新增 `VARCHAR(64) NULL`，同样通过 ORM 新建与 SQLite 幂等迁移补列，不建外键或索引。
- `POST /api/projects/{projectId}/tasks` 创建时从已验证请求上下文捕获一次；异步 worker 只读取任务行，不依赖已结束的 Request/Session。
- 任务 REST、SSE、错误与结果序列化不得暴露该字段；旧任务保持 `NULL`。

### 2.3 迁移原子性

- 两个迁移函数仅在 SQLite、目标表存在且列缺失时执行 `ALTER TABLE ... ADD COLUMN ... VARCHAR(64)`；重复执行不得改数据或失败。
- 事务唯一由 `ensure_schema_columns` 外层管理；迁移函数不得自行 commit。
- 若第二列迁移失败，外层事务须回滚；不得留下临时表。本包不重建既有约束或索引。

## 3. 身份真值

统一内部 helper 只能读取 `request.state.auth_db_user_id`：

- `AUTH_MODE=required`：返回已认证本机用户 ID；必须是非空、长度不超过 64 的字符串。
- `AUTH_MODE=disabled`：没有可信登录身份，固定返回 `None`。
- 状态缺失、类型非法、空白或超长均保守 `None`，不得从 `X-Workspace-Id`、Cookie 原文、用户名、请求体、查询参数或任意自定义 header 推断。
- 本包不记录设备、IP、User-Agent、session ID、角色或 display name。

一次性本地解析公开回调没有登录 Request，其唯一可信 actor 是已消费票据行的 `issued_by_user_id`；不得改用回调投稿值。

## 4. 九类传播矩阵

| `source_kind` | 可信身份捕获点 | 异步/事务要求 |
|---|---|---|
| `browser_put` | `PUT /projects/{id}/editor-state` 的 request state | 与 editor-state、revision 原唯一 commit |
| `task` | `POST /projects/{id}/tasks` 创建任务时写入 `ProjectTaskRow.actor_user_id` | sync/后台 worker 均从任务行传给九类 writer；客户端同名字段无效 |
| `revise` | revise POST 的 request state | 传入 revise service 的两个真实 upsert 写点 |
| `callback` | 个人 `parse-callback` 的 request state | 与正文、任务、项目、revision 原唯一 commit |
| `local_parser` | 一次性票据的 `issued_by_user_id` | 与票据消费、正文、任务、审计、revision 原唯一事务 |
| `content_fuse_apply` | apply POST 的 request state | 与章节、恢复批次、裁剪、revision 原唯一 commit |
| `content_fuse_consume` | consume POST 的 request state | 仅 `restored>0` 产生修订并归因；零恢复不新增修订 |
| `checkpoint_restore` | checkpoint restore POST 的 request state | 传入共享 `stage_locked_canonical_restore`；同版本恢复不新增修订 |
| `revision_restore` | revision restore POST 的 request state | 同上，来源仍固定 `revision_restore` |

任何调用方都必须使用命名参数 `actor_user_id`，不得依靠线程本地变量、模块全局、当前会话猜测或在 recorder 内反查“最后登录用户”。

## 5. recorder 真值语义

`record_editor_state_transition(..., actor_user_id: str | None)` 在写入前校验 actor：只接受 `None` 或非空、无首尾空白、长度不超过 64 的字符串；非法内部调用固定失败并由调用方原事务回滚。

插入规则必须精确为：

1. 账本为空或最新版本不等于 `before` 时，补入的 `before` 行 `actor_user_id` 固定为 `NULL`。这是发现/补齐既有状态，不代表本次请求创造了它。
2. `after` 与当前最新版本不同时，新增 `after` 行记录本次可信 actor（required 用户 ID或 disabled 的 `NULL`）。
3. `before == after` 的空操作不得创造有 actor 的修订；已有同版本最新行及其 actor 必须保持不变。
4. 回到旧版本但与最新不同仍形成新时间点，并记录本次 actor。
5. source、13 键规范哈希、相邻去重、20 条/20 MiB 裁剪、固定保护、display name、历史 API 与恢复语义全部保持。

## 6. 原子性、失败与隐私

- actor 列与对应 editor-state 变更必须同一事务；recorder/flush/裁剪/commit 任一步失败，原有各写链定义的完整业务域继续回滚。
- 409、401、404、422、任务 stale/failed、解析失败、LLM 失败、零恢复和同版本恢复不得伪造 actor 修订。
- `actor_user_id` 不进入 13 键快照、`stateVersion`、响应正文、SSE、浏览器存储、日志或错误 detail。
- P13-C `currentRevisionSourceKind` 响应与前端展示不得变化；P13-D1 不新增公开字段或请求。

## 7. 明确不做

- 不解析或展示用户名；不改修订历史列表/详情字段。
- 不做 presence、在线状态、实时协同、锁人、轮询、SSE/WebSocket 扩展。
- 不做按操作者搜索/筛选、actor 审批、完整身份审计、设备/IP 追踪。
- 不回填旧 revision/task，不改变账户、成员、角色或 workspace 权限。
- 不运行无关全量测试；出现共享迁移/事务回归证据时再由 Codex决定扩大。

## 8. 验收门

1. 先提交独立 failure-first 专项测试并真实失败；生产实现前须记录失败数。
2. 新库与旧库迁移：两列存在、可空、无 FK/新索引、旧值保留、二次迁移幂等、失败回滚。
3. required 模式九类真实成功写链的最终 `after` 行 actor 精确；disabled/旧行/补入 `before` 精确为 `NULL`。
4. 异步任务证明 Request 结束后仍从任务行归因；任务 API/SSE 不泄漏 actor。
5. 客户端在 body/query/header/payload 投稿 actor 均不能控制结果。
6. no-op、stale、零恢复、同版本恢复、异常回滚均不产生伪归因。
7. Grok 仅跑 P13-D1 专项及直接受影响回归；Codex 独立选择迁移、任务、callback、融合、恢复各至少一条真实路径，不机械重复全量。
8. `py_compile`、`git diff --check`、生产文件白名单与工作区洁净检查通过。
