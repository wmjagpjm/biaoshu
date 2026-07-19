# P12J-A 检查点固定与保护裁剪后端契约

模块：P12J-A editor-state 手动/安全检查点固定状态与保护裁剪后端基础

用途：在不改变既有检查点七/八键响应和前端行为的前提下，为单条检查点增加受限固定状态，并让创建与恢复事务的最近 20 条裁剪始终保护固定检查点和本轮恢复前安全检查点。

对接：`EditorStateCheckpointRow`、`editor_state_checkpoint_service._trim_checkpoints`、既有 `editor_state_checkpoints` 路由、required `bid_writer`/CSRF 门。

二次开发：Grok 只能在九文件白名单内先写真实 failure-first 再实现和自测；不得暂存、提交或推送；Codex 负责独立规划、受限审查、独立验收、中文文档、提交和协作分支推送。

状态：2026-07-19 已完成、独立验收并推送。冻结=`9f304da`，实现=`8edebd4`；P12J-B 响应与前端入口仍未实现。

## 1. 选择理由与严格边界

1. P12I 已完成当前项目最多 20 条检查点的名称/可见内容显式搜索。剩余版本治理中，排序/分页会改变读取合同，跨项目历史、完整时间线和多人协作会扩大身份、权限与会话边界；固定保护已有 P12F-J-A/B 的成熟事务模式，适合作为下一最小高价值包。
2. 固定只保护自动裁剪，不改变显式单条 DELETE：用户仍可明确删除已固定检查点；删除后不补写、不恢复、不重算 editor-state。
3. 本包只新增固定状态列、SQLite 幂等迁移、受限单条 PATCH、固定上限校验和保护性裁剪。既有 create/list/search 继续返回精确七键，detail 精确八键，不增加 `isPinned`；前端 API、面板和 E2E 不改。P12J-B 才能单独扩展元数据与技术/商务共用 UI。
4. 不新增数据库索引、Alembic/PostgreSQL、后台任务、批量固定、固定排序/分组、跨项目固定、标签/备注、导出/分享、完整时间线、多人协作、presence、SSE 或 WebSocket。

## 2. 固定列与 SQLite 迁移合同

1. `editor_state_checkpoints.is_pinned` 为服务端布尔状态，ORM/新表固定 `BOOLEAN NOT NULL DEFAULT 0`，SQLite DDL 必须有 `CHECK (is_pinned IN (0, 1))`。客户端不能在创建、恢复、命名或搜索请求投稿该列；手动检查点与恢复前安全检查点初始值均为 `false`。
2. 官方旧表没有该列时，迁移必须把所有存量行置为 `0`；若遇到已有列但缺少 CHECK 的中间态，只保留原始 `0/1`，其它值确定性归零。具备列和等价 0/1 CHECK 时幂等 no-op。
3. SQLite 迁移使用临时表重建，完整保留十一列、三个既有数值 CHECK、workspace/project 外键和四个既有索引；禁止 `writable_schema`、关闭外键/检查约束、吞异常后启动或无行数核对 DROP。
4. 外层事务内先用零行 DML 触发真实事务，再建临时表、显式列复制、核对迁移前后行数、DROP/RENAME、重建索引。建表、复制、行数核对、DROP、RENAME 或索引任一步失败都必须回滚，旧表/旧数据/旧索引保持完整且不得残留临时表。
5. 非 SQLite 迁移函数 no-op；新数据库仍由 ORM `create_all` 直接创建最终结构。

## 3. 单条固定服务与配额合同

1. 每个 workspace/project 最多固定 **5 条**检查点，固定行 `snapshot_bytes` 总和最多 **10 MiB**；两项均在项目级写锁后按数据库当前值重算，超限固定请求返回 409 且零写。
2. SQLite 复用 `Project.updated_at=Project.updated_at` 无副作用 UPDATE 取得项目级写锁；其它方言对项目行 `FOR UPDATE`。禁止进程内锁、先读后写旁路、加载当前 editor-state、检查点正文、自动修订或其它项目行。
3. 锁后查询只投影 `id,snapshot_bytes,is_pinned`，按 `created_at DESC,id DESC`，最多读取 21 行以侦测破坏既有 20 条不变量；`is_pinned` 必须用原始 Integer 投影，禁止 ORM Boolean 把非法 `2` 吞成 `true`，也禁止用 `is_(True)` 过滤掉坏行。
4. 必须先完整物化并严格校验候选的 `snapshot_bytes`（原生整数、1..2 MiB）与原始固定值（仅 0/1），再定位目标和计算配额。候选超过 20、任一元数据损坏或执行/flush/commit 失败，均 rollback 并返回固定脱敏 500。
5. 同值请求幂等：不执行固定 UPDATE，不改变配额，但仍以唯一 commit 结束本次事务；反值更新必须以 workspace/project/checkpoint 三谓词命中恰好一行，只写 `is_pinned`，再 flush、唯一 commit。提交后禁止 refresh、补查、创建修订或重载 editor-state。
6. 取消固定不立即触发裁剪；该行在后续手动创建或恢复事务中重新按时间顺序参与普通裁剪。固定/取消固定都不改变 `created_at`、`display_name`、快照、版本、计数或项目时间。

