<!--
模块：P12C-C3 editor-state 修订历史前端契约
用途：冻结双工作区修订列表、按需摘要、受限恢复和迟到隔离边界。
对接：P12C-C1 列表/详情；P12C-C2 后端 restore；P12B-D2 版本化外部写队列。
二次开发：本包只做前端显式入口；不新增后端、删除、diff、搜索、跨项目历史或多人协作。
-->

# P12C-C3 editor-state 修订历史前端契约

> **状态**：已完成并经 Grok failure-first、Codex 多轮反假绿审查与独立验收。
> **前置**：C2 冻结=`54af600`、范围修订=`2276366`、实现=`0803250`、闭环=`f34e3fc`；后端/前端串行全量基线 **800/263 passed**。

## 1. 目标与关键决策

C3 只在技术标和商务标工作区新增一个共用“修订历史”折叠面板。用户展开后读取当前项目最近 10 条元数据；单击“查看摘要”才读取一条详情；单击“恢复”后必须再次确认，确认时把目标 revision ID 交给各工作区既有版本化外部写队列，由队列在真正执行时读取最新 `expectedStateVersion` 并调用 C2 restore。

不得复用“版本检查点”API、面板或 `checkpoint_restore` 文案冒充修订历史。两个面板可以相邻，但修订历史没有创建按钮、手动命名、删除、下载或上传快照。检查点 create/restore 与 revision restore 必须共用各 hook 现有操作令牌，禁止同时发出两个版本化写请求。

C1 详情返回完整 13 键快照，但 C3 不展示正文。API 层严格校验详情后立即只返回计数摘要，React 组件不得持有原始 `snapshot`；正文、标题、事实内容、矩阵文本、商务值、ID 和版本不得进入 DOM、URL、浏览器存储、日志或错误文案。

## 2. API 封装与严格响应

新增独立 `editorStateRevisionApi.ts`，只封装：

- `GET /projects/{projectId}/editor-state-revisions`：无 query；顶层精确 `{items}`，最多 10 条；每条精确 `revisionId/stateVersion/snapshotBytes/sourceKind/createdAt`；
- `GET /projects/{projectId}/editor-state-revisions/{revisionId}`：仅用户点击摘要后调用；精确六字段且五项元数据必须与当前列表项逐值一致；`snapshot` 必须是精确 13 键对象；
- `POST /projects/{projectId}/editor-state-revisions/{revisionId}/restore`：body 精确 `{expectedStateVersion}`；成功体精确 `safetyCheckpointId/stateVersion/restoredAt`。

`revisionId` 固定 `esr_` + 32 位小写 hex，版本固定 `esv_` + 32 位小写 hex，安全检查点 ID 固定 `escp_` + 32 位小写 hex；字节为非负安全整数；时间为非空字符串；来源精确属于九类：

`browser_put|task|revise|callback|local_parser|content_fuse_apply|content_fuse_consume|checkpoint_restore|revision_restore`。

列表保持服务端顺序，禁止本地重排、过滤、分页、搜索或自动轮询。来源只展示固定中文标签，不显示原始内部值。详情解析后只可返回：大纲节点数、章节数、事实数、响应矩阵行数、商务条目总数、是否含解析正文；所有计数必须由有限深度/有限节点的防御性函数产生，非法或过深结构固定失败，禁止递归耗尽页面。

任何额外/缺失键、超过 10 条、非法 ID/版本/来源/字节/时间、详情元数据与列表不一致、快照键集或摘要结构非法、restore 成功体非法，均只抛固定内部错误；禁止把响应原文、路径参数或后端 detail 拼进 UI。

## 3. 面板交互与内存边界

共用 `EditorStateRevisionPanel` 固定满足：

