# P12G 手动检查点展示名称契约

模块：P12G editor-state 手动/安全检查点展示名称、严格读取与双工作区共用命名入口

用途：让用户为现有手动检查点或恢复前安全检查点设置、覆盖和清除展示名称，从而在最近 20 条检查点中识别关键版本；名称不参与快照、恢复、排序或裁剪。

对接：`backend/app/models/entities.py`、`backend/app/core/database.py`、检查点 Schema/路由/服务、技术标与商务标共用检查点面板、Grok-Codex 协作消息箱。

二次开发：本包只允许本文第 9 节十二文件；Grok 只实现与自测，不暂存、不提交、不推送；Codex 独立审查、串行验收、中文文档闭环并推送协作分支。

## 1. 选择理由与产品边界

1. P12A/P12B 已提供每项目最近 20 条手动/安全检查点、按需详情和安全恢复，但列表只有时间、大小和计数，用户无法区分“投标前确认版”“报价复核前”等关键节点。
2. P12F-H 已验证展示名称的 Unicode、脱敏、单列更新和迟到隔离模式；检查点属于另一张表、另一套 20 条配额和另一块共用面板，必须独立冻结，不能复用修订 API 或裁剪域。
3. 本包不改变创建流程：`POST /editor-state-checkpoints` 请求仍精确 `{}`，新检查点与恢复前安全检查点初始 `displayName=null`；用户只能在创建完成后显式命名。
4. 本包完成后，创建响应、列表项和详情元数据统一增加 `displayName`。列表/创建为精确七键，详情为七键加 `snapshot`；恢复响应仍精确四键。
5. 名称只是检查点行的可见元数据：不进入快照 JSON、`stateVersion`、恢复写回、修订、排序、配额或裁剪判断；恢复某检查点不会把名称复制到当前 editor-state、新修订或新安全检查点。

## 2. 数据模型与 SQLite 迁移

1. `EditorStateCheckpointRow` 新增 `display_name: VARCHAR(160) NULL`，ORM 默认值与数据库存量语义均为 `NULL`；不新增索引、约束、表或依赖。
2. `database.py` 新增独立幂等迁移函数：
   - 仅 SQLite 生效；
   - 表不存在时 no-op，交由 `create_all` 使用新 ORM 建列；
   - `PRAGMA table_info(editor_state_checkpoints)` 已含 `display_name` 时 no-op；
   - 否则只执行字面量 `ALTER TABLE editor_state_checkpoints ADD COLUMN display_name VARCHAR(160)`；
   - 失败必须从 `ensure_schema_columns` 的唯一外层事务抛出，回滚并阻止启动，禁止吞异常继续。
3. 迁移在既有修订表迁移之后或之前均不得改变其顺序语义；只要求检查点迁移在同一 `active_engine.begin()` 中被显式调用。
4. 旧库迁移、全新库建表、重复迁移和迁移中途失败必须有真实 SQLite 证据；既有检查点表精确列集合测试机械增加 `display_name`。

## 3. 名称值合同

`displayName` 只接受原生 JSON 字符串或 `null`：

1. `null` 表示清除名称，持久化为 SQL `NULL`。
2. 字符串先拒绝空串和首尾空白，再执行 Unicode NFKC；规范化后再次拒绝空串和首尾空白。
3. 规范化后长度为 1–40 个 Unicode 码点；40 个非 BMP 字符合法，41 个非法，不按 UTF-16 单元或 UTF-8 字节误算。
4. 原值和规范化值均拒绝 C0、DEL、C1、U+2028/U+2029，以及 ALM/LRM/RLM、LRE/RLE/PDF/LRO/RLO、LRI/RLI/FSI/PDI 双向控制字符。
5. 数字、布尔、数组、对象、缺键、额外键、snake_case、空 body、非对象 JSON、坏 JSON、超过 1024 字节原始 body 均固定 422，禁止 Pydantic 默认错误暴露 `loc/input/type/url` 或原始名称。
6. 合法全角兼容字符按 NFKC 后的结果保存并响应；错误消息不得反射输入。

## 4. 后端 PATCH 合同

唯一新路由：

`PATCH /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}/display-name`

1. query 必须为空；请求 body 精确 `{ "displayName": string|null }`，原始体上限 1024 字节。
2. 成功固定 HTTP 200、`Cache-Control: no-store`，响应精确 `{ "displayName": string|null }`，值等于服务端规范化并实际存储的值。
3. 认证、当前 workspace 与 strict `bid_writer` 权限复用现有中间件/依赖；Cookie、CSRF 和角色语义不得旁路或复制实现。
4. 固定错误：
   - 项目不存在或跨 workspace：404 `project_not_found`；
   - 检查点不存在、跨项目或跨 workspace：404 `editor_state_checkpoint_not_found`；
   - 名称值非法：422 `editor_state_checkpoint_display_name_invalid`；
   - query/body 外壳非法：422 `editor_state_checkpoint_display_name_request_invalid`；
   - execute/flush/commit、异常 rowcount 或内部故障：500 `editor_state_checkpoint_display_name_error`。
