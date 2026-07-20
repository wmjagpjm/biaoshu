# P12L 检查点固定名额提示前端契约

模块：P12L editor-state 检查点默认列表固定数量提示

用途：在 P12J-A/B 已交付固定配额与共用固定入口、P12K 已交付默认列表固定优先之后，让技术标和商务标用户直接看到当前项目已固定数量与 5 条上限，并在固定、取消固定或删除后获得即时且不额外请求的反馈。

对接：`EditorStateCheckpointPanel`、既有检查点 list/search/create/restore/name/delete/pin API，以及 `frontend/e2e/editor-state-checkpoint-restore.spec.ts` 的共享探针。

二次开发：Grok 只能在严格两文件白名单内先新增真实 failure-first E2E，再修改共用面板并串行自测；不得暂存、提交或推送。Codex 负责独立规划、受限审查、分级验收、中文文档、提交和协作分支推送。

状态：2026-07-20 已在干净上游 HEAD `5258f84` 完成只读审计并冻结待实现。面板冻结 SHA-256=`CAA78A98C8113C333FF9D559F84FB2270B933D4F224C997F5897BEA5D4083401`，checkpoint E2E 冻结 SHA-256=`627ADAC0FD76A1971716608DDAD83B739E9B819D4053BFF2B48B45D90CE987DB`。

## 1. 选择理由与边界

1. 后端固定上限已是不可配置合同 `MAX_PINNED_CHECKPOINTS_PER_PROJECT=5`，默认列表又由 P12K 保证全部固定项先于普通项进入最多 20 条响应，因此默认列表中的严格 `isPinned` 元数据足以准确计算当前项目固定条数，不需要新增 API、Schema 或聚合查询。
2. 本包只增加只读展示：默认列表成功完成且不在搜索态时显示精确中文 `已固定 X 条（最多 5 条）`。`X` 只能由当前严格解析后的 `items` 中 `isPinned === true` 的数量派生，合法范围为 0..5。
3. 提示仅说明“条数名额”，不展示或推算 10 MiB 字节配额，不声称剩余空间，不新增进度条、分组标题、前端排序或容量 API。
4. 提示不改变服务端权威：达到 5 条时仍不在前端静默禁用普通项“固定”按钮；第 6 条请求继续交给现有 PATCH，由服务端返回固定错误，界面保留 5 条与原列表。
5. 不改默认列表顺序、搜索候选/顺序、当前列表 pin 原位更新、配额/裁剪、任何后端文件、API 封装、页面/hook、CSS、依赖、配置或锁文件。

## 2. 展示合同

1. 新增唯一固定上限常量，值精确 `5`；只用于展示，禁止复制服务端校验、阻断请求或修改按钮 disabled 逻辑。
2. `pinnedCount` 必须为 render 期纯派生值；禁止新增持久 state、effect、缓存、请求或定时器。计算必须严格等价于统计 `items` 中 `item.isPinned === true`，不得 truthy 宽判。
3. 提示使用稳定 `data-testid="editor-state-checkpoint-pinned-count"`，文本精确 `已固定 ${pinnedCount} 条（最多 5 条）`。
4. 仅在以下条件同时成立时显示：面板已展开、默认列表态、`listLoading=false`、`listError=null`。首次加载中、刷新加载中、列表错误和 active search 均隐藏；默认列表成功为空时显示 0 条。
5. 技术标和商务标必须复用同一组件、同一文案和同一派生规则；不得复制页面级实现。

## 3. 状态变化合同

1. pin 成功沿用既有目标项原位 `map` 更新；提示随下一次 render 从 X 变 X+1。unpin 成功变 X-1。两者均零 list/search/editor-state 重载、零重试。
2. pin/unpin 的 HTTP、坏成功体或相反布尔失败继续保留原 items；提示不得先行乐观变化或在失败后漂移。
3. 删除固定项成功沿用既有原位移除并使数量减一；删除普通项不改变数量。删除失败保留原数量。
4. 默认态刷新、创建、恢复后的既有重载以新响应重算数量；项目切换、折叠、卸载与迟到 A success/catch/finally 继续复用现有会话围栏，旧项目不得污染新项目数量。
5. active search 的 items 是搜索子集，必须隐藏数量提示；搜索中固定只原位更新结果且仍隐藏。清除搜索按既有精确一次默认 GET 后，才显示该默认列表计算出的数量。

