# P12J-B 检查点固定状态八键响应与前端入口契约

模块：P12J-B editor-state 检查点固定状态读取、严格前端解析与技术/商务共用操作入口

用途：在 P12J-A 已交付的服务端固定/取消固定、5 条/10 MiB 配额和保护裁剪之上，让检查点创建、列表、搜索和详情统一返回 `isPinned`，并让用户在共享检查点面板中原位固定或取消固定。

对接：`editor_state_checkpoint_service`、`EditorStateCheckpointMetaOut`、既有 `PATCH .../pin`、共用 `editorStateCheckpointApi`/`EditorStateCheckpointPanel` 与 checkpoint E2E。

二次开发：Grok 只能在十一文件白名单内先写真实 failure-first 再实现和自测；不得暂存、提交或推送；Codex 负责独立规划、受限审查、独立验收、中文文档、提交和协作分支推送。

状态：2026-07-19 已完成只读审计并冻结待实现；十一份白名单代码的哈希基线=`262683e`，契约冻结提交=`65fe259`。实现启动时以协作分支最新上游 HEAD 为准，不得把代码哈希基线误作当前 HEAD。

## 1. 选择理由与严格边界

1. P12J-A 已提供权威 `is_pinned` 列、单条 PATCH、固定配额与“固定行 + 本轮安全检查点 + 最新普通行”裁剪，但 create/list/search/detail 仍为七/八键，浏览器无法获知或操作固定状态；P12J-B 只闭合这条既有能力。
2. 后端只扩展读取投影、严格固定值校验和输出 Schema；不改表、迁移、pin service、项目锁、配额、裁剪、显式 DELETE、恢复 transition、名称或搜索匹配语义。
3. 前端只增加严格八键 parser、一键 pin API、共用面板固定入口与既有 checkpoint E2E；成功原位更新目标，不重新请求 list/search/detail，不改变搜索态、顺序、名称、时间或当前 editor-state。
4. 不做固定优先排序/分组、批量固定、固定数量/容量展示、乐观更新、自动重试、撤销、配额探测、分页/游标、跨项目检查点、完整时间线、多人协作、presence、SSE 或 WebSocket。

## 2. 后端八/九键读取合同

1. create/list/search 的每个元数据对象必须精确八键：`checkpointId/stateVersion/snapshotBytes/outlineNodeCount/chapterCount/createdAt/displayName/isPinned`；detail 必须精确九键，即前述八键加 `snapshot`。`isPinned` 必须是原生 JSON boolean，禁止数字、字符串、null、缺键或额外键。
2. 手动创建响应固定 `isPinned=false`；恢复前安全检查点初始 false，并在后续 list/search/detail 中真实读取为 false。创建请求仍精确 `{}`，客户端不得投稿 `isPinned`。
3. list SQL 必须显式八列投影，detail/search 必须在各自既有字段上增加 `type_coerce(EditorStateCheckpointRow.is_pinned, Integer).label("is_pinned")`；禁止直接 ORM Boolean 结果处理或整实体 detail，因为 SQLite 原始非法 `2` 会被转换为 `true`。
4. 共用读取校验只接受 `type(value) is int` 且值恰为 `0/1`，再转换原生 bool。list 必须完整物化并校验全部最多 20 条；detail 必须按 checkpoint/workspace/project 三谓词读取并校验；search 必须连同未命中候选先完整校验固定值、名称和规范快照，任一非法整次固定 corrupt 且零写。
5. list 仍不读取 `snapshot_json`；detail/search 只在既有有界位置读取快照。最近 20 条、`created_at DESC,id DESC`、名称/内容 NFKC+casefold 匹配、候选上限、双命中去重和不补扫第 21 条完全不变。
6. API `_meta_out` 与 detail 必须用 `data["is_pinned"]` 显式映射，禁止 `.get()` 以默认 false 掩盖服务漏键。既有 pin PATCH 的一键请求/响应、required Cookie+CSRF/bid_writer、disabled、no-store 和固定错误保持 P12J-A 原样。
7. 任一读取损坏不得泄漏 checkpoint ID、版本、项目、原始固定值、名称、关键词、快照、SQL、路径或异常原文；不得写表、flush、commit、修订或当前 editor-state。

## 3. 前端严格 API 合同

