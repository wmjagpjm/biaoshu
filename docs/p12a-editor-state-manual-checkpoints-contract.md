<!--
模块：P12A editor-state 手动检查点只读库契约
用途：冻结技术标/商务标完整编辑态的显式服务端检查点、有限列表和按需只读详情边界。
对接：ProjectEditorStateRow；GET|PUT /api/projects/{id}/editor-state；P11B/P11C 服务端单一真值；P12B 恢复前置基础。
二次开发：本包不是自动版本历史或恢复功能；不得拦截全部写入、接受客户端快照、静默恢复或复用 M3-D 融合批次。
-->

# P12A editor-state 手动检查点只读库契约

> **状态**：已实现、独立验收并推送；实现提交=`9f53d92`。
> **工作分支**：`collab/grok-code-codex-review`。
> **前置基线**：P11B/P11C 已让商务标、技术标工作区只认服务端 editor-state；M3-D 只覆盖融合写入有限恢复，明确不是通用版本库。

## 1. 审计结论与方案选择

当前 editor-state 不只有浏览器 PUT：异步轻量解析、个人 callback、P8C 一次性 callback、模板新建和 M3-D 原子确认/消费都会直接创建或修改 `ProjectEditorStateRow`，其中部分路径必须和任务、票据或恢复批次保持既有同事务。若现在给所有写入自动建版本，会同时扩大多条已验收事务链；若直接做恢复，又缺少覆盖全字段的统一乐观版本，可能被较早发起、较晚提交的 autosave 或后台任务覆盖。

P12A 因此只交付**用户显式创建、服务端读取、有限保留、只读浏览**的检查点基础：

1. 客户端只提交空对象 `{}`，服务端在项目锁内读取当前权威 editor-state，自行构造快照；绝不接受客户端正文、标题、矩阵、商务字段、版本号或名称。
2. 每项目最多保留最近 20 个检查点；列表只返回元数据，详情必须按检查点 ID 显式读取才返回完整快照。
3. P12A 不修改当前 editor-state，不提供恢复、删除、下载、差异、命名、自动保存钩子或前端入口；P12B 必须先冻结全状态并发版本与恢复事务，不能借本包接口静默覆盖。

## 2. 数据模型与快照边界

新增表 `editor_state_checkpoints`，字段固定为：

| 字段 | 规则 |
|---|---|
| `id` | 服务端生成 `escp_` + 32 位小写十六进制，不透明主键 |
| `workspace_id` | 当前已验证工作空间，外键级联删除并索引 |
| `project_id` | 当前已验证项目，外键级联删除并索引 |
| `snapshot_json` | 服务端生成的规范快照，非空且 UTF-8 字节不超过 2 MiB |
| `state_version` | `esv_` + 32 位小写十六进制；对规范快照 JSON 做 SHA-256 后取前 32 位 |
| `snapshot_bytes` | 1～2 MiB，数据库 CHECK |
| `outline_node_count` | 非负整数，数据库 CHECK |
| `chapter_count` | 非负整数，数据库 CHECK |
| `created_at` | 服务端 UTC 时间，索引 |

复合索引固定为 `(workspace_id, project_id, created_at, id)`。项目或工作空间删除时检查点级联删除；不提供软删除、保留期配置、跨项目查询或 Alembic。本项目现有启动期 `Base.metadata.create_all()` 负责新表，禁止顺带修改旧表列。

规范快照只能有以下 13 个键，排序序列化时使用紧凑 UTF-8 JSON：

`outline`、`chapters`、`facts`、`mode`、`analysis`、`responseMatrix`、`guidance`、`parsedMarkdown`、`businessQualify`、`businessToc`、`businessQuote`、`businessCommit`、`analysisOverview`。

不得保存 `projectId`、项目/工作空间名称、`updatedAt`、派生 `responseMatrixVersion`、用户/成员、Cookie、CSRF、API Key、文件路径、任务、M3-D 批次、审计或客户端投稿字段。快照值必须来自 `editor_state_service.get_editor_state()` 的服务端规范输出；`responseMatrix` 因此使用已收敛版本。空项目也可创建检查点，快照必须是稳定的权威空态而不是 `null` 整包。

检查点不是去重接口：用户每次明确创建都生成新记录；并发创建必须由项目锁串行，创建、裁剪旧记录和 commit 同一事务，任一失败零新增、零误删。超过 2 MiB 固定失败并零写入。

## 3. API 与权限

新增独立路由：

1. `POST /api/projects/{projectId}/editor-state-checkpoints`
   - 请求体必须精确为空对象，额外字段固定 422；required 模式继续由现有中间件校验 CSRF。
   - 支持当前空间 `technical|business` 项目；disabled 保持个人版，required 只允许 strict `bid_writer`，其他角色不因 owner 身份绕过。
   - 成功 201，只返回 `checkpointId/stateVersion/snapshotBytes/outlineNodeCount/chapterCount/createdAt`，并带 `Cache-Control: no-store`。
2. `GET /api/projects/{projectId}/editor-state-checkpoints`
   - 固定最近 20 条，`created_at DESC, id DESC`；顶层只能有 `items`，每项字段与创建响应相同。
   - SQL 只投影元数据列，不得读取/解析 `snapshot_json` 后再丢弃；响应 `no-store`。
3. `GET /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}`
   - 成功只返回上述元数据加 `snapshot`；详情从数据库解析后必须验证对象、精确键集、版本和字节数一致，损坏数据固定 500 脱敏错误。
   - 跨项目、跨空间、不存在统一 `404 editor_state_checkpoint_not_found`，不反射 ID；响应 `no-store`。

