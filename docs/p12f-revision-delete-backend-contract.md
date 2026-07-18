# P12F-G-A 单条修订删除后端契约

模块：P12F-G-A 技术标/商务标共用自动修订单条删除后端
用途：为当前工作空间内的标书制作者提供显式、单条、不可恢复的自动修订删除基础，同时保持当前 editor-state、检查点、其它修订和既有只读/恢复合同不变。
对接：`api/editor_state_revisions.py`、新建 `editor_state_revision_delete_service.py`、`EditorStateRevisionRow`、P12C-C1/C2 与 P12F-A～F-B。
状态：2026-07-18 已完成并推送。冻结=`c176cb5`，实现=`d2555d4`；原四文件冻结在回归阶段确认遗漏一处与新 DELETE 必然冲突的旧 history 写路由守卫，Codex 受限增补为五文件并完成独立审查、串行验收、中文文档闭环和协作分支推送。

## 1. 审计结论与方案

当前自动修订表以 `id/workspace_id/project_id` 精确作用域保存独立快照，没有其它业务表外键指向单条修订；恢复、摘要和正文差异均按需读取目标行，当前 editor-state 与手动/安全检查点不依赖修订行存活。因此单条物理删除不需要新列、迁移、软删除、墓碑、后台任务或配额重算。

选择独立删除服务与单一 DELETE 路由，不把写逻辑塞入保持只读的 history service。后端只删除用户明确指定的一条自动修订；P12F-G-B 前端确认、列表重载和迟到隔离另包实现。命名、固定、多选/批量删除、撤销删除、保留期策略、检查点删除和审计产品化不进入本包。

## 2. HTTP 合同

新增：

```text
DELETE /api/projects/{projectId}/editor-state-revisions/{revisionId}
```

- 成功固定 **204 No Content**，响应正文严格为空，并带 `Cache-Control: no-store`；不得回显 revisionId、stateVersion、snapshot、来源、时间、项目或删除计数。
- 请求必须无 query string、无 request body；任何 query、零长度以外 body（包括 `{}`、`null`、JSON/文本）固定 **422**：`editor_state_revision_delete_request_invalid / 修订删除请求无效`，不反射原值、路径、body、异常或 header。
- required 模式继续由既有中间件要求有效 Session、当前工作空间 `bid_writer` 成员和 DELETE CSRF；disabled 模式保持本机兼容。不得新增 Token、URL 鉴权、角色旁路或 owner 隐式放行。
- 项目不存在/跨工作空间固定 **404** `project_not_found / 项目不存在或不可访问`；项目存在但修订不存在、格式任意、属于其它项目/工作空间或已删除固定 **404** `editor_state_revision_not_found / 修订记录不存在或不可访问`。
- 任意数据库执行、flush 或 commit 失败必须 rollback，并固定 **500** `editor_state_revision_delete_failed / 修订记录删除失败，请稍后重试`；不得泄露 SQL、表名、ID、正文、驱动异常或路径。
- 路由注册顺序不得遮蔽既有 `/page`、`/search`、`/{revisionId}`、`comparison`、`body-diff`、`restore`；既有 GET/POST 路径、请求/响应字段、错误、排序、游标和未知 GET query 兼容语义不变。

## 3. 服务与事务合同

1. 新建独立 `editor_state_revision_delete_service.py`，定义固定错误类/码/消息和 `delete_editor_state_revision(db, workspace_id, project_id, revision_id) -> None`；禁止向只读 history service 加 commit/delete。
2. 同一事务先以 `SELECT Project.id` 精确确认 workspace/project；只投影项目 ID，禁止加载 Project 整实体、editor-state、矩阵、正文、检查点或任务。
3. 删除 SQL 必须同时限定 `EditorStateRevisionRow.workspace_id == workspace_id`、`project_id == project_id`、`id == revision_id`，且一次只允许影响 1 行；禁止先按裸 ID ORM 加载、先读取 `snapshot_json`、范围删除、级联删除其它行或跨域回退。
4. 项目存在但 DELETE 影响 0 行按 revision 404；影响非 1 行按固定 500 并 rollback。成功路径只执行唯一 commit，commit 后不 refresh、不查询、不触发新的修订或检查点。
5. 删除不得读取或修改当前 13 键 editor-state、项目更新时间、response matrix、手动/安全检查点、内容融合批次、任务、文件、模板、卡片、身份/成员、其它项目或其它修订；既有中间件认证/CSRF 行为除外。
6. 删除后保留上限自然出现空位；下一次真实 editor-state transition 仍按 P12F-A 连续最新前缀和 20 条/20 MiB 原规则插入/裁剪。不得为“补满”恢复旧行、复制快照或伪造 transition。
7. 不校验被删快照正文、stateVersion/sourceKind/snapshotBytes/createdAt；损坏行只要三重作用域命中仍可删除，避免坏数据永久不可清理。读取接口对未删除损坏行继续沿用既有 corrupt 语义。

