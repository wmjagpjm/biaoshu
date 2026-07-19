# P12I 检查点名称与可见内容显式搜索契约

模块：P12I 技术标/商务标共用检查点名称与可见内容搜索

用途：在现有每项目最近 20 条手动/安全检查点内，按可选展示名称或规范快照中的用户可见文本执行一次显式搜索，只返回既有七键元数据。

对接：`editor_state_checkpoint_service`、`editor_state_checkpoints` 路由、检查点 API 封装、共用检查点面板与既有 checkpoint E2E。

二次开发：Grok 只允许本文第 7 节六文件，先写真实 failure-first 再实现与自测；不得暂存、提交或推送。Codex 负责独立规划、受限审查、串行验收、中文文档闭环与协作分支推送。

交付状态：**已完成并推送**。只读审计基线=`92486cc2a7a64a18f3ce39efec1a59bf134f987b`，冻结提交=`86cc1a3bbfcc94f2468f59643728baa6dbcca1bf`，实现提交=`8c41bbc554ef0a1d7cbcb9a83aebb1fab4a2eeee`。Grok 最终 review_request=`msg_2a430c560a4d415d881a4fd58911ad9d`，Codex 验收回执=`msg_608e5dda4d59453b83ab068ce9879fbf`。

## 1. 选择理由与严格边界

1. P12G 已有 nullable 展示名称，P12H 已有单条删除，但用户仍只能逐条浏览最近 20 条检查点；名称或正文定位是当前最小的可见版本治理缺口。
2. 现有检查点每项目硬上限为 20、单快照上限 2 MiB，搜索可在一个有界候选集内完成，不需要表列、索引、迁移、分页、游标或后台任务。
3. 检查点固定会新增状态列并改变创建/恢复事务内的裁剪；跨项目版本和多人协作会扩大权限、身份与前端会话边界。三者都必须另包，禁止混入本包。
4. 本包只新增显式 `POST search` 与技术标/商务标共用入口；不改创建、列表、详情、恢复、命名和删除接口的请求/响应形状。

## 2. 唯一后端接口

新增：`POST /api/projects/{projectId}/editor-state-checkpoints/search`。

1. query 参数必须为空；请求体原始字节不超过 1024，JSON 必须为精确一键 `{ "query": string }`。空体、null、数组、额外/缺失键、snake_case 或解析失败固定返回 `422 editor_state_checkpoint_search_request_invalid`，`no-store`，不得反射输入、路径或异常原文。
2. `query` 必须为原生字符串，原值非空且首尾无空白，不含 C0/C1、DEL、换行、制表或 NUL；NFKC 后长度为 1..64 码点。非法固定返回 `400 editor_state_checkpoint_search_query_invalid`，不得 trim 后接受或回显关键词。
3. 合法请求按“项目存在性 → 关键词 → 候选读取”处理；项目/空间不存在沿用固定 `project_not_found`/404。成功 `200/no-store`，顶层精确 `{items}`，每项仍是既有七键 `checkpointId/stateVersion/snapshotBytes/outlineNodeCount/chapterCount/createdAt/displayName`。
4. required 模式继续复用当前会话、workspace、`bid_writer` 与 CSRF；disabled 保持本机测试兼容。不得新增 Token、Cookie、角色、审计、外网、重试、轮询或 GET/query 搜索旁路。

## 3. 候选读取、校验与匹配

1. SQL 必须同时限定 `workspace_id/project_id`，按 `created_at DESC,id DESC`，显式投影 `id/state_version/snapshot_bytes/outline_node_count/chapter_count/created_at/display_name/snapshot_json` 八列并 `LIMIT 20`；禁止 ORM 整实体、N+1、COUNT、OFFSET、LIKE、JSON SQL、索引或补扫第 21 条。
2. 必须先完整物化并严格校验全部候选，再匹配任何一条：复用现有检查点规范 JSON、精确 13 键、UTF-8 字节、stateVersion、outline/chapter 计数校验；存储名称必须是 null 或与 P12G 规范化结果逐字相等。任一坏行使整次请求固定 `editor_state_checkpoint_corrupt`/500，禁止名称命中短路坏快照。
3. 匹配双方统一 `NFKC + casefold` 连续字面子串，不使用 regex、分词、评分或模糊算法。同一条名称和内容双命中只返回一次，顺序保持候选倒序。
4. 可搜索内容白名单与修订搜索保持一致：outline 的 `title/description` 及对象 `children`；chapters 的 `title/preview/body`；`parsedMarkdown`；businessQualify 的 `requirement/response/evidence`；businessToc 的 `title/category/note`；businessQuote.rows 的 `name/unit/quantity/unitPrice/amount/remark` 与 quote `notes`；businessCommit 的 `title/body`。其它键、ID、版本、路径、用户、时间戳和未知嵌套不得进入搜索。
5. 使用显式栈，候选内对象预算最多 4096、字符串叶预算最多 8192；超限固定 corrupt。禁止递归爆栈、HTML/Markdown 渲染、把原始快照或匹配片段放入响应。
6. 搜索全程只读；editor-state、检查点、修订、项目、任务及其它业务域必须逐字不变，不得 flush/commit/rollback/refresh。

