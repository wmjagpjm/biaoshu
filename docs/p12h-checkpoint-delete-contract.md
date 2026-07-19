# P12H 单条检查点删除契约

模块：P12H editor-state 手动/安全检查点单条物理删除与双工作区共用删除入口

用途：让标书制作者删除不再需要的单条检查点，保持当前编辑态、其它检查点、自动修订和恢复链不变；删除必须显式确认、精确一次且不可恢复。

对接：`backend/app/api/editor_state_checkpoints.py`、独立删除服务、技术标与商务标共用 `EditorStateCheckpointPanel`、检查点 E2E、Grok-Codex 协作消息箱。

二次开发：本包只允许本文第 8 节七文件；Grok 只实现与自测，不暂存、不提交、不推送；Codex 独立规划、受限审查、串行验收、中文文档闭环并推送协作分支。

## 1. 选择理由与只读审计结论

1. P12G 已让最近 20 条手动/安全检查点可命名，但失效、误建或临时检查点仍只能等待后续创建触发裁剪；单条删除是当前最小、最直接的版本治理缺口。
2. 检查点固定会改变 `_trim_checkpoints` 的连续最近 20 条语义并需要新列、迁移、数量/容量保护；跨项目历史和多人协作还涉及新的查询、权限、身份与实时状态边界，均明显大于本包。
3. `EditorStateCheckpointRow.id` 未被其它业务表外键引用。恢复结果中的 `restoredCheckpointId/safetyCheckpointId` 只是当次响应，不是持久引用；删除一行无需回填、迁移、软删除或级联业务清理。
4. 现有详情路径固定 GET，集合固定 GET/POST，`/display-name` 固定 PATCH，`/restore` 固定 POST；同一详情路径增加 DELETE 不改变静态路由优先级或既有方法。
5. 前端已有技术标/商务标共用面板、内联恢复确认、命名单飞和项目会话围栏；删除可在同一面板内独立实现，不修改两个页面或工作区 hook。

## 2. 产品与数据边界

1. 允许删除当前工作空间、当前项目列表中的任意一条手动或恢复前安全检查点；现有模型没有来源字段，前后端不得伪造“手动/安全”分类或仅凭位置猜测来源。
2. 删除是物理删除且不可撤销；必须先进入内联固定文案确认态，确认前和取消均零 DELETE。
3. 删除不修改当前 editor-state、`stateVersion`、项目 `updated_at`、自动修订、其它检查点、名称、快照内容、任务、财务、人力、投标人或内容融合数据。
4. 删除不补建安全检查点、不创建修订、不重排或重写剩余行；列表自然保持 `created_at DESC,id DESC`。因为服务端每项目已物理裁到最多 20 条，成功后前端原位移除目标即可，不需要额外列表重载补第 21 条。
5. 本包不区分“当前正在其它客户端使用”的检查点，也不新增跨客户端锁、presence 或审计记录；单行 DELETE 与既有数据库事务负责原子性，同一面板实例负责操作互斥和迟到隔离。

## 3. 后端 DELETE 合同

唯一新路由：

`DELETE /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}`

1. query 必须为空，请求 body 必须精确零字节；`{}`、`null`、空白、文本、坏 JSON 或任意其它非空体均固定 422。禁止用 Pydantic 默认错误回显路径、输入或类型。
2. 成功固定 HTTP 204、严格空正文、`Cache-Control: no-store`；不得返回 JSON、ID、计数、版本、名称或正文。
3. required 模式继续由统一中间件/`get_workspace_id` 限定活动 workspace 与 `bid_writer`；DELETE 必须通过既有 CSRF 检查。disabled 模式保持本机兼容，不新增鉴权分支。
4. 固定错误：
   - query/body 外壳非法：422 `editor_state_checkpoint_delete_request_invalid`；
   - 项目不存在或跨 workspace：404 `project_not_found`；
   - 检查点不存在、跨项目或跨 workspace：404 `editor_state_checkpoint_not_found`；
   - execute/flush/commit、异常 rowcount 或内部故障：500 `editor_state_checkpoint_delete_error`。
5. 所有错误均为固定 `code/message` 且 no-store，不得回显 projectId/checkpointId、路径、query/body、名称、快照、SQL、异常原文、Cookie 或 CSRF。
6. 集合 DELETE 继续 405；详情 PUT/PATCH 继续 405；`/display-name` 只开放 PATCH，`/restore` 只开放 POST。新增 DELETE 不接受 body，也不得把旧详情 GET 改成读后删除。

## 4. 独立删除服务与事务边界

新增 `backend/app/services/editor_state_checkpoint_delete_service.py`：

