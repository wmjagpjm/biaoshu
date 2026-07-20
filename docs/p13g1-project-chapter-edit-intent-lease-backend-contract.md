# P13-G1 项目章节编辑意图租约后端契约

> 状态：已完成、独立验收并推送；冻结=`a0b7c48`，实现=`015ab37`
> 审计基线：`f0325d0593b0b8c6fc291ee08f646cffe74164fe`
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 前置真值：P13-F1 后端=`6164d8c`，P13-F2 前端=`dfa6bc0`
> 协作：Grok 仅按七文件白名单实现与自测；Codex 负责审查、双确认返修门、独立验收、中文提交、文档闭环和 Git

## 1. 目标与诚实语义

为技术标项目的单个权威章节提供短期“正在处理意图”租约：

1. required 模式下，当前活动 workspace 的 strict `bid_writer` 可用文档内存 `clientId` 对一个真实章节建立或续期 45 秒租约。
2. 同一项目同一章节同一时刻最多一个活动租约；原持有 client 可续期，其它用户或其它 client 得到固定 409 与安全持有人用户名。
3. 客户端离开章节时可精确释放自己的租约；异常离开仍由 45 秒过期兜底。
4. 本包只交付后端协议，不修改现有 editor-state PUT，也不阻止旧客户端、任务、回调或其它写路径。

因此本能力只能称“章节编辑意图”“近期由某成员处理”，不得称“强制锁”“独占锁”“已阻止覆盖”或“实时协同”。真正强制锁需要先拆分章节级写 API 或让所有写入口携带并校验租约，必须另包设计。

## 2. 方案选择

采用“项目级事务串行 + 章节单持有者短租约 + HTTP heartbeat/leave”：

- 复用 P13-F1 的 45/15 秒节奏、clientId 摘要、required 活动空间和 strict bid_writer 边界。
- 新表只保存 workspace/project/chapter/user/client 摘要和服务端时间；不保存章节正文、标题、用户名或客户端原文。
- heartbeat 每次在取得项目级写锁后重新读取当前 `chapters_json`，目标 `chapterId` 必须精确出现一次。
- 冲突持有人必须重新通过启用用户、同活动空间启用 bid_writer 成员和安全用户名门；失效持有人租约按陈旧处理。

不选择：

- 不在本包做硬锁：现有 PUT 是 13 键整包写，章节没有实体表，旧客户端和任务写链也不携带 clientId。
- 不先做协同光标：需要选区协议、实时传输、节流和断线语义，无法从当前 HTTP 租约安全推导。
- 不先做事件流：持久游标、裁剪缺口、SSE 广播、多进程与重连恢复必须独立设计。

## 3. 身份、作用域与启用门

1. 仅 `AUTH_MODE=required`、已认证、`active_workspace_id` 非空且活动成员角色精确为 `bid_writer` 时可用。
2. disabled、未认证、活动成员缺失、停用用户/成员和其它角色全部拒绝，owner 不绕过角色。
3. 只接受当前活动 workspace 内的 `kind=technical` 项目；跨空间与不存在项目统一固定 404。
4. 任意 `X-Workspace-Id` 请求头，包括空值，固定拒绝；不得用 query、Cookie 读取或客户端字段切换作用域。
5. `chapterId` 只从当前项目权威 `ProjectEditorStateRow.chapters_json` 校验；无 editor-state、非数组、目标缺失均不可建立租约。
6. 目标 ID 必须在当前章节数组的字典项中以原生字符串精确出现一次；重复目标固定拒绝，禁止 trim、NFKC、大小写折叠或标题回退。
7. 当前 actor 的数据库用户、成员、角色和用户名也须重验；用户名不通过安全文本门时固定 403、零租约，禁止建立一个未来无法安全展示 holder 的租约。

## 4. 请求与响应协议

端点：

- `POST /api/projects/{projectId}/chapter-edit-lease/heartbeat`
- `POST /api/projects/{projectId}/chapter-edit-lease/leave`

请求体必须精确两键：

```json
{"clientId":"<22..64 位 [A-Za-z0-9_-]>","chapterId":"<1..128 个安全 Unicode 码点>"}
```