## 4. 前端 API 与交互合同

1. API 新增 `searchEditorStateCheckpoints(projectId, query)`：客户端先按同一规则判定关键词，合法时只发一次 POST，body 精确 `{query}`，URL 无关键词/query 参数；响应复用列表的精确顶层与七键 parser，最多 20 条。
2. 共用面板增加“名称或内容搜索”输入、显式“搜索”和“清除”按钮；输入/编辑零请求，按钮或 Enter 才搜索。同一已应用关键词再次提交零请求，不做自动搜索、防抖、建议、历史、缓存或本地快照过滤。
3. 展开默认仍只 GET 一次列表。应用搜索后显示固定搜索态与结果/空态；清除恰好一次既有列表 GET。刷新、创建和恢复需要重载可见列表时，若搜索态有效则重发同一 POST，否则沿用 GET；不得额外请求详情、editor-state、修订或外网。
4. 命名成功仍只原位更新当前项，删除成功仍只原位移除；两者不得借搜索新增列表重载。若名称变化影响当前命中，用户显式刷新后才重新计算，避免破坏 P12G/P12H 既有成功合同。
5. 搜索与 list/create/restore/name/delete/toggle/确认态全部互斥；`disabled` 必须真实传给搜索控件。搜索使用独立同步 flight token，在第一个 await 前占用，双击/Enter+点击只能产生一个 POST。
6. 项目切换、折叠、卸载必须作废搜索代次并清空草稿/已应用状态；A 项目迟到 success/catch/finally 不得污染、清空或解锁 B 项目搜索。失败保留已应用结果和输入，可重试，固定中文不得带原始错误。
7. 关键词、名称、ID、版本、快照、原始错误与 CSRF 不进入 URL、localStorage、sessionStorage、Cookie、console、pageerror、unhandled rejection、剪贴板、下载或外网；名称只由 React 文本渲染。

## 5. Failure-first 与反假绿

1. 第一阶段只允许新增 `backend/tests/test_p12i_checkpoint_search.py` 和修改既有 checkpoint E2E；四个生产文件必须保持第 7 节冻结哈希。后端首个有效失败应为合法 POST search 当前 405，前端首个有效失败应为页面加载后缺少“名称或内容搜索”入口。
2. 后端红测必须覆盖：名称唯一命中、内容唯一命中、并集/去重/顺序、Unicode 规范化、20/21 候选、坏行与预算不短路、八列投影、三重作用域、request/query 错误优先级、required CSRF/角色门和五域零写。
3. 前端红测必须覆盖技术标与商务标共用、精确 POST/body、输入零请求、同值零重发、清除 GET、active refresh/create/restore 重发、命名/删除原位、真实 disabled、同任务双触发单飞、A→B 迟到隔离、失败保值和数据泄漏门。
4. 禁止宽状态、`>=1`、truthy/defined、条件断言、固定 sleep、skip/fixme/xpass、`force:true`、可选首项、`Math.min`、空数组兜底、只等 route arrived 不等 complete、route fallback 假成功、客户端自造过滤或源码字符串替代运行时证据。

## 6. 串行验收门

Grok 与 Codex 均必须逐条串行；pytest 禁止 xdist/并发分组，Playwright 必须显式 `--workers=1 --retries=0`：

1. `cd backend && .\.venv\Scripts\python -m pytest -q tests/test_p12i_checkpoint_search.py --tb=line`
2. `cd backend && .\.venv\Scripts\python -m pytest -q tests/test_editor_state_checkpoints.py tests/test_editor_state_checkpoint_restore.py tests/test_p12c_checkpoint_restore_revisions.py tests/test_p12g_checkpoint_display_name.py tests/test_p12h_checkpoint_delete.py --tb=line`
3. `cd backend && .\.venv\Scripts\python -m pytest -q --tb=line`
4. `cd frontend && npx --no-install playwright test e2e/editor-state-checkpoint-restore.spec.ts --grep "P12I" --workers=1 --retries=0`
5. `cd frontend && npx --no-install playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0`
6. `cd frontend && npx --no-install playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0`
7. `cd frontend && npx --no-install playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0`
8. `cd frontend && npx --no-install playwright test e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0`
9. `cd frontend && npm run lint`，随后 `npm run build`
10. `py_compile`、`git diff --check`、精确六文件、空暂存区、最终 SHA-256、SQL/AST/弱断言/泄漏禁区和干净测试产物检查。