## 4. 安全与数据最小化

1. 不得把 checkpointId、stateVersion、snapshot、displayName、关键词、原始错误、Cookie 或 CSRF 写进提示、URL、DOM 新属性、console、local/sessionStorage、Cookie、剪贴板、下载或外网。
2. 不得新增请求；提示变化只能来自既有 list/search/create/restore/delete/pin 状态变化。
3. 错误继续使用现有固定中文；不得展示后端配额内部码、原始响应或 10 MiB 使用量。
4. 新增/修改注释必须为简体中文并保持文件顶四字段规范；不得引入宽 OR、sleep、retry、skip、xfail 或只断言源码字符串的假绿。

## 5. Failure-first 与反假绿证据

1. 第一阶段只修改 E2E 文件，面板 SHA-256 必须仍等于冻结值。P12L 聚焦测试至少一项必须在页面和默认列表真实加载后，因缺少 `editor-state-checkpoint-pinned-count` 而失败；路由、白页、服务启动、收集、fixture、超时、skip/xfail 不算业务红测。
2. 真实浏览器证据至少覆盖：
   - 默认空列表显示 0/5，混合列表显示精确 X/5；
   - pin/unpin 成功即时加减且零 list/search 重载；失败保持数量；
   - 固定项删除成功减一、普通项删除不变、失败保值；
   - 5/5 时普通项按钮仍可发起一次 PATCH，服务端失败后保持 5/5；
   - active search 隐藏提示，搜索 pin 后仍隐藏，清除后一次 GET 恢复默认数量；
   - 技术标/商务标共用，新旧项目迟到响应不污染数量。
3. 请求计数必须使用精确差值；禁止“至少一次”、宽状态集合、`A || B`/`A or B`、只断言提示文本而不确认页面/列表已加载，或用 mock 内部状态冒充 DOM 行为。
4. 至少一项测试必须验证提示不存在时仍能看到已加载列表项，确保 failure-first 不是前置加载失败。

## 6. 严格两文件白名单

| 文件 | 冻结 SHA-256 | 允许变化 |
|---|---|---|
| `frontend/src/features/editor-state-checkpoints/EditorStateCheckpointPanel.tsx` | `CAA78A98C8113C333FF9D559F84FB2270B933D4F224C997F5897BEA5D4083401` | 固定上限展示常量、render 期精确计数与受限提示 |
| `frontend/e2e/editor-state-checkpoint-restore.spec.ts` | `627ADAC0FD76A1971716608DDAD83B739E9B819D4053BFF2B48B45D90CE987DB` | P12L failure-first、真实行为、请求计数、搜索隐藏、跨项目与泄漏证据 |

禁止修改后端、API 封装、页面/hook、CSS、既有测试语义、Playwright/Vite/TypeScript 配置、依赖、锁文件、脚本或其它文档。若两文件不足，Grok 只能发送 `question`，不得自行扩围。

## 7. 分级串行验收门

所有 Playwright 命令必须显式单 worker、零重试并逐条串行；禁止并发运行共享 SQLite：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --grep "P12L" --project=chromium --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --project=chromium --workers=1 --retries=0
npm run lint
npm run build
```

Grok 负责 P12L 聚焦、一次完整 checkpoint 受影响套件、lint/build。Codex 独立复跑 P12L 聚焦并执行 diff、白名单、哈希、弱断言/泄漏/新增网络调用静态审查；不重复 checkpoint 全套，不运行整仓 318 E2E，也不运行后端 pytest。只有聚焦失败、受影响回归或审查发现跨域风险时才升级测试范围。

## 8. Grok 回执合同

Grok 只发送一个完整 `review_request`：真实 failure-first 与冻结面板哈希、逐条串行命令和精确结果、两文件列表/最终哈希/空暂存区、默认态 0..5、pin/unpin/delete/失败保值、5/5 仍请求、搜索隐藏/清除、技术/商务共用、请求精确差值、泄漏门与明确未做项。额度、认证或进程中断只发送 `status`，禁止补造数字或完成结论。

## 9. 明确未做

不做字节容量展示、固定项分组标题、前端重排、搜索固定优先、到达 5 条后本地禁用、分页/游标、批量固定、乐观更新、自动重试、创建时命名、标签/备注、跨项目检查点、完整时间线、导出/分享、多人协作、presence、SSE/WebSocket、后端/API/数据库/依赖变更。