`clientId` 规则与 P13-F1 一致：原生字符串、长度 22..64、字符仅 ASCII 字母数字、下划线和连字符，不 trim。`chapterId` 为原生字符串、1..128 Unicode 码点、无首尾空白，拒绝 C0/C1/DEL、U+2028/U+2029 和双向控制字符；只放在 JSON body 和服务端作用域校验中，不进 URL、日志或错误原文。

heartbeat 200 精确两键：

```json
{"leaseExpiresAt":"<服务端 UTC 时间>","refreshAfterSeconds":15}
```

活动租约被其它 client 持有时返回 409，`detail` 精确三键：

```json
{"code":"chapter_edit_lease_conflict","message":"此章节近期已有处理意图","holderUsername":"<安全用户名>"}
```

leave 成功或目标本就不属于当前 actor/client 时均为 204 空 body。所有成功与业务错误均 `Cache-Control: no-store`。

## 5. 表、时间与事务

新表 `project_chapter_edit_leases`：

- `id`：不透明主键，最多 64 字符。
- `workspace_id`：FK `workspaces.id ON DELETE CASCADE`。
- `project_id`：FK `projects.id ON DELETE CASCADE`。
- `chapter_id`：最多 128 字符，允许明文，因为它是编辑态内部定位键，不是 client 秘密；禁止响应和日志外泄。
- `user_id`：FK `local_users.id ON DELETE CASCADE`。
- `client_digest`：规范 clientId 的 SHA-256 小写十六进制摘要，禁止原文落库。
- `last_seen_at`、`expires_at`：仅服务端 UTC 时间。

约束与索引：

1. 唯一键精确 `(workspace_id, project_id, chapter_id)`，保证单章节单持有者。
2. 复合索引 `(workspace_id, project_id, expires_at)`。
3. 复合索引 `(workspace_id, project_id, user_id, expires_at)`，供每用户项目活动租约上限使用。
4. 每用户每项目最多 8 个活动章节意图；同一持有租约续期不占新名额，新章节达到上限固定 429。

heartbeat 必须在任何项目/章节判断、过期清理、计数、冲突判断和写入前取得项目级数据库写锁。SQLite 对当前项目行做无值变化 UPDATE；PostgreSQL 等对项目行 `SELECT ... FOR UPDATE`。取得锁后才采样一次 `now`，过期为 `now + 45s`；服务只 `flush`，路由唯一 `commit`，任意失败完整 rollback。

## 6. heartbeat 规则

1. 锁后重验项目、技术标 kind、权威章节唯一命中和当前 actor 安全身份。
2. 机会性清理已过期租约；不得启动后台线程或定时任务。
3. 当前章节无活动租约时，在 8 个活动章节上限内建立新租约。
4. 当前租约的 user/client 摘要都与 actor 匹配时原行续期，不创建新行。
5. 同用户不同 client 也视为冲突，防止同一账号多个页面相互覆盖意图。
6. 当前持有人已停用、成员停用/改角色、跨空间或用户名不安全时，旧租约视为陈旧，可在同一事务删除并由当前 actor 接管。
7. 活动冲突只返回安全 `holderUsername`；禁止返回 user/member/lease/client ID、digest、角色、owner、时间明细、项目 ID 或章节正文/标题。

## 7. leave、删除与失效

1. leave 仍须重验 required strict bid_writer 与当前空间项目，但不要求章节当前仍存在，以便章节删除后清理旧租约。
2. 只删除 `(workspace, project, chapter, actor user, client digest)` 全部匹配的行；不得删除同用户其它 client、其它章节、其它用户或其它项目。
3. 错 client、重复 leave 或租约已过期均幂等 204。
4. 项目、用户和 workspace 删除依赖 FK 级联；章节从 JSON 删除后旧租约最多保留至过期或原 client leave。

## 8. 错误与隐私