## 4. 创建/恢复保护性裁剪合同

1. `_trim_checkpoints` 仍只在调用方原事务内工作，不自行 commit/rollback/refresh；查询只投影 `id,snapshot_bytes,is_pinned`，禁止读取 `snapshot_json`、`state_version`、名称或 ORM 整实体。
2. 必须完整物化并严格校验本项目全部候选的 `snapshot_bytes` 和原始 `is_pinned` 后再决定删除集合；任一坏行、固定数量超过 5 或固定字节超过 10 MiB 时固定 `editor_state_checkpoint_corrupt`，整次创建/恢复事务回滚，禁止部分 DELETE。
3. 所有固定行无条件进入保留集合。若恢复传入 `protect_id`，该 ID 必须存在于当前项目候选中，并无论固定状态、时间戳或 ID 排序都进入保留集合；缺失保护 ID 固定视为损坏并回滚。
4. 在固定集合和可选安全 `protect_id` 之外，普通非固定行按 `created_at DESC,id DESC` 依次加入，直到总保留数达到 20；其余更旧非固定行删除。检查点仍只有 20 条计数上限，不新增总字节裁剪或跳过新大行保留旧小行。
5. 合法固定上限最多占 5 个槽位；恢复时即使已有 5 条固定，仍须保留本轮安全检查点，并为最新普通检查点留下 14 个槽位。手动创建在 5 固定+15 普通时保留新建项并淘汰最旧普通项。
6. DELETE 必须同时限定 workspace、project、id；不得删除固定集合、`protect_id`、其它项目或其它工作空间。显式 P12H DELETE 不调用本裁剪函数，仍允许删除固定行。

## 5. PATCH 接口与安全合同

唯一新增入口：`PATCH /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}/pin`。

- query 必须为空；JSON 原始体最多 1024 字节且必须为精确一键 `{ "isPinned": true|false }`。空体、null、数组、字符串、snake_case、额外/缺失键、非原生布尔和超限 body 固定 422/no-store，禁止回显输入。
- 成功必须精确返回 `{ "isPinned": true|false }`、200/no-store；不返回 ID、项目、版本、名称、正文、计数、配额或时间。
- 合法请求下，项目/空间不存在固定 404 `project_not_found`；目标不存在或跨项目/空间固定 404 `editor_state_checkpoint_not_found`；超限固定 409 `editor_state_checkpoint_pin_limit`；内部/元数据/事务失败固定 500 `editor_state_checkpoint_pin_failed`。
- required 模式继续复用既有 Cookie 会话、当前 workspace、`bid_writer` 与 CSRF 门；disabled 继续本机测试兼容。不得新增 Token、Cookie、角色、审计内容、日志正文或外网请求。
- 所有成功与业务错误响应 `Cache-Control: no-store`；错误正文只能是固定 `detail.code/message`，禁止泄漏路径参数、请求体、名称、快照、SQL、异常原文、Cookie 或 CSRF。

## 6. 九文件白名单与冻结 SHA-256