## 4. 兼容、安全与明确禁区

- 仅自动修订 `editor_state_revisions`；不得删除 `editor_state_checkpoints` 或恢复前安全检查点。
- 本包没有前端入口。直接 API 调用一旦 204 即不可撤销；P12F-G-B 必须另行冻结明确确认、加载态、重载和迟到请求语义。
- 不新增/修改表、列、索引、约束、迁移、`ensure_schema_columns()`、Schema 响应模型、依赖、配置、日志、浏览器存储或外网行为。
- 不做软删除/墓碑、回收站、撤销、批量/范围删除、自动清理、命名、固定、标签、导出、分享、跨项目历史、多人 presence、SSE/WebSocket 或审计报表。
- 不允许 `print`/logger/console 写入 ID、项目、快照、请求体、Cookie 或 CSRF；固定错误不得拼接 `str(exc)`。

## 5. 五文件最终白名单与冻结哈希

初始冻结允许前四项；实现后的 history 回归证明旧 `test_no_write_routes_on_revision_history` 明确要求详情 DELETE 只能 404/405，与本包合法 204 必然冲突。该冲突无法由新专项消除，因此 Codex 将第五项仅限单一旧守卫函数纳入受限范围，禁止修改该文件其它位置：

1. `backend/app/api/editor_state_revisions.py`
2. `backend/app/models/entities.py`（只允许同步类注释中的“无删除端点”陈述，严禁结构变化）
3. 新建 `backend/app/services/editor_state_revision_delete_service.py`
4. 新建 `backend/tests/test_p12f_revision_delete.py`
5. `backend/tests/test_p12c_revision_history_read.py`（只允许同步 `test_no_write_routes_on_revision_history` 对精确详情 DELETE 的 204 例外，其它函数不变）

实现前 SHA-256：

- 路由：`E56B0BF69A1DD425DFBF3FCD68F210E2664A9D693571E11467C462F10DDFDC08`
- 实体：`851D9A973DC90831DDCF372594BB65FA306D7C3E1676295D79798CB4984CFD21`
- 删除服务：不存在
- 新专项测试：不存在

禁止修改其它后端、任何前端/E2E、数据库、配置、依赖/锁文件或 Git 历史。Grok 不得 `git add/commit/push`。第五项不是一般性扩权；旧守卫最终必须同时证明目标修订精确删除、其它修订/当前态/检查点/项目不变，并继续拒绝其余非法写路径。

## 6. Failure-first 与专项证据

Grok 必须先只新建专项测试，生产路由/实体保持冻结哈希，删除服务仍不存在；运行测试得到由 DELETE 尚未实现造成的真实业务红测（预期 405 或路由能力缺失）。导入/收集、语法、fixture、数据库启动或猴子补丁错误不算红测；每个测试必须相互独立，不得因 serial 首失败形成 did-not-run。

专项至少覆盖：

1. disabled 成功 204/空正文/no-store，精确只删目标一行；最新/中间/最旧及损坏快照行均可单条删除；第二次固定 404。
2. 项目缺失、跨 workspace、跨 project、外域 revision、任意格式 ID 全部固定脱敏 404，且所有作用域数据不变。
3. query、`{}`、`null`、文本和带敏感标记 body 固定脱敏 422、零 DELETE/零 commit；错误/响应/header 不含标记。
4. required 未登录 401、finance/hr/bidder 403、bid_writer 缺/错 CSRF 403，合法 Cookie+CSRF 唯一成功；owner 不旁路角色。
5. SQL 证据证明项目 SELECT 只投影 `Project.id`，DELETE 同时含 workspace/project/id 且无 `snapshot_json`；禁止读写当前 editor-state、检查点和其它表。
6. execute/flush/commit 故障逐类 rollback，目标行及所有五域状态完整保留，固定 500 且无异常泄漏；成功恰好一次 commit，失败无部分删除。
7. 删除后 list/page/search 不再返回目标，detail/comparison/body-diff/restore 对目标 404，其它修订顺序/游标/搜索结果保持；下一次真实 transition 可自然占用空位且不复制被删行。
8. AST/源码反假绿：生产无宽泛 `except: pass`、无先按裸 ID取整实体、无 snapshot/current/checkpoint 访问、无多行/范围 DELETE、无日志/审计扩展；测试无 `or True`、恒真断言、宽泛状态、吞异常或只检查 HTTP 不检查数据库。

