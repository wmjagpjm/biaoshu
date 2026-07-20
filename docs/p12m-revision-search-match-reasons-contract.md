# P12M 修订搜索命中来源标签契约

模块：P12M editor-state 修订历史搜索命中原因

用途：在 P12F-F-A/B 已交付当前项目名称/可见内容联合搜索之后，让用户知道每条结果是因名称、可见内容或两者命中；只返回固定来源标签，不返回正文片段、关键词或内部路径。

对接：`list_editor_state_revision_search`、`POST /api/projects/{projectId}/editor-state-revisions/search`、修订历史 API/parser、技术/商务共用修订面板及既有 history E2E。

二次开发：Grok 只能在七文件白名单内先新增真实 failure-first，再实现后端/前端并串行自测；不得暂存、提交或推送。Codex 负责独立规划、受限审查、分级验收、中文文档、提交和协作分支推送。

状态：2026-07-20 已在干净 HEAD `37a4461` 完成只读审计并冻结待实现。严格七文件冻结哈希见第 6 节；实现启动 HEAD 必须为包含冻结提交的最新干净上游，不得回退或扩围。

## 1. 选择理由与边界

1. 当前搜索只返回七键元数据，用户无法区分名称命中还是正文命中；返回两个固定原因标签即可解释结果，不泄漏正文，也不需要 FTS、索引、缓存或跨项目聚合。
2. 只改搜索成功结果项：增加第八键 `matchReasons`，值为非空、无重复、固定顺序的 `displayName` 与/或 `visibleContent`。list/page/detail/create/restore/name/delete/pin 等既有响应不变。
3. 候选仍是当前 workspace/project 在既有来源/时间条件下按 `created_at DESC,id DESC` 的最新 20 条；先完整校验元数据、快照和预算，再同时计算名称与可见内容命中，禁止名称命中短路快照校验。
4. 前端只在 active search 结果行显示固定中文标签：`命中：名称`、`命中：可见内容` 或 `命中：名称、可见内容`；不做高亮、片段、评分、自动搜索、缓存、分页或额外请求。
5. 不改数据库、模型、迁移、索引、搜索候选窗口、排序、关键词规范、来源/时间过滤、错误码、no-store、Cookie/CSRF/RBAC、跨项目权限或多人协作。

## 2. 后端合同

1. service 内部原因枚举只允许 `displayName`、`visibleContent`，固定顺序按该顺序；响应不得出现 snake_case。
2. `name_match` 与 `snapshot_match` 必须在完整候选校验后分别求值；匹配结果为：
   - 仅名称：`["displayName"]`；
   - 仅可见内容：`["visibleContent"]`；
   - 两者：`["displayName","visibleContent"]`。
   未命中项仍不返回。
3. 新搜索项精确八键：既有 `revisionId/stateVersion/snapshotBytes/sourceKind/createdAt/displayName/isPinned` 加 `matchReasons`；`matchReasons` 每项只能是两个固定枚举之一，数组长度 1..2。
4. list/page 继续精确七键；detail 继续精确八键（含 snapshot）；搜索顶层继续精确 `{items}`，只改变 items 的搜索专属键集。
5. 不回显 query、原始字符串、snapshot、命中字段值、revisionId 到新文案或错误；任何坏元数据、坏快照、预算超限仍整次固定 `editor_state_revision_corrupt`，五域零写。

## 3. 前端合同

1. 搜索 parser 必须独立严格校验八键搜索项：键缺失/额外、`matchReasons` 非数组、空数组、重复、未知枚举、错误顺序、非原生字符串均固定失败；list/page parser 仍只接受七键。
2. `EditorStateRevisionSearchResult.items` 使用搜索专属类型；普通 list/page item 不伪造 `matchReasons`。
3. 面板仅在 active search 且该行已严格解析原因时渲染稳定 `data-testid="editor-state-revision-search-match-reasons-{index}"`；固定中文标签按服务端顺序显示，禁止渲染 query、正文、ID、内部键或原始原因。
4. 搜索仍精确一次既有 POST；刷新、清除、来源/时间筛选、项目切换、折叠、迟到 success/catch/finally 复用现有代次和单飞，不新增请求或自动重试。
5. 技术标/商务标必须共用同一 API/parser/面板；结果顺序与旧搜索一致。