1. 默认折叠，折叠态零 revision 请求；展开后恰好一次列表 GET；用户显式刷新才再 GET。
2. 列表只展示格式化时间、固定中文来源标签和格式化大小；ID/version 不渲染，也不进入 `data-*`、React key 以外可见属性、URL 或存储；测试定位只用数组下标。
3. “查看摘要”按需 GET 详情；同一时刻只展开一项摘要。组件只保存摘要计数，不保存完整 snapshot。再次点击、折叠、刷新、恢复或项目切换均清空摘要与确认态。
4. “恢复”第一次点击只显示内联确认；文案固定说明：服务器当前内容会先保存为安全检查点，恢复替换技术标和商务标全部编辑态，尚未保存的本地修改不会写入。确认前 POST 数量必须为 0。
5. 面板只把 revision ID 传给 hook；不得把列表项 `stateVersion` 当 expected。全状态阻断、初始加载失败、版本未知或 API 未就绪时恢复按钮禁用，但列表/摘要只读仍可刷新。
6. 列表/详情失败、恢复失败、恢复被阻断、POST 成功但 editor-state 重载失败分别使用固定中文；不得显示后端错误、ID、版本、来源原值或正文。
7. 项目切换、折叠、卸载、刷新、查看另一项、恢复和重复点击均以项目会话代次隔离迟到 list/detail/restore；旧结果不得改新项目列表、摘要、提示、确认态或正文。

不得写 localStorage/sessionStorage/IndexedDB、Cookie、剪贴板、下载、console、URL 参数；不得自动展开、预取详情、定时刷新、页面加载即请求或访问外网。

## 4. 两个工作区恢复编排

技术与商务 hook 各新增 `restoreRevision(revisionId)`，但必须复用现有 `runVersionedExternalWrite` 与检查点操作令牌：

1. 调用前验证当前项目、未全状态阻断、当前版本合法；同项目已有检查点 create/restore 或 revision restore 时拒绝重复操作；
2. 清除尚未发送的防抖 PUT，进入既有技术 `matrixSaveChainRef` / 商务 `saveChainRef`；等待此前已排队普通 PUT；
3. 真正执行 POST 时读取 `stateVersionRef.current`，body 只能是该最新 expected；revision ID 只来自严格列表内存项；
4. POST 合法成功后接受返回版本、阻断旧 UI，递增既有写入 epoch，执行唯一一次 editor-state GET；成功才水合并解除阻断；
5. 409、404、网络 abort、非法成功体或其他 POST 不确定失败均保留本地 UI、停止自动保存、零自动重试；不得使用 `currentStateVersion` 自动重发；
6. POST 成功但唯一 editor-state GET 失败，返回独立 `reload_failed`，提示业务已完成但需显式重载；不得重试 POST；
7. 合法成功或 reload_failed 后，面板可额外执行一次 revision **列表** GET 显示新时间点；该请求不计入唯一 editor-state GET，不得自动请求详情。

恢复后的水合必须继续覆盖技术与商务全部既有 13 键语义；不得只刷新当前工作区可见字段。水合触发的 effect 只吞一次，两个防抖窗口内零旧 UI PUT；用户下一次真实编辑必须精确恢复正常保存。

## 5. 精确文件白名单

Grok 只允许修改以下 7 个文件：

1. 新增 `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`；
2. 新增 `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`；
3. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`；
4. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`；
5. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`；
6. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`；
7. 新增 `frontend/e2e/editor-state-revision-history.spec.ts`。

禁止修改后端、检查点 API/面板/E2E、共享 `api.ts`、认证、路由总表、CSS、依赖、配置、锁文件、其他既有测试或文档。新面板必须复用现有 class/内联样式，不新增样式文件。既有 truth/E2E 因新面板默认折叠不应产生新请求，不得为迎合实现放宽旧网络或存储断言。Grok 不得 `git add/commit/push`。

## 6. failure-first 与反假绿矩阵

生产修改前先新增真实 Playwright 文件并串行运行，至少因面板不存在或 revision 请求数量为 0 真实失败；报告精确 failed/passed。最终至少覆盖：