- 请求 JSON 解析、extra/缺键/坏类型/非法 clientId/chapterId：422，`chapter_edit_lease_request_invalid / 章节编辑意图请求无效`，禁止 Pydantic 默认错误回显原始值。
- 项目缺失、跨空间或非 technical：404，`project_not_found / 项目不存在`。
- 无 editor-state、`chapters_json` 非数组或目标缺失：404，`chapter_not_found / 章节不存在`。
- 目标 `chapterId` 在当前章节数组重复出现：409，`chapter_state_invalid / 章节状态不可用`。
- 当前 actor 用户名不安全：403，复用 `role_forbidden / 当前角色无权执行此操作`。
- 活动冲突：409，`chapter_edit_lease_conflict / 此章节近期已有处理意图`，另带唯一安全 `holderUsername`。
- 用户项目活动章节达到 8 个：429，`chapter_edit_lease_limit / 当前项目章节处理意图数量已达上限`。
- 未知数据库/提交错误：500，`chapter_edit_lease_failed / 章节编辑意图处理失败`，完整 rollback，不回显 SQL、路径、clientId、chapterId 或异常原文。
- 禁止任何 console/print/logger 输出请求体；禁止写审计、历史、Cookie、session、外部系统或新缓存。

## 9. 严格七文件白名单

修改：

- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/api/schemas.py`
- `backend/app/main.py`

新建：

- `backend/app/services/project_chapter_edit_lease_service.py`
- `backend/app/api/project_chapter_edit_leases.py`
- `backend/tests/test_p13g1_project_chapter_edit_lease.py`

禁止修改 P13-F1/P13-F2 文件、`editor_state_service.py`、projects/editor-state 路由、认证中间件/deps、公共配置、前端、依赖、已有测试或文档。新表由 ORM `create_all` 建立，不新增轻量加列迁移。

## 10. failure-first 与验收

新专项必须先只写测试并真实失败，至少覆盖：

1. heartbeat/leave 路由缺失红测，生产六文件哈希冻结。
2. 成功精确 body/响应/no-store、client 摘要落库、45/15 时间。
3. 同 client 原行续期、同用户不同 client 冲突、不同用户冲突与安全用户名。
4. 两用户并发抢同章节恰一成功一冲突，不重复、不 500。
5. 过期接管、锁后 fresh now、8 章节上限和旧持有续期。
6. 当前章节精确唯一命中；缺失、重复、非数组、技术/商务项目边界。
7. required/disabled、角色、owner、停用、跨空间、任意 `X-Workspace-Id`、CSRF。
8. leave 全维隔离、幂等、章节删除后清理。
9. 持有人停用/改角色/坏用户名后接管。
10. 表精确列/唯一键/复合索引/FK 级联、service/commit 故障 rollback、敏感字段零出口。
11. 禁止 GET/query/SSE/WebSocket 路由，禁止 editor-state PUT 强制锁断言。

pytest 只允许串行：先 P13-G1 新专项，再 P13-F1、auth/projects/editor-state 代表直接回归；运行 `py_compile`、`git diff --check`、白名单、空暂存和 SHA-256 门。不默认后端全量或任何 Playwright。

## 11. 双确认返修门

Codex 发现疑似问题后先发送只读 question/review；Grok 只能确认或否认并给证据，不得修改。只有双方明确确认存在，Codex 才另发独立 task 精确授权返修。所有消息 ID、红绿数字、最终哈希、未运行项和残余风险写回完成态文档。

## 12. 明确不做

- 不做强制章节锁、修改 editor-state PUT、章节级 PATCH、自动保存门禁或旧客户端阻断。
- 不做前端选章 heartbeat、禁用编辑器、持有人提示、倒计时或通知；留给 P13-G2。
- 不做项目级锁、大纲/事实/商务字段锁、多资源通用锁或锁升级。
- 不做协同光标、选区、正在输入、正文增量同步、CRDT/OT、自动合并。
- 不做 GET 列表、在线历史、最后活跃、审计、SSE/WebSocket、广播、游标重放或跨标签同步。

## 13. 交付记录

- 冻结=`a0b7c48`，实现=`015ab37`。
- 有效 failure-first=`42 failed / 3 passed`；Grok 最终聚焦/专项=`17/53 passed`。
- Codex 独立专项/P13-F1/认证/editor-state=`53/41/8/1 passed`，六文件 `py_compile`、diff-check、严格七文件与哈希门通过。
- 第一轮六项问题经 question=`msg_cec182e52c6c4775b99ef33eef0cbf60`、只读确认=`msg_7d6862739de5449082c65350b4536deb` 后才由 task=`msg_2e591638e1b94f559cdab1ea3e57c0d6` 返修；验收 result=`msg_18dc76c33b9f47d0a72d754e7578682c`。
- 未运行后端全量、Playwright、前端或 xdist。P13-G2 前端提示尚未实现，本契约仍不提供强制锁。
