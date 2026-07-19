# P12F-J-B 修订固定状态七键响应与前端入口契约

模块：P12F-J-B editor-state 修订固定状态读取、严格前端解析与技术/商务共用操作入口

用途：在 P12F-J-A 已交付的服务端固定/取消固定与保护性裁剪之上，让历史列表、游标页、搜索和详情统一返回 `isPinned`，并让用户在共享修订面板中原位固定或取消固定。

对接：既有 history service、`EditorStateRevisionMetaOut`、`PATCH .../pin`、共用 `editorStateRevisionApi`/`EditorStateRevisionPanel` 与 history E2E。

二次开发：Grok 只能在十四文件白名单内先写真实 failure-first 再实现和自测；不得暂存、提交或推送；Codex 负责独立审查、独立验收、中文文档、提交和协作分支推送。

状态：2026-07-19 已完成实现、独立审查与串行验收；冻结=`f019a4b`，实现=`5ef7abd`，Codex 验收回执=`msg_8399a348aa1543e2b4b61cbdd25b4ac9`。

## 1. 选择理由与严格边界

1. P12F-J-A 已提供权威 `is_pinned` 列、单条 PATCH、5 条/10 MiB 固定上限和保护性裁剪，但 list/page/search/detail 仍是六键，浏览器无法得知或操作固定状态；J-B 只闭合这条现有能力。
2. 后端只扩展读取投影和输出 Schema，不改表、迁移、固定服务、裁剪、锁、配额、错误码、排序、游标、搜索候选窗或显式 DELETE 语义。
3. 前端只增加严格七键 parser、一键 pin API 和共用面板入口；固定成功原位改一项，不重新请求 page/search/detail，不重排，不改变来源、时间、关键词、游标、双修订选择或当前 editor-state。
4. 不做固定优先排序、批量固定、固定数量展示、乐观更新、自动重试、撤销 toast、检查点命名、收藏/标签、导出/分享、跨项目历史或多人协作。

## 2. 后端七键读取合同

1. list/page/search 的每个元数据项必须精确七键：`revisionId/stateVersion/snapshotBytes/sourceKind/createdAt/displayName/isPinned`；detail 必须精确八键，即前述七键加 `snapshot`。JSON 使用原生布尔，禁止 `0/1` 数字、字符串、null、缺键或额外键。
2. history service 的 list/page/detail/search SQL 必须显式投影原始固定值：`type_coerce(EditorStateRevisionRow.is_pinned, Integer).label("is_pinned")`。禁止直接 ORM Boolean 结果处理器，因为 SQLite 原始非法 `2` 会被转换为 `True`，从而绕过损坏检测。
3. 共用元数据校验只接受 `type(is_pinned) is int` 且值恰为 `0` 或 `1`，返回原生 bool；任一候选非法，list/detail/search 整次固定 corrupt；page 必须连同第 11 条 lookahead 完整校验后再截断和生成游标。
4. list/page 继续不投影 `snapshot_json`；detail/search 仍只在既有有界位置投影快照。list 10 条、page 10+1、search 最新 20 候选、`created_at DESC,id DESC`、来源/时间条件、V1/V2/V3 游标和搜索联合匹配完全不变。
5. API `_meta_out`、list/page/search/detail 必须统一通过同一七键输出模型；`isPinned` 只是读取结果，不接受在这些 GET/POST search 请求中投稿。既有 `PATCH .../pin` 的一键请求/响应、required Cookie+CSRF、bid_writer、no-store 和固定脱敏错误保持 P12F-J-A 原样。
6. 任一读取错误不得泄漏修订 ID、版本、项目、固定原始值、名称、关键词、快照、SQL、路径或异常原文；损坏读取不得写表、flush 或 commit。

## 3. 前端严格 API 合同

1. `EditorStateRevisionMeta` 增加必填 `isPinned:boolean`；`META_KEYS` 精确七键，`DETAIL_KEYS` 精确八键。list/page/search 任一项缺失、额外或非原生布尔时整次抛固定内部错误；详情必须逐值核对七项元数据，包括 `isPinned`。
2. 新增 `setEditorStateRevisionPin(projectId, revisionId, isPinned)`：revisionId 先走既有合法性校验；仅发一次 `PATCH /projects/{projectId}/editor-state-revisions/{revisionId}/pin`；URL 无 query；JSON 精确一键 `{isPinned:boolean}`；不加自定义 header、不重试、不读取其它接口。
3. pin 响应必须是精确一键 `{isPinned:boolean}`，且响应值必须等于请求目标；缺键、额外键、null、数字、字符串或相反布尔均固定失败。CSRF 由共享 `apiFetch` 处理，禁止在面板保存或显示 Token/Cookie。
4. parser/API 错误不得拼接响应原文、ID、项目、固定值、名称、关键词、路径或异常消息；不得 console、localStorage、sessionStorage、Cookie、URL、剪贴板或下载持久化。

## 4. 共用面板交互与并发合同