Codex 独立复跑聚焦、受影响回归与后端全量；整仓前端是否重跑按实际影响与已有 318 基线决定，未运行不得冒充结果。

## 7. 严格六文件白名单与冻结哈希

| 文件 | 冻结 SHA-256 | 允许变化 |
|---|---|---|
| `backend/app/services/editor_state_checkpoint_service.py` | `4908CF3B154350B433453E1DB9265E7897E0DEA16CBE3302C38FFAE8E1CA048C` | 有界搜索、严格校验与固定错误 |
| `backend/app/api/editor_state_checkpoints.py` | `2108927563218E604A4E2A484600F7008F3D375482B28E3D893BAEF275414A25` | POST search 外壳与 no-store 映射 |
| `backend/tests/test_p12i_checkpoint_search.py` | 新文件，不存在 | P12I 后端 failure-first 与安全证据 |
| `frontend/src/features/editor-state-checkpoints/editorStateCheckpointApi.ts` | `D64E0841582DD49D9DF438701CC7289C6FE2299CFF0939903F3718F5CE3D6129` | 搜索请求与严格复用 parser |
| `frontend/src/features/editor-state-checkpoints/EditorStateCheckpointPanel.tsx` | `DBF27FD0391FB1B106D6ECC4767BD73F0ED7410437CCBF5BEF935204E9FA5EC0` | 共用显式搜索、互斥与迟到隔离 |
| `frontend/e2e/editor-state-checkpoint-restore.spec.ts` | `AC015348F2B050424B7885D92A69B2A3F506EFDBD69CFD96071F27057B83AF60` | P12I failure-first、探针与静态门 |

禁止修改模型、数据库初始化/迁移、Schema、命名/删除服务、核心 editor-state/修订服务、技术/商务页面或 hook、CSS、共享请求层、其它测试、配置、依赖/锁文件、文档或 Git 历史。任何扩围必须先向 Codex 发 `question` 并重新冻结。

## 8. 明确未做

不做固定/置顶与保护裁剪、排序、分页/游标、片段/高亮/评分、自动搜索/防抖、搜索历史/缓存、标签/备注、批量操作、创建时命名、下载/导出/分享、跨项目检查点/搜索、完整时间线、跨客户端互斥、多人协作、presence、SSE/WebSocket、数据库索引/迁移或通用全文检索。

## 9. 完成记录

1. Grok 首轮实现真实通过后端 P12I/检查点回归/全量 **16/123/1233 passed**，前端 P12I/checkpoint/history/技术 truth/商务 truth **5/73/61/28/18 passed**；Codex 审查发现“搜索失败后同词无法重试”和“active search 刷新未占同步 flight token”两项生产缺陷，并发现 CSRF/角色、SQL 投影、库存坏名称、字符串叶预算和前端并发/泄漏证据不足。
2. 受限返修任务=`msg_69b8bb73702945b3a4f0b3ebd26c942a`。两项测试先在旧生产代码上真实得到 **2 failed**，修复后 **2 passed**；只修改面板、新后端专项和既有 checkpoint E2E，另外三个 P12I 生产文件哈希保持首轮值。
3. Codex 独立串行通过后端 P12I/五文件检查点回归/全量 **18/123/1235 passed**；前端 P12I/checkpoint/history/技术 truth/商务 truth **8/76/61/28/18 passed**。pytest 仅 1 条既有 Starlette/httpx 弃用警告；lint、build、py_compile、`git diff --check`、精确六文件、空暂存区与最终哈希均通过，build 仅既有大 chunk 提示。整仓前端沿用已验收 **318 passed** 基线，本包未冒充重跑。
4. 最终 SHA-256：service=`CA117EC759F791C34F9B0ADB4423D07F9D814689AE03F399E1967A1BEE3E60F2`，route=`374EDB65557942B3AB8D86068D8B2BC1ECCB96E083B37B2E7EB756EA861A701F`，backend test=`286F9EBB85C00FC62C8A26E490A0393ACE61E1A9C3048C8B6979B56524C49CD3`，frontend API=`860E7398E4A4B69F6E99FB7D7753B6384F5C92CA7DEA5D2310AD63A30B795AA3`，panel=`0761A882D1F49E75ABD56BD60F9EF01F9DE199B92F32F1B0F088BE8B12ADD4EA`，E2E=`BE547CCE17E52BBD58470ABECCA4E665BCEAF442B079A35F4BD0E55796E98BA3`。
