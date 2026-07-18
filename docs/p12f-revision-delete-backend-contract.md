# P12F-G-A 单条修订删除后端契约

模块：P12F-G-A 技术标/商务标共用自动修订单条删除后端
用途：为当前工作空间内的标书制作者提供显式、单条、不可恢复的自动修订删除基础，同时保持当前 editor-state、检查点、其它修订和既有只读/恢复合同不变。
对接：`api/editor_state_revisions.py`、新建 `editor_state_revision_delete_service.py`、`EditorStateRevisionRow`、P12C-C1/C2 与 P12F-A～F-B。
状态：2026-07-18 已完成只读审计，当前文档提交即冻结后端四文件边界；Grok 负责 failure-first 实现，Codex 负责独立审查、串行验收、中文文档闭环和协作分支推送。

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

## 5. 四文件白名单与冻结哈希

Grok 只允许修改：

1. `backend/app/api/editor_state_revisions.py`
2. `backend/app/models/entities.py`（只允许同步类注释中的“无删除端点”陈述，严禁结构变化）
3. 新建 `backend/app/services/editor_state_revision_delete_service.py`
4. 新建 `backend/tests/test_p12f_revision_delete.py`

实现前 SHA-256：

- 路由：`E56B0BF69A1DD425DFBF3FCD68F210E2664A9D693571E11467C462F10DDFDC08`
- 实体：`851D9A973DC90831DDCF372594BB65FA306D7C3E1676295D79798CB4984CFD21`
- 删除服务：不存在
- 新专项测试：不存在

禁止修改其它后端、任何前端/E2E、文档、数据库、配置、依赖/锁文件或 Git 历史。Grok 不得 `git add/commit/push`。

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
6. `py_compile` 新服务/路由/测试；`git diff --check`；精确四文件、空暂存区、实体结构/禁区/弱断言扫描。

所有 pytest 禁止 xdist/并行。Grok 完成后只通过消息箱发送 `review_request`，报告真实红测数字与首个业务失败、专项/回归/全量结果、精确 SQL/事务/权限/零副作用证据、最终文件哈希和未做项；Codex 独立审查与重跑前不得提交。