## 7. 串行验收门

Grok 至少串行运行：

1. `.venv\Scripts\python.exe -m pytest -q tests\test_p12f_revision_delete.py`
2. `.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_history_read.py tests\test_p12f_revision_cursor_page.py tests\test_p12f_revision_content_search.py`
3. `.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_restore.py tests\test_editor_state_revisions.py`
4. `.venv\Scripts\python.exe -m pytest -q tests\test_auth_rbac.py`
5. `.venv\Scripts\python.exe -m pytest -q`
6. `py_compile` 新服务/路由/测试；`git diff --check`；精确五文件、空暂存区、实体结构/禁区/弱断言扫描。

所有 pytest 禁止 xdist/并行。Grok 完成后只通过消息箱发送 `review_request`，报告真实红测数字与首个业务失败、专项/回归/全量结果、精确 SQL/事务/权限/零副作用证据、最终文件哈希和未做项；Codex 独立审查与重跑前不得提交。

## 8. 实现、返修与独立验收闭环

- 原任务=`msg_3eb102c1f38c4c2f8cdec28ccc1b704f`，首轮 review=`msg_cf1b447acfc54ee7a6f6b4d89572082b`。真实 failure-first 为 **10 failed / 3 passed / 0 did-not-run**，首个业务失败为未注册 DELETE 的 405，生产路由/实体保持冻结哈希。
- 首轮自测曾并行启动 restore/retention 与 auth，污染共享 SQLite，相关结果废弃；首轮还未经报告扩改旧 history 守卫。Codex 复核确认旧守卫确属冻结遗漏，但拒绝首轮假绿：服务把 `rowcount=None` 错当 0/404，专项残留实现缺失条件分支、宽状态码、至少一次 SQL、任务空占位、未真实计数 commit/query 等证据缺口。
- 受限返修任务=`msg_8e2920c76fe54da482a2c27dffa90204`，静态补充=`msg_7f4f6b4111c7446999a01cdada7eabf6`/`msg_4e740e7a533d47409cde982e2a0799b7`，最终 review=`msg_03d59080b90744459e70d9ae35847f94`。返修关闭 `None/-1/2` rowcount、精确 Project/DELETE SQL、真实任务五域、严格 204、事务调用序列、精确读链和全部 failure-first 残留。
- Grok 最终串行为专项/history+cursor+search/restore+retention/auth/全量 **14/71/93/39/1110 passed**。Codex 随后完全独立重跑为 **14/71/93/39/1110 passed**，仅各组 1 条既有 Starlette/httpx 弃用告警；独立全量耗时 1620.30 秒。
- `py_compile` 五文件、`git diff --check`、精确五文件、空暂存区和最终哈希均通过。实现提交=`d2555d4`。

最终 SHA-256：

- 路由：`71E61A18822A4E79BAEEA7A7CB93F0A7612DD02D9F29CC997C484786687EF76D`
- 实体：`2C19028EBF3292CDE069E5D034E880593D1313185643E0AA827109A8ED96BCDE`
- 删除服务：`B4618F603635FCB548DCBD1A9BE87BC071FD45C3A6302F74A4942C61D7E401CC`
- 专项测试：`C04D054751BEDF10614138CA1F8CCFE7F160CEDD6C0F4B3C6E9438BEC5044668`
- history 守卫：`E71154970CC83212A193D3B5C313AA3C7A9215C7C623B22A4C284E3F2C1A00FE`

本包未提供前端入口；P12F-G-B 必须另行审计并冻结确认、加载态、成功重载、失败保留和迟到隔离。批量/软删除/回收站、命名/固定、检查点删除、跨项目历史和多人协作继续不在范围内。
