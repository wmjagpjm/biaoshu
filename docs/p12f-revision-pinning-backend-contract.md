# P12F-J-A 修订固定与裁剪保护后端契约

模块：P12F-J-A editor-state 自动修订固定状态与配额保护后端基础
用途：在不改变现有历史列表/分页/搜索六键响应和前端行为的前提下，为单条自动修订增加受限固定状态，并让自动裁剪永远保护已固定修订。
对接：`EditorStateRevisionRow`、`editor_state_revision_service._trim_revisions`、既有 `editor_state_revisions` 路由与 required bid_writer/CSRF 门。
二次开发：Grok 只能在九文件白名单内先写真实 failure-first 再实现和自测；不得暂存、提交或推送；Codex 负责独立审查、独立验收、中文文档、提交和协作分支推送。

状态：2026-07-19 已完成实现、独立验收并推送；冻结文档=`2f03b8c`，实现提交=`a7021c4`，Grok review_request=`msg_88f4752ef1cf4a929c6b194df00d9398`，Codex 验收回执=`msg_c630805296ac48d6941809bbca957b7f`。

## 1. 选择理由与严格边界

1. P12F-I 已完成名称与可见内容联合搜索；名称排序会破坏既有 `created_at DESC,id DESC` 键集语义，检查点命名属于另一套 20 条配额域，因此本包选择固定/置顶的后端基础，但不把前端入口和历史元数据扩展混入。
2. 固定只保护自动裁剪，不改变显式单条 DELETE：用户明确删除已固定修订仍然允许，删除后不补写、不恢复、不重算 editor-state。
3. 本包只新增固定状态列、受限单条 PATCH、固定上限校验和裁剪算法；既有 list/page/search/detail 继续返回精确六键，不增加 `isPinned`，前端 API、面板和 E2E 不改。P12F-J-B 再单独扩展元数据与技术/商务 UI。
4. 不新增数据库索引、PostgreSQL/Alembic、后台任务、批量固定、固定排序、固定跨项目、收藏/标签、导出/分享或多人协作。

## 2. 固定状态与容量合同

1. `editor_state_revisions.is_pinned` 为服务端布尔状态，存储 `BOOLEAN NOT NULL DEFAULT 0`；SQLite 迁移必须附 `CHECK (is_pinned IN (0,1))`，存量行全部为 `0`。客户端不得投稿该列，transition 新增行默认不固定。
2. 项目固定上限为 **5 条**，固定修订 `snapshot_bytes` 总和上限为 **10 MiB**。两项均按当前 workspace/project 三重作用域计算；超限固定请求固定返回 409，不改变任何行。
3. 固定/取消固定均使用项目级写锁：SQLite 复用 `Project.updated_at=Project.updated_at` 锁策略，其他方言对项目行 `FOR UPDATE`。锁后重新读取目标和固定集合，禁止进程内锁/GIL/先读后写旁路。
4. 固定状态损坏、快照字节非法、固定集合超过数量/容量上限或执行/flush/commit 异常，均 rollback 并返回固定脱敏错误；不得泄漏 ID、项目、版本、快照、SQL、路径、异常原文或请求体。

## 3. 自动裁剪合同

1. `_trim_revisions` 仍只在调用方原事务内执行、只 `flush`、不 `commit/rollback/refresh`；查询只投影 `id,state_version,snapshot_bytes,is_pinned`，不读取 `snapshot_json`。
2. 必须先完整物化并严格校验全部候选行的 `snapshot_bytes` 与 `is_pinned`，再决定删除集合；任一行损坏不得部分 DELETE。
3. 所有固定行永远进入保留集合；非固定行按 `created_at DESC,id DESC` 的最新前缀加入，直到总行数超过 20 或总 `snapshot_bytes` 超过 20 MiB；首次不满足的非固定行及其后所有更旧非固定行删除。固定旧行可在时间顺序中形成空洞，但总行数/总字节仍不得超过原配额。
4. 先校验固定集合不超过 5 条/10 MiB，再执行裁剪；由于固定上限预留至少 15 条与 10 MiB 非固定空间，合法新快照（单条最多 2 MiB）不会因固定保护阻断既有业务 transition。禁止跳过大非固定行保留更旧小行。
5. DELETE 必须同时限定 workspace、project、id；跨项目/跨空间、自动裁剪之外的范围删除均禁止。

## 4. PATCH 接口合同

唯一新增入口：`PATCH /api/projects/{projectId}/editor-state-revisions/{revisionId}/pin`。

