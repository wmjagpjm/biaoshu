# P12F-H 单条修订命名契约

模块：P12F-H 技术标/商务标共用自动修订单条命名
用途：为有限自动修订增加可选展示名称，提供严格单条写接口、列表呈现、清除名称和跨项目迟到隔离。
对接：`EditorStateRevisionRow`、修订 history/name service、`editor_state_revisions` 路由与 schema、共用修订 API/面板/history E2E。
状态：2026-07-18 已完成只读审计；本文与实施计划提交推送后冻结十文件边界并允许 Grok failure-first 实现。Grok 只实现/自测、不暂存、不提交、不推送；Codex 负责独立审查、验收、中文闭环和协作分支推送。

## 1. 选择与边界

1. P12F-G 已闭合单条删除；版本治理剩余候选中，命名只改变单条修订的展示元数据，不触碰快照、当前 editor-state、检查点、恢复、来源、时间、游标或搜索候选范围，适合作为下一最小主线包。
2. 本包只做“可选展示名称”，不做固定/置顶。名称不会阻止 P12F-A 的 20 条/20 MiB 裁剪；被裁剪或删除的修订连同名称消失。若未来做固定，必须另立保留与配额契约。
3. 不增加独立名称表：在 `editor_state_revisions` 增加 nullable `display_name VARCHAR(160)`；新修订默认 null，恢复不复制名称。
4. 列表、page、search 和 detail 的元数据统一由五键扩展为六键，多出 `displayName: string | null`。search 仍只按 P12F-F-A 的既有可见快照字段匹配，名称不是本包的搜索目标。
5. 必须同包更新后端、严格前端 parser 和共用 UI，禁止先推送会让既有前端因响应 extra key 失败的半成品。

## 2. 数据与迁移合同

1. `EditorStateRevisionRow.display_name` 为 nullable 字符串；ORM 长度 160 只限制存储字节上界的防御，不替代服务端码点校验。
2. SQLite 个人版在 `ensure_schema_columns` 中于九来源 CHECK 迁移完成后幂等执行：`ALTER TABLE editor_state_revisions ADD COLUMN display_name VARCHAR(160)`。列已存在可忽略；迁移本身失败继续阻止启动。
3. 新库由 `create_all` 直接建列；旧库原八列及快照、版本、来源、时间、索引和行数保持不变，存量名称为 null。
4. 不新增索引、表、外键、依赖或配置；不得重写旧来源 CHECK 迁移为吞异常路径。

## 3. 名称规范

请求体顶层精确一键：`{"displayName": <string|null>}`。

- `null`：清除名称；重复清除允许成功。
- 字符串：必须原生 string，首尾无空白；NFKC 规范化后长度 1..40 个 Unicode 码点；规范化结果仍须首尾无空白。
- 拒绝空串、纯空白、超过 40 码点、C0/C1 控制字符、换行/制表/NUL、U+2028/U+2029 和 Unicode 双向控制字符。
- 服务端只存 NFKC 结果。错误固定 `editor_state_revision_display_name_invalid / 修订名称无效`，不得反射名称、ID、路径、类型、位置或原始 Pydantic `input`。

名称是用户主动输入并预期显示的文本；前端必须使用普通 React 文本渲染，不得使用 HTML 注入。名称不得进入 URL、浏览器存储、console、错误文案、Cookie、CSRF、日志或审计 target。

## 4. 写接口与事务

新增：

`PATCH /api/projects/{projectId}/editor-state-revisions/{revisionId}/display-name`

成功返回精确 `200 {"displayName": string|null}`，并固定 `Cache-Control: no-store`。

1. query 必须为空；body 原始长度固定不超过 1024 bytes，且必须是可解码 JSON 对象并只含 `displayName`。缺失、extra、snake_case、数组、标量、非法 JSON或超限 body 均固定脱敏 422。
2. required 模式继续统一 workspace/成员/bid_writer 与 CSRF；disabled 兼容不变。不得新增公开路径或绕过中间件。
3. 服务先只投影 `Project.id` 验证当前 workspace/project；再以 workspace/project/revision 三谓词单条 UPDATE `display_name`，禁止加载 `snapshot_json` 或 ORM 整实体。
4. 修订不存在或跨项目/空间固定 404 `editor_state_revision_not_found / 修订不存在`；项目不存在沿用 `project_not_found / 项目不存在`。不能先全局按 revisionId 查询再 Python 判权。
5. 成功唯一 commit；任意 execute/flush/commit 异常显式 rollback 并固定 500 `editor_state_revision_display_name_error / 保存修订名称失败`。`rowcount=None`、负数或非 1 不得冒充 404/成功。
6. 只允许修改 `display_name`；快照、state version、snapshot bytes、source kind、createdAt、当前 editor-state、检查点、其它修订和项目均不变。不创建新自动修订，不触发恢复、删除或审计扩展。