1. 每条未固定修订显示按钮“固定”；已固定修订显示文本标记“已固定”和按钮“取消固定”。按钮单击立即执行，无二次确认；`data-testid` 可沿现有 index 体系，但不得把 revisionId 写入 DOM 属性。
2. 固定请求采用全局单飞互斥。同步 ref 必须在调用 Promise 前关门，双击/连续点击/另一行点击只能产生一个在途 PATCH。在途文案固定为“保存固定状态中…”。
3. 请求在途时，折叠/展开切换、刷新、来源/时间/搜索控件、加载更多、摘要、当前对比、正文差异、双修订选择/执行、恢复、删除、命名以及所有固定按钮都必须真实 `disabled`；测试不得用 `force:true` 绕过用户不可执行状态。
4. 成功固定显示“修订已固定”，成功取消显示“已取消固定”；仅以 `setItems(prev => prev.map(...))` 原位替换目标的 `isPinned`。禁止调用 `loadList`、page/search/detail、重排、插入/删除条目、清游标或清筛选草稿/已应用条件。
5. HTTP 409/404/500、网络错误、严格响应失败或相反布尔均显示“保存修订固定状态失败，当前状态已保留”，目标及全部其它条目保持原值，零重载、零自动重试。
6. 新增 pin generation、同步在途 revision ref 和项目 ref 围栏；success/catch/finally 均同时核对 mounted、session、generation、projectAtStart 与 revisionId。项目切换、折叠和卸载必须作废旧代次并清当前 busy；A 的迟到 success/catch/finally 不得污染 B 的列表/文案或解锁 B 的新请求。
7. 开始固定前应作废并清除摘要、当前对比、正文差异、双修订差异、恢复/删除/命名意图和在途 load-more，保持与既有行操作互斥模型一致。固定本身不得调用 editor-state GET/PUT、restore、checkpoint、DELETE、display-name 或外网。
8. 固定标记只显示固定中文；revisionId/stateVersion/snapshot/cursor/内部来源原值/后端错误及路径不得进入 DOM、URL、存储、Cookie 或 console。

## 5. 十四文件白名单与冻结哈希

| 文件 | 冻结 SHA-256 | 允许变化 |
|---|---|---|
| `backend/app/api/schemas.py` | `90DDB1ECC0CD3009D04C507F97E107D706034EA370722AAB5D4A18A0BA40314D` | 元数据输出增加 `isPinned` |
| `backend/app/api/editor_state_revisions.py` | `A30DFB3633088D359588476968EF0F784A847C87AB9D6FFC67434F57A9645E7A` | `_meta_out` 与 detail 七键映射 |
| `backend/app/services/editor_state_revision_history_service.py` | `2A45E20DBD22E3894456B8930EA420BA2884D31546A783BCB595E439D157DFFD` | 四类读取原始固定投影、严格校验与结果字典 |
| `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts` | `880D8ECC27AEAEE7610C19E7EC3AD919BBC3559C8AB90D36F2ED961D9F4CCFDC` | 七键/八键 parser 与一键 pin API |
| `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx` | `636CAD938B088C4F24C5C713F0DC2988504C92C2CCB6283F7A4829A66F5BAAD8` | 固定标记、按钮、互斥单飞、原位更新和迟到围栏 |
| `frontend/e2e/editor-state-revision-history.spec.ts` | `280E84D7049AECA9EA68B85327EC75CE7EAA13E76066BF1D9E366902E8D7BB3A` | 探针七键、pin route/log/gate、技术/商务与静态证据 |
| `backend/tests/test_p12c_revision_history_read.py` | `16BA20C17A70637DF07D6B2CCAB8BC4D6CB46DA7ECB7003CBBEC17F7C29446DF` | 六键机械同步及真实坏固定值读取证据 |
| `backend/tests/test_p12f_revision_cursor_page.py` | `10D9A9B23EB8B1DB64CADF43FA944F5FF6103D000A758DB6E054D16A18ADE6E3` | page 七键/坏 lookahead 固定值证据 |
| `backend/tests/test_p12f_revision_delete.py` | `DD161B06291E928DD0749819514423686C50AD24B0AC1B9177EFAFA08936B4EB` | 删除前后读取七键机械同步，禁止改删除语义 |
| `backend/tests/test_p12f_revision_pin.py` | `9905C54174FDE8E89A598A567C4F33BF01622D105E279B6CF5939F2822951544` | pin 后 list/page/search/detail 七键联调与坏值零写 |
| `backend/tests/test_p12f_revision_time_range_filter.py` | `D095AB4495BD6A805386812DB959E0BE006284D8713644B7AEB645C6CB3A1ADD` | 时间筛选响应七键机械同步 |
| `backend/tests/test_p12f_revision_content_search.py` | `F6D0FC753CD40090CB6182CFF6E356F474F30DACF2A6377A4E68C9DE7C09C839` | search 七键与坏固定候选整次失败 |
| `backend/tests/test_p12f_revision_source_filter.py` | `8206FCAB492FCE4BF57E93CCBC5ED4A10FE63FD5152C58CB5159A0CD2675DA7D` | 来源筛选响应七键机械同步 |
| `backend/tests/test_p12f_revision_name.py` | `6C502C289E10284BE4E7882035F5C85AD017C2444D7794651A447A424491CBF6` | 命名后七键保持与固定值不变证据 |