1. 先以 `SELECT Project.id` 且同时限定 `Project.id/workspace_id` 确认项目；禁止加载 Project 整实体、当前 editor-state 或项目写锁。
2. 再执行单条 SQL DELETE，同时限定 `EditorStateCheckpointRow.workspace_id/project_id/id`；禁止先按裸 checkpoint ID 加载 ORM，禁止 SELECT `snapshot_json` 或其它检查点列。
3. `rowcount == 0` 才映射检查点 404；`rowcount == 1` 才成功；`None/-1/2` 或任意非 0/1 值固定 500。禁止 `int(rowcount or 0)`、truthy/falsy 或宽泛 `>=1`。
4. 成功路径固定 execute → flush → 唯一 commit；commit 后禁止 refresh、SELECT、列表/详情读取、审计或任何补写。
5. 项目 404、检查点 404、异常 rowcount、execute/flush/commit 和任意运行时异常均 rollback；内部异常统一固定 500，不保留异常原文。
6. 测试必须证明 SQL 只投影 Project.id，DELETE 三谓词齐全，且删除目标前后其它行完整字段、当前 13 键、修订行、项目与旁路 workspace/project 均不变。

## 5. 前端 API 与共用面板合同

1. `editorStateCheckpointApi.ts` 新增 `deleteEditorStateCheckpoint(projectId, checkpointId): Promise<void>`：
   - 复用既有 checkpoint ID 严格校验；非法 ID 在请求前固定失败；
   - URL 精确同源详情路径、无 query；init 精确 `{method:"DELETE"}`，不带 body、额外 header 或响应 parser；
   - 不重试、不轮询、不缓存、不读取响应 JSON。
2. 技术标与商务标继续共用 `EditorStateCheckpointPanel`。每行正常态增加“删除”；点击只进入内联确认，固定文案为：`删除后无法恢复。当前编辑内容、修订历史和其它检查点不会改变，确定删除这条检查点吗？`
3. 删除不依赖 editor-state expected version，因此即使 `props.disabled=true` 仍可进入确认并执行；但列表加载、创建、恢复、命名或其它删除意图在途/确认时必须互斥。
4. 确认前、取消和只进入确认均零 DELETE。确认必须使用独立同步 flight token 在第一个 await 前原子关门；同一 JavaScript 任务内连续两次 DOM click 也只能产生一次 DELETE。
5. 成功只从内存 `items` 原位移除目标，清确认并显示固定成功文案；禁止 list/detail/restore/create、editor-state GET/PUT、revision、页面导航或其它网络请求。删除最后一项后显示既有空态。
6. 失败显示固定中文，保留原列表、目标确认态和所有名称/元数据，释放本轮 flight 允许显式重试或取消；禁止自动重载、重试或乐观移除。
7. 删除确认/在途时，toggle、刷新、创建、恢复、命名、其它行删除及确认/输入控件必须真实 disabled；进入删除确认前清理本行可能残留的恢复/命名意图，不改其它业务状态。
8. 迟到隔离绑定 mounted、projectId、session、delete generation、checkpointId 和 flight token。A 项目旧 success/catch/finally 在切到 B 并开始新删除后，不得移除 B 项、覆盖 B 文案、清 B 确认、解除 B busy 或释放 B flight。
9. checkpointId/stateVersion/displayName/后端错误/CSRF 不得进入 DOM、URL query、浏览器存储、Cookie、console、剪贴板、下载或外网；checkpointId 只作内存 key 与同源 DELETE 路径段。

## 6. 测试与反假绿门

### 6.1 failure-first

1. Grok 先只修改 `backend/tests/test_editor_state_checkpoints.py`、新增后端专项测试并修改既有 checkpoint E2E；三个既有生产文件哈希必须保持第 8 节冻结值，新删除服务必须仍不存在。
2. 既有后端 405 守卫只机械移除“详情 DELETE 必须 405”，集合 DELETE 与详情 PUT/PATCH 仍精确 405；新专项必须真实请求详情 DELETE 并首先得到 405。
3. 前端 E2E 先扩展 DELETE 探针与 P12H 断言，生产 API/面板不改；首个真实业务失败必须是删除入口缺失或零 DELETE，不得用手工 `throw`、恒真断言、`.skip/.only` 或只搜源码伪造。
4. 红测记录真实 failed/passed/did-not-run、首个业务失败、生产哈希和当时文件清单，不硬编码预期失败数量。

### 6.2 后端验收