## 5. 读取合同

1. `EditorStateRevisionMetaOut` 增加 `displayName`，值只允许 null 或符合第 3 节的已规范字符串；list/page/search/detail 全部复用六键元数据。
2. history service 的 list/page/search 投影可增加 `display_name`，仍禁止列表路径读取 `snapshot_json`；detail 既有快照读取与严格校验不变。
3. 任一数据库名称类型/长度/字符不合法，整次响应固定既有 `editor_state_revision_corrupt / 修订数据损坏`，不得静默 trim、替换或回显坏值。
4. page 排序、游标、来源/时间条件、LIMIT 11、search 候选 20 与正文匹配语义完全不变；`displayName` 不进入 cursor，也不改变搜索命中集合。
5. P12F-A 裁剪、G-A 删除、C2 恢复及所有 revision recorder 不需读取名称；新插入行依赖 nullable 默认 null。

## 6. 前端合同

### 6.1 API

- `EditorStateRevisionMeta` 与严格 parser 精确增加 `displayName: string | null`，前端按与服务端一致的可见安全规则校验；非法六键响应固定失败。
- 新增 `setEditorStateRevisionDisplayName(projectId, revisionId, displayName): Promise<string|null>`；合法 revisionId、合法名称/null，精确 PATCH JSON body，禁止 query/retry/轮询/额外 header。
- 响应严格精确一键 `displayName`，必须等于请求的规范值；不返回/接受 revisionId、版本或后端 detail。

### 6.2 共用面板

1. 每行显示可选名称；无名称不伪造“版本 1”等本地名字。提供“命名”，进入内联输入；已有名称作为初值。
2. 输入本身零请求；“取消”零请求且不改列表；“保存”只接受前端可判定合法非空名称并精确一次 PATCH；“清除名称”仅已有名称时可用并发送 null。
3. 固定状态文案：在途“保存名称中…”；成功“修订名称已保存”或“修订名称已清除”；失败“保存修订名称失败，当前名称已保留”。不得显示后端 detail 或用户输入。
4. 成功只原位更新当前已加载条目的 `displayName`，不重载 page/search，不改变顺序、cursor、草稿/已应用筛选，不触发 editor-state、restore、checkpoint 或 DELETE。失败保留原名称与列表。
5. 命名确认/在途与摘要、当前对比、单双正文差异、双选、恢复、删除、刷新、筛选、搜索、加载更多、折叠互斥；确认期间除输入/保存/清除/取消外其它控制真实 disabled。
6. 使用 mounted + project/session + name generation + revisionId 守卫 success/catch/finally。A 在途切 B 后，A 的成功/失败/finally 不得污染 B；旧 finally 不得解锁 B 新一轮命名。
7. 名称只在 React 内存和普通文本 DOM 中出现；不写 URL/localStorage/sessionStorage/Cookie/console，不使用 `dangerouslySetInnerHTML`。

## 7. Failure-first 与自动化

Failure-first 只允许新建后端专项测试和修改既有 history E2E；八个生产文件保持冻结哈希。

### 7.1 后端专项至少覆盖

1. 新路由真实 404 红测；合法保存/读取/覆盖/清除/重复清除；响应精确一键/no-store。
2. 缺失/extra/snake_case/非法 JSON/非对象/空白/41 码点/控制字符/双向字符固定脱敏 422，原始 marker 不出 body/header/log。
3. URL 编码、query 非空、跨项目、跨空间、角色、无会话、缺/错 CSRF；项目 404 与修订 404 优先级。
4. SQL/AST 证明项目只投影 id、UPDATE 三谓词且只写 display_name、零 snapshot_json/整实体、唯一 commit、失败 rollback；`rowcount=None/0/2` 精确映射。
5. list/page/search/detail 六键、坏名称 corrupt、page cursor/排序/筛选与 search 命中集合不变；新修订 null、恢复不复制、删除与裁剪自然移除。
6. SQLite 旧库幂等加列，八来源迁移后列存在、行数/八原字段/索引不变；迁移失败仍阻止启动。