1. 技术/商务默认折叠零请求；展开一次列表 GET；固定顺序、九来源标签、最多 10 条和手动刷新精确次数；
2. 列表 DOM、HTML、URL、存储、console 不含 revision ID、stateVersion、snapshot 或正文；未知外网请求立即失败；
3. 详情严格按点击加载；只展示六项摘要，不展示任何正文值；折叠、刷新、切项和项目切换清空；详情元数据错配/额外键/非法快照固定脱敏失败；
4. 技术与商务确认前 POST=0；POST 路径使用内存 revision ID，body 精确只有执行时最新 expected；不得投稿 snapshot/source/force；
5. 先挂起普通 PUT，再点恢复：restore POST=0；释放 PUT 后 expected 精确等于 PUT 响应版本；
6. 成功恢复 POST 精确 1 次、editor-state GET 精确 1 次、revision list 额外 1 次；技术/商务分别完整水合自身及跨域 13 键；两个防抖窗口零旧 PUT，下一真实编辑精确 +1 PUT；
7. 双击确认、同时点击检查点与修订恢复只能产生一个版本化写请求；不得用禁用按钮 `force:true` 冒充令牌验证；
8. 409、404、abort、500、成功体缺失/非法/额外字段均固定失败、保留完整本地状态、零自动重试、保持阻断；禁止宽泛任意错误码；
9. POST 成功 + 唯一 GET 失败显示“业务已完成但刷新失败”，revision POST 仍精确 1 次；显式重载前保持阻断；
10. A→B 迟到 list/detail/restore、折叠后迟到、旧项目 finally 均不污染 B，也不误清 B 的操作令牌；
11. checkpoint 面板仍存在且行为不变；新面板无创建/删除/diff/search/download/分页/自动请求。

测试必须使用请求闸/响应 promise 同步，禁止固定 sleep 冒充时序、`.or(...)`、宽泛 2xx/4xx/5xx、`>=1`、条件断言、空集合、路由 fallback 假成功、只看提示不核请求体/次数/顺序，或把 API 私有解析单测冒充真实双工作区 E2E。

## 7. 验收与非目标

Grok 最低自测：新 C3 E2E、既有 checkpoint restore E2E、技术/商务 editor-state truth、`npm run lint`、`npm run build`、`git diff --check` 与精确七文件白名单。所有 Playwright 命令固定 `--workers=1 --retries=0` 且逐条运行，禁止并行共享 SQLite。

Codex 独立审查 API 严格 shape、原文最小化、同令牌/同队列、执行时 expected、唯一重读和迟到隔离，再串行运行专项、受影响回归及前端全量。C3 不实现删除、diff、搜索、分页、跨项目历史、命名、标签、超出最近 10 条的完整历史/保留策略、分支/合并/发布/审批或多人实时协作。

## 8. 完成与独立验收记录

C3 冻结=`6b9143a`、实现=`5e4f9f6`。Grok 在生产未改时先跑出 **2 failed / 0 passed / 18 did not run**；初版实现后，Codex 先发现目标 revision 在恢复后列表头变化导致的测试时序取证错误，再连续关闭条件 count、三选一消息、条件点击、未真实发起检查点写、未真实发起 A 项目列表、详情旧请求无独立代次、到达计数冒充完成计数和保存链后补发等假绿。最终 E2E 使用真实请求闸及 `listCompleteLog/detailCompleteLog`，证明迟到响应已 fulfill 后仍不污染新项目或新摘要。

Codex 独立通过 C3 专项 **21 passed**、既有 checkpoint restore **51 passed**、技术/商务 editor-state truth **46 passed**、`lint`、`build` 和单 worker、零重试前端全量 **284 passed**；后端未改，沿用 **800 passed**。精确七文件白名单、暂存区、`git diff --check`、原始快照最小化、共享令牌/保存链、执行时 expected、唯一重读和迟到代次均通过。P12C 最近 10 条修订的九来源留史、列表、按需摘要和受限恢复链至此完整闭环；删除、diff、搜索、跨项目历史、超出最近 10 条的完整历史/保留策略与多人协作仍未实现。