- 请求 query 必须为空；JSON 必须为精确一键 `{ "isPinned": true|false }`，拒绝空体、null、数组、字符串、snake_case、额外键、非原生布尔和超限 body；错误固定 422/no-store。
- 成功响应必须精确一键 `{ "isPinned": true|false }`、200/no-store；同值重复请求幂等且不改变固定计数/字节。
- 项目/空间不存在优先固定 `project_not_found`/404；目标修订不存在或跨项目固定 `editor_state_revision_not_found`/404；固定容量超限固定 `editor_state_revision_pin_limit`/409；数据库/flush/commit 失败固定 `editor_state_revision_pin_failed`/500。
- required 模式继续复用既有会话、当前 workspace、`bid_writer` 与 CSRF 门；disabled 保持本机测试兼容。不得新增角色、Token、Cookie、审计或外网请求。

## 5. 九文件白名单与冻结哈希

| 文件 | 冻结 SHA-256 | 允许变化 |
|---|---|---|
| `backend/app/models/entities.py` | `7CC2A19BB3923859DA99D7870D951B98F62EA559B3D576F3387CFDCC62E4A8F5` | `is_pinned` ORM 列与中文注释 |
| `backend/app/core/database.py` | `BA5624B5862595B71E1873C0411D89C34CCC2DC79439FE7904142A6905AA6EB9` | SQLite 幂等加列与固定 CHECK 迁移 |
| `backend/app/services/editor_state_revision_service.py` | `5E7247BCE8594ED155E80F63311F166CC8C9462BEC85D2783874CF206B3A3C85` | 固定集合校验与保护性裁剪 |
| `backend/app/services/editor_state_revision_pin_service.py` | 新文件，不存在 | 单条固定服务与固定错误 |
| `backend/app/api/schemas.py` | `EA01B3048A26D2D97D6B0B46F7FEFF46BA3C6DFD99F7CE20764962D6ED5C3D06` | 固定请求/响应 Schema |
| `backend/app/api/editor_state_revisions.py` | `69B2F75363F51D70006C066501AC8DD30D8ED9B8211798F5D818FEAD8E548D0E` | 新 PATCH 路由与固定脱敏解析 |
| `backend/tests/test_editor_state_revisions.py` | `58B8B8F2428C5E3AFF0FC27DE1218F7634B4681703C0E77DC183FE49E0D01936` | 精确列集合与裁剪基线的机械/新增证据 |
| `backend/tests/test_p12f_revision_pin.py` | 新文件，不存在 | 真实 ASGI/SQLite 固定、上限、锁、回滚和安全门 |
| `backend/tests/test_p12f_revision_delete.py` | `E1CE8CBA925022EC6202146879557DC570DE87FB73ADE78A68705BAC7CD1529E` | 仅在 `test_delete_ast_and_source_guards` 的 `ann_fields` 精确清单中，于 `display_name` 后加入 `is_pinned`；禁止改其它字符 |

禁止修改前端、历史 API 六键 parser/response、其它服务、迁移以外的数据库结构、依赖/锁文件、其它测试、Git 历史或文档（文档由 Codex 后续闭环）。

## 6. Failure-first 与串行验收门

Grok 必须先只新增/修改两个测试文件，真实红测后才能改六个生产文件。首个有效失败必须是 PATCH 固定入口缺失或 `is_pinned` 列/保护断言缺失，不得以 import/收集错误、跳过、宽状态或假夹具代替。

Grok 与 Codex 均逐条串行；pytest 禁止 xdist/并发分组：

1. `python -m pytest -q tests/test_p12f_revision_pin.py --tb=line`
2. `python -m pytest -q tests/test_editor_state_revisions.py tests/test_p12c_revision_restore.py --tb=line`
3. 后端全量 `python -m pytest -q --tb=line`
4. `python -m py_compile app/models/entities.py app/core/database.py app/services/editor_state_revision_service.py app/services/editor_state_revision_pin_service.py app/api/schemas.py app/api/editor_state_revisions.py tests/test_p12f_revision_pin.py`
5. `git diff --check`、精确九文件、空暂存区、最终 SHA-256、SQL/AST/错误脱敏/零写/无正文投影扫描。

## 7. 明确未做

不做 `isPinned` 历史列表/page/search/detail 响应扩展、前端按钮/标签/状态水合、技术/商务 UI、批量固定/批量取消、固定排序、固定跨项目、搜索高亮/片段/评分/游标/缓存、检查点命名、自动清理任务、导出/分享、多人协作、SSE/WebSocket、PostgreSQL/Alembic 或新的索引。