5. 所有错误只返回固定 `code/message` 且 `no-store`；不得回显 ID、路径、名称、SQL、异常原文、请求体、Cookie、CSRF 或快照。
6. 集合与普通详情路径既有 PUT/PATCH/DELETE 405 语义保持不变；仅精确 `/display-name` 子路径开放 PATCH，其它方法仍 405。

## 5. 后端写服务与事务边界

新增 `editor_state_checkpoint_name_service.py`，不得把修订名称服务的异常类型或错误码直接泄漏到检查点域：

1. 先以 `SELECT Project.id` 和 `workspace_id/project_id` 确认项目；禁止加载 Project 整实体。
2. 以单条 SQL UPDATE 同时限定 `workspace_id/project_id/checkpoint_id`，且 `.values()` 只写 `display_name`；禁止先按裸 ID 加载 ORM、禁止读取 `snapshot_json`。
3. `rowcount == 0` 才映射检查点 404；`rowcount == 1` 才成功；`None/-1/2` 或任意非 1 值固定 500，不得用 `int(rowcount or 0)` 把未知值误当 0。
4. 成功路径为 execute → flush → 唯一 commit；commit 后禁止 refresh、SELECT、详情 GET、列表重载、审计、修订或其它写入。
5. 业务错误和任意运行时异常都必须 rollback；execute/flush/commit 故障后名称和其它五域状态保持原值。
6. 名称写入不得加载/修改当前 editor-state、响应矩阵、检查点快照、自动修订、财务、人力、投标人数据，也不得获取项目写锁或生成新版本。

## 6. 既有检查点读取与恢复不变量

1. `_meta_from_row`、创建返回、列表显式投影和详情输出统一带 `display_name`；创建和安全检查点插入仍不接受名称参数，初始值只能由列默认 `NULL` 产生。
2. 列表 SQL 只比原来多投影 `display_name`，继续禁止 `snapshot_json`，继续按 `created_at DESC,id DESC`，固定 `LIMIT 20`。
3. 详情仍按 checkpoint/workspace/project 三重作用域读取并完整校验规范快照；名称只随元数据返回，不参与快照合法性、字节、计数或版本计算。
4. `_trim_checkpoints` 继续只选择 ID 并按原规则裁到最近 20 条；命名不能保护检查点免于淘汰，也不能改变顺序。
5. 恢复前安全检查点固定 `displayName=null`；恢复命名检查点不得把名称复制到安全检查点、当前状态或自动修订。恢复请求/响应和唯一 editor-state GET 语义保持冻结。

## 7. 前端 API 与共用面板合同

1. `EditorStateCheckpointMeta` 与 `META_KEYS` 从六键升为精确七键，增加 `displayName: string|null`；缺键、额外键、非字符串非 null、空白/控制/双向字符或超过 40 码点均拒绝。
2. 新增单一 API helper：
   - URL 为精确 `/projects/{projectId}/editor-state-checkpoints/{checkpointId}/display-name`，无 query；
   - body 精确 `{displayName: normalized|null}`；
   - 成功响应精确一键且值必须与请求目标全等，否则按失败处理；
   - 不重试、不轮询、不缓存。
3. 技术标与商务标继续共用 `EditorStateCheckpointPanel`，不修改两个页面或 hook。非空名称以 React 文本显示；提供“命名/重命名”、输入、保存、清除、取消，取消和只输入均零请求。
4. 成功只原位更新目标列表项的 `displayName`；不得触发 checkpoint list/detail/restore、editor-state GET/PUT、revision、页面导航或创建。失败显示固定中文并保留原名称、输入草稿、列表和确认状态。
5. 名称操作与列表加载、刷新、创建、恢复、折叠以及其它行名称操作互斥；在名称请求在途时相关按钮必须真实 disabled。同步 ref 必须在第一个 await 前关门，双击/连续点击只能产生一次 PATCH。
6. 迟到隔离至少绑定 mounted、projectId、session、name generation、checkpointId。A 项目旧 success/catch/finally 在切到 B 并开始新操作后，不得污染 B 的名称、消息、busy 或解锁 B。
7. checkpointId/stateVersion/名称/后端错误/CSRF 不得进入 URL、浏览器存储、Cookie、console、剪贴板、下载或外网；名称只允许出现在同源 PATCH body 与 React 文本。
8. 既有 history E2E 对检查点 POST 的严格 mock 必须机械增加 `displayName:null`，不得借机改变修订历史行为。

## 8. 测试与反假绿门

### 8.1 failure-first

1. Grok 先只改两个既有测试文件并新增后端专项测试，生产文件哈希必须保持冻结；先运行后端专项与 P12G 前端 grep，记录真实 failed/passed/did-not-run 和首个业务失败。
2. 红测必须证明至少一个真实缺口：数据库无列、新 PATCH 404/405、元数据缺 `displayName`、前端无命名入口。不得用恒真断言、仅源码字符串、未挂路由或手工 `throw` 伪造。
3. failure-first 期间 `frontend/e2e/editor-state-revision-history.spec.ts` 只允许机械补检查点 mock，不新增 P12G 主断言；P12G 主 E2E 必须进入 checkpoint restore 既有套件。