| 文件 | 冻结 SHA-256 | 允许变化 |
|---|---|---|
| `backend/app/models/entities.py` | `13D6122DA3C42839472920ED0FB010698AA397CF8BA5F8121748A04120E130BC` | `is_pinned` ORM 列、CHECK 与中文注释 |
| `backend/app/core/database.py` | `70F7D0A911A74AB9D682CEEA9A6CCF8946AB596B8F29CDA9F452BBF23229A2BC` | 检查点 SQLite 幂等重建迁移及调用顺序 |
| `backend/app/services/editor_state_checkpoint_service.py` | `CA117EC759F791C34F9B0ADB4423D07F9D814689AE03F399E1967A1BEE3E60F2` | 固定元数据校验、固定/安全双保护裁剪、初始 false |
| `backend/app/services/editor_state_checkpoint_pin_service.py` | 新文件，不存在 | 单条固定服务、配额、锁和固定错误 |
| `backend/app/api/schemas.py` | `28C324C479F32ED80755DB41853475B55D5090FB6C95251A0F9E50AFC3076230` | 固定请求/响应 Schema |
| `backend/app/api/editor_state_checkpoints.py` | `374EDB65557942B3AB8D86068D8B2BC1ECCB96E083B37B2E7EB756EA861A701F` | 新 PATCH 路由、≤1024 字节解析和固定脱敏错误 |
| `backend/tests/test_editor_state_checkpoints.py` | `92923B9FA88778CE153EC0E63D971638F634717F42B3B9E124EF5C62906FFE42` | 仅更新裁剪 SQL 精确投影基线；其它新增证据优先放新专项 |
| `backend/tests/test_p12j_checkpoint_pin.py` | 新文件，不存在 | 真实 ASGI/SQLite 的迁移、固定、配额、保护裁剪、回滚与安全门 |
| `backend/tests/test_p12h_checkpoint_delete.py` | `043AF230708E88F304CBB280A4D21AD8DACD7295AACDBFF2A5B5B74E91E0DAAD` | 仅在精确 ORM 字段清单中于 `display_name` 后增加 `is_pinned`；禁止改其它字符 |

禁止修改前端、检查点七/八键响应、名称/删除服务、搜索匹配、恢复/修订业务、其它模型/表/索引、依赖/锁文件、其它测试、Git 历史或冻结文档。文档闭环只由 Codex 修改。

## 7. Failure-first 与串行验收门

Grok 必须先只新增/修改三个测试文件，形成真实 failure-first 后才能修改六个生产文件：

1. 新增 `backend/tests/test_p12j_checkpoint_pin.py`，真实 HTTP/SQLite 覆盖请求外壳、权限/CSRF、三重作用域、项目锁、同值、配额、原始坏值、20/21 行侦测、迁移事务、创建/恢复保护裁剪、显式删除固定行、execute/flush/commit 回滚、五域零副作用和泄漏门。
2. 在 `test_editor_state_checkpoints.py` 只把旧“裁剪仅投影 id”机械升级为精确 `id,snapshot_bytes,is_pinned` 原始投影且仍禁止正文/版本/名称；在 P12H 测试只更新 ORM 字段清单。
3. 首个有效红测必须来自 PATCH 缺失、列缺失或保护裁剪不成立；import/收集错误、共享数据库污染、skip/xfail、宽状态码、恒真断言或只读源码字符串不算有效 failure-first。
4. 红测后记录六个生产文件哈希仍等于冻结值，再进入实现。

Grok 与 Codex 均逐条串行；pytest 禁止 xdist、并发分组或与其它测试同时运行：

1. `cd backend && python -m pytest -q tests/test_p12j_checkpoint_pin.py --tb=line`
2. `cd backend && python -m pytest -q tests/test_editor_state_checkpoints.py tests/test_editor_state_checkpoint_restore.py tests/test_p12g_checkpoint_display_name.py tests/test_p12h_checkpoint_delete.py tests/test_p12i_checkpoint_search.py tests/test_p12f_revision_pin.py --tb=line`
3. `cd backend && python -m pytest -q --tb=line`
4. `cd backend && python -m py_compile app/models/entities.py app/core/database.py app/services/editor_state_checkpoint_service.py app/services/editor_state_checkpoint_pin_service.py app/api/schemas.py app/api/editor_state_checkpoints.py tests/test_p12j_checkpoint_pin.py`
5. 仓库根执行 `git diff --check`、精确九文件、空暂存区、最终 SHA-256、SQLite DDL/索引/回滚、SQL 原始投影/三谓词、错误脱敏、零正文投影和零越权扫描。

本包不改前端，禁止为“验收完整”擅自运行或修改 Playwright。后端全量是唯一全仓运行；其余命令均为受影响集合。

## 8. Grok 回执合同

Grok 完成后只通过消息箱发送一个 `review_request`，必须包含：

- failure-first 的真实失败/通过数量、首个业务失败和生产文件未改哈希；
- 每条串行命令、最终精确结果与既有告警；
- 九文件清单、最终 SHA-256、空暂存区、未 commit/未 push；
- 迁移三种旧库、DROP 前后故障回滚、索引/约束保留证据；
- 0/1 原始投影、5 条/10 MiB、20/21 行、手动创建与安全恢复双保护、显式删除固定行证据；
- required/disabled、CSRF、跨项目/跨空间、错误脱敏、五域零副作用与明确未做项。