禁止修改 ORM/迁移/pin service/裁剪/共享 `apiFetch`、CSS、技术/商务页面或 hook、依赖/锁文件、Playwright 配置、其它测试、文档、Git 历史。后端八个测试文件只能同步精确响应键和加入本合同直接要求的真实固定读取证据，禁止借机重写旧测试。

## 6. Failure-first、测试证据与反假绿门

1. Grok 第一阶段只改八个后端测试和 history E2E，生产六文件 SHA-256 必须仍等于冻结值。必须分别得到后端七键/坏值和前端固定入口缺失的真实业务失败；import/收集错误、宽状态、skip、空测试或未运行不算红测。
2. 后端必须用原始 SQL 写入 `is_pinned=2`，证明 list/page lookahead/search/detail 固定脱敏失败且零写；静态门精确要求四处 `type_coerce(..., Integer).label("is_pinned")`，禁止 `is_(True)` 或直接 Boolean 投影冒充严格校验。
3. E2E 探针默认所有元数据/详情均显式 `isPinned:false`；route 必须精确记录 method/path/query/bodyKeys/body/CSRF、arrived/complete。技术路径覆盖固定/取消、严格 parser、失败保值、单飞、全互斥和 A→B 双 gate；商务路径证明复用同一入口及零旁路。
4. E2E 必须精确比较请求增量与完整序列，不能用 `>0`、宽 OR、`Promise.race` 未完成、`Math.min`、首项代替目标项、条件跳过或 `force:true`。迟到证据必须分别控制 arrived 与 complete，且在旧请求完成前启动并锁住 B 新请求。

## 7. 串行验收门

所有命令逐条串行；pytest 禁止 xdist/并发分组，Playwright 必须显式 `--workers=1 --retries=0`：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12f_revision_pin.py tests\test_p12c_revision_history_read.py tests\test_p12f_revision_cursor_page.py tests\test_p12f_revision_source_filter.py tests\test_p12f_revision_time_range_filter.py tests\test_p12f_revision_content_search.py tests\test_p12f_revision_delete.py tests\test_p12f_revision_name.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q --tb=line
.\.venv\Scripts\python.exe -m py_compile app\api\schemas.py app\api\editor_state_revisions.py app\services\editor_state_revision_history_service.py

cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-revision-history.spec.ts --grep "P12F-J-B" --workers=1 --retries=0
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0
npm run test:e2e:technical-editor-state-truth -- --workers=1 --retries=0
npm run test:e2e:business-editor-state-truth -- --workers=1 --retries=0
npm run lint
npm run build
npm run test:e2e -- --workers=1 --retries=0
```

最后执行 `git diff --check`、精确十四文件、空暂存区、最终 SHA-256、源码/AST/SQL/泄漏/弱断言扫描。任一失败都先停在当前包内定位，不以重试掩盖确定性缺陷。

## 8. 明确未做

不做固定排序或分组、批量/全选、固定数/容量进度、乐观 UI、轮询/自动重试、检查点命名、收藏/标签、名称排序、搜索高亮/片段/评分/游标、跨项目时间线、导出/分享、审计扩展、多人协作、SSE/WebSocket、PostgreSQL/Alembic、表/索引/依赖变更。

## 9. 最终交付证据

1. 实现严格保持十四文件白名单；最终 `git diff --check`、空暂存区、Python 编译、前端 lint 与构建均通过。history service 恰有四处原始整数 `type_coerce(..., Integer).label("is_pinned")` 投影，未出现直接 Boolean 投影、写事务或范围外文件。
2. Codex 独立串行验收：后端八文件专项 **297 passed**，后端全量 **1170 passed / 1 warning**；P12F-J-B 定向 E2E **6 passed**，history 全量 **61 passed**，checkpoint restore **51 passed**，技术/商务 truth **28/18 passed**。整仓前端全量沿用上一包已验收 **318 passed** 基线，按用户停止重复测试的要求未再重复扫描不受影响套件。
3. Grok 首轮把后端与 Playwright 并发运行，违反串行门，因此该轮结果全部作废；后续实现保留在工作区，但 Grok 额度/进程中断后未形成 `review_request`。Codex 仅在十四文件内修正刷新互斥、Hooks 依赖及四处 E2E 确定性问题，再独立完成全部有效验收；未把无回执冒充已回执。
4. 最终 SHA-256：路由=`AA6B2E82AD47126C4CEBFFF5351B3C394B81DCBF3D49D24B20D782CE9981F147`，Schema=`65A5E879E0201E9FAF22F16A5B2914219BDE3FF386C8106FB4BADA338CBD5BE5`，history service=`5C126E7B18D081231AABF9C4C7A04672DE5472F2632C015667A70F08C101B438`，前端 API=`AB194540B8E0EE564218C9E3820BDBEDEF43E97D477650F806C2F09EE686B279`，面板=`283386B7EAE16DF9643C95D0C8CD255FA80FCD47EED189746DE0C62CD95A104F`，history E2E=`6FCB317644AEC24C38532CAD4338BCD4B7DA4AD0A2AB9CDE332D70744FED3A50`。