1. `EditorStateCheckpointMeta` 增加必填 `isPinned:boolean`；`META_KEYS` 精确八键。list/search/create 任一元数据缺失、额外或非原生布尔时整次抛固定内部错误；既有 create `stateVersion` 专用错误优先级不变。
2. 新增 `setEditorStateCheckpointPin(projectId, checkpointId, isPinned)`：checkpointId 先走既有格式校验；仅发一次 `PATCH /projects/{projectId}/editor-state-checkpoints/{checkpointId}/pin`；URL 无 query；JSON 精确一键 `{isPinned:boolean}`；不加自定义 header、不重试、不读取其它接口。
3. pin 成功响应必须是精确一键 `{isPinned:boolean}`，且值必须等于请求目标；缺键、额外键、null、数字、字符串或相反布尔均固定失败。CSRF 继续由共享 `apiFetch` 处理。
4. parser/API 错误只使用固定内部错误，不拼接响应原文、ID、项目、固定值、名称、关键词、路径或异常消息；不得 console、localStorage、sessionStorage、Cookie、URL、剪贴板或下载持久化。

## 4. 共用面板交互与并发合同

1. 每条未固定检查点显示按钮“固定”；已固定检查点显示文本标记“已固定”和按钮“取消固定”。按钮单击立即执行，无二次确认；`data-testid` 继续用列表 index，禁止把 checkpointId/stateVersion 写入 DOM 属性。
2. 固定请求全局单飞；同步 ref 必须在调用 Promise 前关门，双击、连续点击或另一行点击只能产生一个在途 PATCH。在途文案固定为“保存固定状态中…”。固定入口与显式删除相同，不依赖编辑态 `props.disabled`，但必须受列表/搜索/创建/恢复/命名/删除/其它固定及确认态互斥。
3. pin 在途时，折叠切换、刷新、搜索输入/应用/清除、创建、恢复及其确认/取消、命名及其输入/保存/清除/取消、删除及其确认/取消、所有固定按钮都必须真实 `disabled`；测试禁止 `force:true` 绕过不可执行状态。
4. 成功固定显示“检查点已固定”，成功取消显示“已取消固定”；只 `setItems(prev => prev.map(...))` 原位替换目标 `isPinned`。禁止调用 list/search/detail/create/restore/editor-state GET/PUT，禁止重排、插入/删除项目、清名称、清搜索草稿或已应用关键词。
5. HTTP 409/404/500、网络错误、严格响应失败或相反布尔均显示“保存检查点固定状态失败，当前状态已保留”；目标和全部其它项、搜索态、名称及确认态不得被错误修改，零重载、零自动重试。
6. 增加 pin generation、同步在途 checkpoint ref 与 project ref 围栏；success/catch/finally 同时核对 mounted、session、generation、projectAtStart 与 checkpointId。项目切换、折叠、卸载必须作废旧代次并清旧 busy；A 的迟到 success/catch/finally 不得污染 B 的列表/文案或解锁 B 的新 pin。
7. 开始 pin 前作废其它行操作意图并清除恢复/命名/删除确认；保留当前普通列表或 active search 的结果、关键词与顺序。pin 本身不得访问修订历史、知识库、任务、文件、导出、外网或浏览器持久化。
8. 固定标记只显示固定中文；checkpointId/stateVersion/snapshot/名称原值/关键词/后端错误/CSRF/路径不得进入新增 DOM、URL、存储、Cookie、console、剪贴板、下载或外网。

## 5. 十一文件白名单与冻结 SHA-256

| 文件 | 冻结 SHA-256 | 允许变化 |
|---|---|---|
| `backend/app/api/schemas.py` | `B88C85BA8E99FFC68BB5FEC736E8613F687D3B1257D844DFF1205D18D39D31E9` | 检查点元数据/详情输出增加 `isPinned` |
| `backend/app/api/editor_state_checkpoints.py` | `004D93AD0B6AECB1F35BD9A50F6C7FA4547AD83BA7320C3B29B817F195F0D3BC` | `_meta_out` 与 detail 显式映射固定状态 |
| `backend/app/services/editor_state_checkpoint_service.py` | `45126F09A2E8C28FF6938118A94BDBBCD07AF27DB774F919BDCDB9957390BA59` | create/list/detail/search 原始固定读取、严格校验与结果字典 |
| `frontend/src/features/editor-state-checkpoints/editorStateCheckpointApi.ts` | `860E7398E4A4B69F6E99FB7D7753B6384F5C92CA7DEA5D2310AD63A30B795AA3` | 八键 parser 与一键 pin API |
| `frontend/src/features/editor-state-checkpoints/EditorStateCheckpointPanel.tsx` | `0761A882D1F49E75ABD56BD60F9EF01F9DE199B92F32F1B0F088BE8B12ADD4EA` | 固定标记、按钮、互斥单飞、原位更新和迟到围栏 |
| `frontend/e2e/editor-state-checkpoint-restore.spec.ts` | `BE547CCE17E52BBD58470ABECCA4E665BCEAF442B079A35F4BD0E55796E98BA3` | 探针八键、pin route/log/gate、技术/商务与静态证据 |
| `backend/tests/test_editor_state_checkpoints.py` | `A8536393D2D664E8D4A4F465C513EC7E2CAF2E90990E17B79A21BDAC77210B85` | create/list/detail 八/九键与坏固定值读取证据 |
| `backend/tests/test_p12g_checkpoint_display_name.py` | `86A6B3FCEBA7B94D84BE656A18BC933C62B0189935C9CF03A1386377FB7139CC` | 命名/安全检查点八/九键及固定状态保持证据 |
| `backend/tests/test_p12h_checkpoint_delete.py` | `721932631233E3583197363770B96D6BB0AABCF5AC004F753A3A0B970431D87C` | 删除前后响应键机械同步与固定行删除兼容证据 |
| `backend/tests/test_p12i_checkpoint_search.py` | `286F9EBB85C00FC62C8A26E490A0393ACE61E1A9C3048C8B6979B56524C49CD3` | search 八键、坏固定未命中候选整次失败证据 |
| `backend/tests/test_p12j_checkpoint_pin.py` | `B00FAB634706AAD7BD345DBF6C2A58D6F2176E816DC3609D0DF4C3DDC678EB91` | pin 后 create/list/search/detail 联调、严格原始值与零写证据 |