## 4. Failure-first 与反假绿证据

1. 第一阶段只新增/修改既有后端搜索测试与既有 history E2E，三后端生产文件和两前端生产文件哈希保持冻结。必须先因响应缺 `matchReasons` 或 UI 标签缺失取得真实业务红测；不得把路由 404、收集错误、fixture、白页、skip/xfail 算作红测。
2. 后端真实证据至少覆盖名称、可见内容、双命中、未命中、21 条候选第 21 条不进入、来源/时间过滤、坏候选先校验、非法原因/额外键和零写；SQL 仍八列含 snapshot、无新增查询/写入。
3. 前端真实证据至少覆盖八键 parser 缺失/额外/未知/重复/乱序失败、技术/商务标签、双命中顺序、搜索清除隐藏、关键词/正文/ID/原因内部值不泄漏、零额外请求与 A→B 隔离。
4. 禁止“至少一次”、宽状态集合、`A || B`/`A or B`、只断言源码字符串、`force:true`、`waitForTimeout`、sleep、retry、Promise.race、并发 Playwright 或后端 xdist。

## 5. 分级串行验收门

所有动态命令逐条串行，Playwright 必须 `--workers=1 --retries=0`。Grok 运行后端搜索专项、后端受影响回归、P12M history 聚焦、既有 history 受影响 E2E、lint/build/py_compile；Codex 独立复跑 P12M 后端专项与 history 聚焦、静态/哈希/差异门，不机械重复后端全量或整仓前端 318。只有跨域风险或聚焦失败才升级范围。

## 6. 严格七文件白名单与冻结哈希

| 文件 | 冻结 SHA-256 | 允许变化 |
|---|---|---|
| `backend/app/api/schemas.py` | `8ECBFC2B679329CB4A23E58F7FB1CE919C4BF1E22EFE55FEF1797AFB6508D9DF` | 新搜索专属八键响应模型及中文顶注释 |
| `backend/app/api/editor_state_revisions.py` | `AA6B2E82AD47126C4CEBFFF5351B3C394B81DCBF3D49D24B20D782CE9981F147` | 搜索路由使用专属响应模型/映射 |
| `backend/app/services/editor_state_revision_history_service.py` | `5C126E7B18D081231AABF9C4C7A04672DE5472F2632C015667A70F08C101B438` | 搜索原因计算与精确顺序/枚举 |
| `backend/tests/test_p12f_revision_content_search.py` | 既有文件 | P12M 后端真实行为/SQL/零写测试 |
| `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts` | `AB194540B8E0EE564218C9E3820BDBEDEF43E97D477650F806C2F09EE686B279` | 搜索专属八键类型/parser |
| `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx` | `283386B7EAE16DF9643C95D0C8CD255FA80FCD47EED189746DE0C62CD95A104F` | active search 原因中文标签 |
| `frontend/e2e/editor-state-revision-history.spec.ts` | `D5C47A8A81667458A4A86C7A98B189B8D9038FC7769B490E6A1F05F90E21AC84` | P12M failure-first、parser/UI/泄漏与迟到隔离证据 |

禁止修改其它后端/前端文件、数据库、依赖、配置、脚本或文档；七文件不足时只能发送 `question`。

## 7. Grok 回执合同

只发送一个完整 `review_request`：真实 failure-first、后端专项/受影响回归/前端聚焦/lint/build/py_compile 精确结果、七文件列表/最终哈希/空暂存区、名称/内容/双命中、候选 20、坏值零写、八键 parser、技术/商务、泄漏、未做项。额度/认证/进程中断只发 `status`，禁止补造数字或完成结论。

## 8. 明确未做

不做正文片段/高亮/评分、自动搜索、防抖、搜索游标、FTS/索引/缓存、跨项目搜索、来源多选、日期预设、批量比较、完整时间线、导出/分享、固定分组/容量、多人协作、presence、SSE/WebSocket、数据库/依赖变更。