如额度、网络或进程中断，只发送 `status` 如实说明，禁止伪造 `review_request`、测试数字或完成结论。

## 9. 明确未做

不做 `isPinned` 的 create/list/search/detail 响应扩展、前端固定按钮/标签/状态水合、技术/商务 UI、固定排序/分组、批量固定、配额展示、乐观更新、自动重试、检查点分页/游标、片段/高亮/评分/缓存、跨项目检查点、完整版本时间线、多人协作、presence、SSE/WebSocket、Alembic/PostgreSQL、后台清理或新索引。

## 10. 实施与验收闭环（2026-07-19）

1. Grok 在冻结提交 `9f304da` 上先得到真实 failure-first **16 failed / 3 passed**，首个合法业务失败为 `/pin` 返回 404，六个生产文件哈希保持冻结；首轮完成后专项 **19 passed**、受影响回归 **140 passed**、当时后端全量 **1254 passed**。
2. Codex 独立审查发现三类实质缺口：已有列但可空/无默认值会被迁移误判为最终态；空候选携带 `protect_id` 会静默返回；测试缺少真实 5 固定+15 普通边界并保留宽状态/条件分支。返修任务=`msg_f9bc9783042748b9bad6125c529081c1`，Grok 先得到 **2 failed / 0 passed**，修后专项 **23 passed**、受影响回归 **140 passed**；原任务/review=`msg_e349fbe9fb7148d986fc9e2d9558225a`/`msg_80ce5845baf14fdfbe5fe86caf379305`，返修 review=`msg_3a93a06c7c9b4343813b7069273afd30`。
3. Codex 仅将专项中最后一个宽泛源码二选一断言收紧为两个必备条件，未改 Grok 的生产实现；随后严格串行独立通过专项/受影响回归/后端全量 **23/140/1258 passed**。全量耗时 **1454.53 秒**，仅 1 条既有 Starlette/httpx 弃用告警；本后端包没有运行或修改 Playwright，整仓前端沿用已验收 **318 passed** 基线且不冒充本包重跑结果。
4. `py_compile`、`git diff --check`、精确九文件、空暂存区、原始 Integer 三列投影、三谓词 UPDATE/DELETE、迁移 DROP 前后真实故障回滚、5 条/10 MiB、20/21 行、5 固定+新建+14 普通、5 固定+安全点+14 其它、required/disabled/CSRF/越权/脱敏/五域零副作用均通过。Grok 全程未暂存、提交或推送；实现由 Codex 以 `8edebd4 功能：完成P12J-A检查点固定后端基础` 提交并推送，验收确认=`msg_6e53fde20dd14ddd94a0ca03192531c6`。

最终九文件 SHA-256：

| 文件 | 最终 SHA-256 |
|---|---|
| `backend/app/api/editor_state_checkpoints.py` | `004D93AD0B6AECB1F35BD9A50F6C7FA4547AD83BA7320C3B29B817F195F0D3BC` |
| `backend/app/api/schemas.py` | `B88C85BA8E99FFC68BB5FEC736E8613F687D3B1257D844DFF1205D18D39D31E9` |
| `backend/app/core/database.py` | `F9060BC0784E3B6D3FFFC6E4201D0D02402FFCD6D7B2C744331A9585CFCBED78` |
| `backend/app/models/entities.py` | `695251AEF5742517C95565A1EA018E9581B7A035FB70A6D2F6B2880E00F27728` |
| `backend/app/services/editor_state_checkpoint_pin_service.py` | `B580BF5B6D2B9666F37BF0136F6301DE9D099DFD0A12D669B074B57B0ED91C3F` |
| `backend/app/services/editor_state_checkpoint_service.py` | `45126F09A2E8C28FF6938118A94BDBBCD07AF27DB774F919BDCDB9957390BA59` |
| `backend/tests/test_editor_state_checkpoints.py` | `A8536393D2D664E8D4A4F465C513EC7E2CAF2E90990E17B79A21BDAC77210B85` |
| `backend/tests/test_p12h_checkpoint_delete.py` | `721932631233E3583197363770B96D6BB0AABCF5AC004F753A3A0B970431D87C` |
| `backend/tests/test_p12j_checkpoint_pin.py` | `B00FAB634706AAD7BD345DBF6C2A58D6F2176E816DC3609D0DF4C3DDC678EB91` |