必须覆盖：204 空体/no-store；query/所有非空 body 固定 422；项目/检查点/跨空间/跨项目固定 404；required 角色与 CSRF；Project.id 单列投影；三谓词 DELETE；rowcount 0/1/None/-1/2；execute/flush/commit 回滚；commit 后零查询；命名/未命名目标；删除后 list 200 且不含目标、detail 404；其它行完整字段、当前态、修订、项目、任务与旁路作用域零副作用；集合和其它子路径方法不放宽。

### 6.3 前端验收

必须覆盖：技术/商务共用入口；确认前/取消零请求；精确无 query/body DELETE；成功原位移除且零重载/旁路；失败保值可重试；`disabled=true` 仍可删除；同任务双击单飞；确认/在途全操作互斥；A→B 旧 success 与 failure/catch/finally 双 hold 隔离；ID/名称/错误/CSRF 的 DOM、URL、存储、Cookie、console、外网零泄漏；既有 59 条 checkpoint、61 条 history 和技术/商务真相基线不被破坏。

### 6.4 有效命令必须逐条串行

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12h_checkpoint_delete.py tests\test_editor_state_checkpoints.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12g_checkpoint_display_name.py tests\test_editor_state_checkpoint_restore.py tests\test_p12c_checkpoint_restore_revisions.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile app\api\editor_state_checkpoints.py app\services\editor_state_checkpoint_delete_service.py tests\test_p12h_checkpoint_delete.py tests\test_editor_state_checkpoints.py

cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --grep "P12H" --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0
npx playwright test e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0
npm run lint
npm run build
```

pytest 与 Playwright 禁止 xdist、并发分组或 `--workers>1`。后端全量最多一次；整仓前端全量是否执行由 Codex 依据全部受影响套件结果与“避免无止境重复测试”要求决定，并在交付文档如实记录。

## 7. Grok-Codex 协作门

1. Grok 只读取消息箱最新 P12H task，严格七文件 failure-first、实现、自测和自审；发现需要第八文件、改变产品语义或既有测试矛盾时先发 `question`，不得自行扩围。
2. Grok 的 `review_request` 必须报告：任务 ID、真实红测、最终串行命令结果、精确七文件、最终 SHA-256、`git diff --check`、风险/未做项，以及“未暂存/未提交/未推送”。
3. Codex 负责逐文件审查 SQL/事务/脱敏、前端真单飞/迟到围栏和 E2E 反假绿；缺陷只下发最小白名单返修。
4. 只有 Codex 独立验收通过并发送 ack 后，才由 Codex 使用中文提交实现、推送、更新中文文档并再次提交推送。

## 8. 严格七文件白名单与冻结哈希

冻结基线 HEAD/跟踪远端：`7a5345fea33fc219edc841dd4962e9cfe27c5005`，工作区干净。

现有五文件：

| 文件 | 冻结 SHA-256 |
|---|---|
| `backend/app/api/editor_state_checkpoints.py` | `566CB28D34E702C981706062D5D24202E48F5E3D6D2B9212E7F379DB953B394A` |
| `backend/tests/test_editor_state_checkpoints.py` | `2CE45AFEFF4B2B3C4F75FB0DB3E7CB84C07080A4152EFA5658939FB17BF2B9C2` |
| `frontend/src/features/editor-state-checkpoints/editorStateCheckpointApi.ts` | `E1716BE70CC4962564747D1B486DB36BB0C2EB026E7C934846A79FE8B42C7C06` |
| `frontend/src/features/editor-state-checkpoints/EditorStateCheckpointPanel.tsx` | `C2C5B2849035AF83B85C53FB53A09EF501B3B2D58FFCF2E61552060942883E60` |
| `frontend/e2e/editor-state-checkpoint-restore.spec.ts` | `AE321AC7D5EFF800B9D239BBF44444A42A1DB981B44C90DC6A7A7184B3E0BC9B` |

允许新增：

- `backend/app/services/editor_state_checkpoint_delete_service.py`
- `backend/tests/test_p12h_checkpoint_delete.py`

除上述七文件外，任何模型、数据库、Schema、核心检查点/恢复服务、页面、hook、CSS、共享请求层、其它 E2E/测试、配置、依赖、脚本或文档改动都必须先向 Codex 发 `question` 并重新冻结。Grok 不修改本契约、计划、路线图、交接或联调清单。

## 9. 明确未做

不做批量删除、软删除、撤销/回收站、自动清理、删除审计、操作人/来源字段、固定/置顶、固定保护裁剪、名称搜索/排序、创建时命名、恢复时删除、下载/导出/分享、跨项目检查点、完整时间线、跨客户端互斥、多人协作、presence、SSE/WebSocket、数据库迁移、索引、依赖或共享请求层变更。