### 7.2 前端 E2E 至少三个独立用例

1. 技术标保存/覆盖/清除/失败：输入与取消零 PATCH；精确 method/path/query/body/CSRF；成功原位六键呈现且零重载，失败保值；非法输入前端零请求。
2. 技术标互斥与迟到：命名确认清除其它意图；所有非命名控件真实 disabled；A hold 后切 B 并开始 B，释放 A success/catch/finally 不污染或解锁 B。
3. 商务标共用与数据最小化：同一入口；名称以 React 文本显示，HTML marker 不执行；URL/存储/Cookie/console 无泄漏；editor-state GET/PUT、restore、checkpoint、DELETE、page/search 重载和外网旁路均为零。

关键断言必须精确。禁止 `.or(...)`、`>=1`、宽状态、条件断言、固定 sleep、skip/xpass、吞异常、只等 arrived 不等 complete、route fallback 假成功、`force:true`、可选首项、`Math.min` 截断或 `A || []` 掩盖夹具缺失。

## 8. 十文件白名单与冻结哈希

Grok 只允许修改/新建：

1. `backend/app/models/entities.py` — `2C19028EBF3292CDE069E5D034E880593D1313185643E0AA827109A8ED96BCDE`
2. `backend/app/core/database.py` — `092D73A4448662CD298B886561445B1F6A89A5ABA2C16E223E22B5107A3E7EC0`
3. `backend/app/api/schemas.py` — `474680ECEC41BEACACE624A6F154B5951167C1EEC23AEF4D48AAC708CD277221`
4. `backend/app/api/editor_state_revisions.py` — `71E61A18822A4E79BAEEA7A7CB93F0A7612DD02D9F29CC997C484786687EF76D`
5. `backend/app/services/editor_state_revision_history_service.py` — `8EACFAD08E213B14F8FF3FC5A3DBE93F3F9A17D02BCA282FF79BF8D51C350B2C`
6. `backend/app/services/editor_state_revision_name_service.py` — 冻结时不存在
7. `backend/tests/test_p12f_revision_name.py` — 冻结时不存在
8. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts` — `260589B9D02F8B88E3A8FDF8A19CA9BB7C03B3645D9072A612A5E7B55AF6DDAD`
9. `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx` — `DDAF690B6A310171144168ACAD113BF335EAB070D5918F2CA14173497EB1CE37`
10. `frontend/e2e/editor-state-revision-history.spec.ts` — `6797E9BBA85FEBDD2F603709556DCB85F78CF44C03B638245C2AD28CA6CB60DD`

禁止修改其它后端/测试、共享 `apiFetch`、workspace hook、检查点、配置、依赖/锁文件、文档或 Git 历史。Grok 不得 `git add/commit/push`。

## 9. 串行验收门

Grok 与 Codex 分别逐条串行运行：

1. `python -m pytest -q tests/test_p12f_revision_name.py`
2. `python -m pytest -q tests/test_p12c_revision_history_read.py tests/test_p12f_revision_cursor_page.py tests/test_p12f_revision_source_filter.py tests/test_p12f_revision_time_range_filter.py tests/test_p12f_revision_content_search.py tests/test_p12f_revision_delete.py`
3. `python -m pytest -q tests/test_editor_state_revisions.py tests/test_p12c_revision_restore.py tests/test_auth_rbac.py`
4. 后端串行全量 `python -m pytest -q`
5. `npx --no-install playwright test e2e/editor-state-revision-history.spec.ts --grep "P12F-H" --workers=1 --retries=0`
6. `npx --no-install playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0`
7. `npx --no-install playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0`
8. `npx --no-install playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0`
9. `npx --no-install playwright test e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0`
10. `npm run lint`、`npm run build`
11. Codex 额外串行前端全量 `npx --no-install playwright test --workers=1 --retries=0`

所有 pytest 共用 SQLite 测试库，禁止 xdist、并发分组或与 Playwright 并行。最后检查 `py_compile`、`git diff --check`、精确十文件、空暂存区、哈希、AST/SQL/弱断言/泄漏禁区。

## 10. 明确未做

不做固定/置顶/收藏、保护裁剪、批量命名、标签/备注、名称搜索、排序、导出/分享、软删除/回收站、检查点命名、跨项目历史、多人实时协作、SSE/WebSocket、审计扩展、数据库索引、缓存/离线队列或通用 metadata 框架。