### 8.2 后端验收

必须覆盖：全新/旧库/幂等/失败回滚迁移；精确列集合；创建/list/detail 七/八键；合法保存/覆盖/NFKC/清除；原生类型、空白、码点、C0/C1/DEL/双向字符；query/body 上限与固定脱敏；跨空间/项目；精确单列 UPDATE；异常 rowcount；execute/flush/commit 回滚；commit 后零查询；列表无正文投影、排序/20 条裁剪不变；恢复不复制名称；五域零副作用。

### 8.3 前端验收

必须覆盖：严格七键 parser；技术/商务保存、覆盖、清除、取消；精确一次 PATCH 与同值响应；成功原位更新且零列表/详情/恢复/editor-state 请求；失败保值；双击单飞；操作互斥；A→B 旧 success/catch/finally 隔离；URL/存储/Cookie/console/外网零泄漏；既有 61 条 history 与 51 条 checkpoint 基线不被破坏。

### 8.4 有效命令必须逐条串行

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12g_checkpoint_display_name.py tests\test_editor_state_checkpoints.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoint_restore.py tests\test_p12c_checkpoint_restore_revisions.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile app\models\entities.py app\core\database.py app\api\schemas.py app\api\editor_state_checkpoints.py app\services\editor_state_checkpoint_service.py app\services\editor_state_checkpoint_name_service.py tests\test_p12g_checkpoint_display_name.py tests\test_editor_state_checkpoints.py

cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --grep "P12G" --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0
npx playwright test e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0
npm run lint
npm run build
```

全量后端和整仓前端最多各做一次有效验收；不得与其它 pytest/Playwright 命令并发。若聚焦和受影响回归全部通过，是否重复整仓前端由 Codex 按改动风险与用户“避免无止境重复测试”的要求决定，并在交付文档中如实记录。

## 9. 严格十二文件白名单与冻结哈希

现有十个文件的冻结基线为 HEAD `dfb6b1eacb2e31c8d00d450ec57514caba674961`：

| 文件 | 冻结 SHA-256 |
|---|---|
| `backend/app/models/entities.py` | `626E7AE2996A20C11675B7D6FE77D66DDF4C762379B2C68A139D0471A8A76281` |
| `backend/app/core/database.py` | `AFEF2ABC987FA67EE281296539ABA0085E5C9F48A85F1E61ECBD52A3C4055D4B` |
| `backend/app/api/schemas.py` | `65A5E879E0201E9FAF22F16A5B2914219BDE3FF386C8106FB4BADA338CBD5BE5` |
| `backend/app/api/editor_state_checkpoints.py` | `4EA0E96E43323C9C822CD9F1C4A978CBFA804D3C619C9A57745EDDDA37653799` |
| `backend/app/services/editor_state_checkpoint_service.py` | `585500D4E9652BE4B9270C0829251F1B29402707337C040C34923C90F5E6A902` |
| `backend/tests/test_editor_state_checkpoints.py` | `3BA03B8C093F97CA0236EB089E4EF6D4041722D40F046DBC2EB9C284436DBF71` |
| `frontend/src/features/editor-state-checkpoints/editorStateCheckpointApi.ts` | `E8041EF23C5041335550039F778A6E306188E8A6E4E945D2F30954A3F1690519` |
| `frontend/src/features/editor-state-checkpoints/EditorStateCheckpointPanel.tsx` | `A8C2DD4C9120B3BD326F1E0D5CC5812E06B1D95D2BC88E9A0C1FB38F4C3D2545` |
| `frontend/e2e/editor-state-checkpoint-restore.spec.ts` | `BBB32F68040706F73559924C83D6E895FB71DAF246BD954DA5D8FBB0604266BB` |
| `frontend/e2e/editor-state-revision-history.spec.ts` | `6FCB317644AEC24C38532CAD4338BCD4B7DA4AD0A2AB9CDE332D70744FED3A50` |

允许新增：

- `backend/app/services/editor_state_checkpoint_name_service.py`
- `backend/tests/test_p12g_checkpoint_display_name.py`

除上述十二文件外，任何生产代码、测试、页面、hook、CSS、共享请求层、配置、依赖、脚本或文档改动都必须先向 Codex 发 `question`，经重新冻结后才能继续。Grok 不修改本契约、计划、路线图、交接或联调清单。

## 10. 明确未做

不做创建时命名、自动名称、名称唯一性、名称搜索/排序/筛选、检查点固定/删除/下载/分享、批量命名、标签/备注、恢复复制名称、跨项目检查点、完整时间线、自动检查点历史、多人协作、审批/审计扩展、SSE/WebSocket、PostgreSQL/Alembic、索引/依赖变更，也不修改 P12F 修订名称/固定能力。