禁止修改 ORM/迁移/pin service/裁剪算法、名称/删除服务、共享 `apiFetch`、CSS、技术/商务页面或 hook、依赖/锁文件、Playwright 配置、其它测试、Git 历史或冻结文档。五个后端测试文件只能机械同步精确响应键和加入本合同直接要求的真实固定读取证据，禁止借机重写旧测试。

## 6. Failure-first、测试证据与反假绿门

1. Grok 第一阶段只改六个测试文件，五个生产文件 SHA-256 必须保持冻结。必须分别得到后端八/九键或坏固定值读取、前端固定入口缺失的真实业务失败；import/收集错误、宽状态、skip/xfail、空测试或未运行不算红测。
2. 后端用原始 SQL 写入 `is_pinned=2`，分别证明 list、detail、search 未命中候选固定脱敏失败且五域零写；静态门精确要求 list/detail/search 三处 `type_coerce(..., Integer).label("is_pinned")`，禁止 `is_(True)`、直接 Boolean 投影或 `.get("is_pinned", False)`。
3. E2E 探针所有 create/list/search 元数据显式 `isPinned:false|true`；pin route 精确记录 method/path/query/bodyKeys/body/CSRF、arrived/complete。技术路径覆盖固定/取消、严格 parser、失败保值、active search 原位更新、全局单飞、全操作互斥和 A→B 双 gate；商务路径证明复用同一入口及零旁路。
4. E2E 必须精确比较请求增量和完整序列；禁止 `>0`、宽 OR、条件跳过、`Promise.race` 未完成、`Math.min`、首项代替目标项或 `force:true`。迟到证据分别控制 arrived/complete，旧 A 完成前 B 新 pin 必须已 arrived 且仍被 gate 锁住。

## 7. 串行验收门

所有命令逐条串行；pytest 禁止 xdist/并发分组，Playwright 固定单 worker、零重试：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoints.py tests\test_p12g_checkpoint_display_name.py tests\test_p12h_checkpoint_delete.py tests\test_p12i_checkpoint_search.py tests\test_p12j_checkpoint_pin.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q --tb=line
.\.venv\Scripts\python.exe -m py_compile app\api\schemas.py app\api\editor_state_checkpoints.py app\services\editor_state_checkpoint_service.py

cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --grep "P12J-B" --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
npm run test:e2e:technical-editor-state-truth -- --workers=1 --retries=0
npm run test:e2e:business-editor-state-truth -- --workers=1 --retries=0
npm run lint
npm run build
```

最后执行 `git diff --check`、精确十一文件、空暂存区、最终 SHA-256、源码/AST/SQL/泄漏/弱断言扫描。本包不重复运行不受影响的整仓前端 E2E；沿用已独立验收 **318 passed** 基线，不得冒充本包重跑结果。

## 8. Grok 回执合同

Grok 只发送一个内容完整的 `review_request`：真实 failure-first 和生产哈希、逐条串行命令/结果、十一文件/最终哈希/空暂存区、三处原始投影和坏值零写、八/九键、严格 parser、一键 PATCH、技术/商务、active search、全互斥、A→B 双 gate、泄漏门与明确未做项。额度或进程中断只发送 `status`，禁止补造测试数字或完成结论。

## 9. 明确未做

不做固定排序/分组、批量/全选、固定数/容量进度、乐观 UI、轮询/自动重试、配额预查询、撤销、标签/备注、分页/游标、搜索片段/评分/高亮/缓存、跨项目时间线、导出/分享、审计扩展、多人协作、presence、SSE/WebSocket、PostgreSQL/Alembic、表/索引/依赖变更。