项目不存在或跨空间统一 `404 project_not_found`；角色和工作空间错误沿用现有认证固定码。不得新增 `PUT/PATCH/DELETE/restore/download/export/search`，不得通过 query 选择其他项目、数量、游标或全文。

## 4. 锁、事务与资源边界

创建检查点前必须锁定当前项目：SQLite 对 `projects` 行执行无副作用 UPDATE 取得文件库写锁；PostgreSQL 等对 project 与已存在 editor-state 行使用 `FOR UPDATE`。锁后重新读取 editor-state，再构造快照。该锁用于让同项目并发检查点稳定裁剪，不宣称已经让所有既有写路径获得全状态乐观锁。

计数必须有上限友好的迭代实现：`outlineNodeCount` 只统计 outline 树中的字典节点，`chapterCount` 只统计 chapters 列表中的字典项；不得递归爆栈、无界复制或把客户端异常结构写回当前状态。序列化后先按 UTF-8 字节检查 2 MiB，再写表。

创建失败、序列化失败、超限、数据库异常或裁剪失败必须 rollback；不得先 commit 检查点再清理。列表不得拖取最多 40 MiB 的正文；详情单次最多返回一个已验证的 2 MiB 快照。

## 5. 精确文件白名单

P12A 后端只允许 Grok 修改/新增以下七文件：

- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/main.py`
- `backend/app/api/schemas.py`
- `backend/app/api/editor_state_checkpoints.py`（新增）
- `backend/app/services/editor_state_checkpoint_service.py`（新增）
- `backend/tests/test_editor_state_checkpoints.py`（新增）

禁止修改 `editor_state_service.py`、`projects.py`、认证/CSRF/数据库基础设施、M3-D、任务/解析/模板服务、前端、依赖/锁文件、既有测试和文档；不得 commit/push。

## 6. 反假绿验收

后端测试至少覆盖：

- 技术标与商务标完整规范快照；空态稳定；13 键精确，派生/敏感字段不存在；`stateVersion` 用独立重算精确相等；
- 额外请求字段 422；required strict `bid_writer`、CSRF、其他角色/owner、disabled、跨空间/项目隔离；
- 创建响应、列表元数据、详情字段和 `no-store`；列表真实 SQL 投影不含 `snapshot_json`；
- 21 次创建后当前项目精确保留 20 条、稳定倒序，其他项目/空间不误删；并发创建最终不超过 20；
- 2 MiB 边界、超限零写、序列化/insert/裁剪异常回滚；数据库 CHECK、复合索引和项目级联删除真实生效；
- 详情跨项目/空间/不存在统一 404；损坏 JSON、错误键集、字节数或版本固定 500 且不泄漏历史正文/路径/SQL；
- 未实现 restore/PUT/PATCH/DELETE 路径不能返回伪成功；既有 editor-state、M3-D、P8C 与技术/商务标回归继续通过。

禁止 `or True`、捕获后忽略、只断言非空、宽泛 `in` 状态码、用 mock 列表假装 SQL 投影、顺序调用假装并发、或客户端自造快照绕过权威读取。

## 7. 非目标与后续 P12B 闸门

- 不自动记录每次 autosave、任务、解析、callback、模板新建或 M3-D 写入；不声称是完整历史。
- 不恢复、回滚、覆盖或比较当前 editor-state；不提供“恢复”按钮或隐藏 API。
- 不做命名版本、标签、发布、审批、分支、合并、协作者身份、跨项目浏览、搜索、导出、下载或无限保留。
- 不修改响应矩阵现有版本语义；`stateVersion` 只描述检查点规范快照，不能冒充当前 PUT 的全状态乐观锁。
- P12B 若实现恢复，必须先覆盖普通 PUT 与后台/回调写入的并发顺序，冻结 expected current state version、恢复前安全检查点、原子写入和迟到 autosave 防护；不得直接复用本包 `stateVersion` 静默覆盖。

## 8. 实现与独立验收结论

P12A 严格按七文件白名单落地，新增独立 `editor_state_checkpoints` 表以及 POST 创建、GET 元数据列表、GET 单条详情三条路由。服务端只从锁内 `get_editor_state()` 权威输出抽取精确 13 键，使用 UTF-8 紧凑排序标准 JSON、2 MiB 上限和 `esv_` 摘要；同项目创建、裁剪最近 20 条与提交保持同一事务。列表和淘汰查询均不加载历史正文，跨项目详情在 SQL 中同时限定检查点、工作空间与项目。

Grok 首版经 Codex 两轮拒绝后修复：首轮消除淘汰加载完整正文、提交后 `refresh` 假失败、非规范 JSON 放行、错误元数据异常泄漏和跨项目先加载正文；第二轮把项目锁、权威读取、序列化、计数、插入、裁剪与提交全部纳入统一显式回滚域，并以 `allow_nan=False` 拒绝 `NaN/Infinity` 非标准 JSON。正式协作回执依次为原任务 `msg_b1d4a03f493e4edc909eea632b60133a`、首轮返修 `msg_2248b407df6a4747aca0b0860e93bcf0`、第二轮返修 `msg_38b36fcf84284344b59407d28b153aa4`、Codex 验收 `msg_2d76b0ced0c749fca11edbccdf4dc20c`。

Codex 独立验收：P12A 专项 **29 passed**、editor-state/认证/项目/M3-D/模板受影响回归 **97 passed**、P8C 与异步 callback 链 **15 passed**、后端串行全量 **518 passed**；均只有 1 条既有 Starlette/httpx 弃用警告。`git diff --check` 与暂存区 whitespace 检查通过。P12A 没有前端改动，也没有实现恢复、删除、下载、自动历史或并发版本。
