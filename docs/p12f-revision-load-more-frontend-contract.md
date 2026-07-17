# P12F-C 修订历史前端加载更多契约

模块：P12F-C 双工作区修订历史手动加载更多
用途：消费 P12F-B 独立后端游标页，让技术标与商务标用户在默认最近 10 条之外按需访问第 11～20 条，同时保持详情、恢复、对比、正文差异和迟到隔离合同不变。
对接：`editorStateRevisionApi`、`EditorStateRevisionPanel`、`editor-state-revision-history.spec.ts`；后端 `GET /api/projects/{projectId}/editor-state-revisions/page`。
状态：2026-07-17 已冻结，等待 Grok 按三文件白名单 failure-first 实现；Codex 负责审查、独立验收、中文闭环和提交推送。

## 1. 审计结论与兼容边界

既有旧列表：

```text
GET /api/projects/{projectId}/editor-state-revisions
```

顶层严格只有 `{items}`，没有游标。前端不能从旧列表自行生成游标，也不能解码、猜测或拼接服务端排序位置，因此 P12F-C 首次展开、刷新和恢复后重载必须改用 P12F-B 新页：

```text
GET /api/projects/{projectId}/editor-state-revisions/page
GET /api/projects/{projectId}/editor-state-revisions/page?cursor={opaqueCursor}
```

旧 API 封装可以保留以维持兼容，但共用面板不得再用旧路由作为首屏，也不得同时请求新旧两套列表。P12F-C 不修改后端、技术标/商务标 workspace 或 hook，不改变恢复 POST、当前 editor-state GET、详情、当前对比、单修订正文差异或双修订正文差异请求。

## 2. 严格页解析与请求

API 层新增页类型，成功体顶层精确：

```json
{
  "items": [],
  "nextCursor": null
}
```

解析必须：

- 顶层精确 `items/nextCursor`，拒绝额外或缺失键；
- `items` 每页最多 10 条，逐项复用既有五键 `parseRevisionMeta`，页内 revision ID 不得重复；
- `nextCursor` 只能为 `null`，或完整长度不超过 192、非空、无首尾空白、前缀 `esrc1_`、其余仅 base64url 安全字符且无 `=` 的不透明字符串；
- 非空 `nextCursor` 时本页必须恰好 10 条；前端禁止解码正文、读取其中 ID/时间或本地生成游标；
- 请求只允许 GET，无 body；首次不带查询参数，后续只带一个经 `encodeURIComponent` 编码的 `cursor`；禁止客户端 `limit/offset/page/total/hasMore/source/search/q`；
- 任何 shape、元数据或游标错误只抛内部固定错误，不把响应原文、游标、ID、路径或后端 detail 带到可见文案、console 或存储。

## 3. 面板状态与交互

首次展开、刷新以及成功恢复后的历史重载均读取第一页，替换 `items` 并保存服务端 `nextCursor`。刷新继续沿用既有语义：作废并清空摘要、当前对比、单/双正文差异、恢复确认和旧列表会话；不得保留旧第二页。

仅当 `nextCursor` 非空时显示手动按钮：

```text
data-testid="editor-state-revision-load-more"
空闲文案：加载更多
在途文案：加载更多…
```

点击后：

1. 精确发送一次当前项目、当前游标的页 GET；连续点击/双击不得产生第二个在途请求；
2. 成功时按服务端顺序追加，原前 10 条顺序与当前摘要/对比/正文差异/双侧选择保持不变；
3. 合并后 revision ID 必须全局无重复，总数不得超过 P12F-A 保留上限 20；任何重叠、超 20 或“不一致的第三页游标”固定按加载更多失败处理；
4. `nextCursor=null` 后按钮消失，不自动继续请求；
5. 失败时保留原 items、原 cursor 和当前意图，显示固定 `更多修订加载失败，请稍后重试`，按钮可用同一游标重试；不得退化为清空列表或通用首屏失败。

加载更多只在恢复执行、首次/刷新列表在途或自身在途时禁用；不得自动轮询、滚动触发或预取。加载更多本身不请求详情、恢复、当前 editor-state、比较、正文差异或检查点。追加项必须复用现有按需摘要/对比/正文差异、双侧选择和恢复流程，不新增第二套动作。

## 4. 会话代次与迟到隔离

加载更多需要组件实例级独立请求代次和同步在途门，不能只依赖 React state。以下事件必须递增代次、清空在途门并使旧 `try/catch/finally` 全部失效：

- 折叠、卸载或 `projectId` 切换；
- 刷新/首次页重载；
- 成功或 reload-failed 恢复触发的第一页重载。

迟到加载更多无论成功、HTTP 失败还是非法 shape，都不得向折叠面板、新项目或新第一页追加 items，不得写入错误，不得清除新请求 loading/cursor。E2E 必须分别记录请求到达与 `route.fulfill` 完成；只等 arrived 或只释放 gate 不能冒充迟到续体已执行。

刷新按钮和恢复按钮在加载更多在途时禁用，避免列表替换与恢复写链并发。摘要、只读对比和正文差异可以继续按既有独立代次运行；加载更多成功不得无故清除它们。

## 5. 数据最小化与安全门

- revision ID/stateVersion/游标不得渲染到 DOM、应用 URL、localStorage、sessionStorage、Cookie、console、剪贴板或下载；游标只存在组件内存与规定的 API 查询参数中；
- 快照正文仍只在详情 API 栈内压缩为摘要，不得因分页返回 React；
- 只显示既有固定中文时间、来源、字节和动作标签；第二页来源同样只能展示中文映射；
- 不新增外网、计时器、模块全局缓存、AbortController 作为唯一隔离证据、浏览器持久化或新依赖；
- 不使用宽泛状态码、固定 sleep、`.or(...)`、`>=1` 或任意请求顺序冒充精确证据。

## 6. 三文件白名单

Grok 只允许修改：

1. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
2. `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
3. `frontend/e2e/editor-state-revision-history.spec.ts`

禁止修改后端、技术标/商务标 workspace 或 hook、共享 `apiFetch`、样式、其他 E2E、依赖/锁文件、配置、文档或 Git 历史。Grok 不得 `git add/commit/push`。

## 7. Failure-first 与验收门

Grok 必须先只修改 E2E，运行 P12F-C 新标题过滤形成真实业务红测：现有面板没有“加载更多”，且首屏仍请求旧列表；不得以收集、导入、fixture、依赖、浏览器启动、后端启动或 TypeScript 语法错误冒充红测。生产两文件在红测前不得修改。

新增/调整 E2E 至少覆盖：

- 默认折叠零新旧列表请求；展开精确一次无 cursor 页 GET，旧列表请求精确为 0；
- 11/20 条时按钮出现，点击精确一次携带服务端原 cursor，无 body/无额外查询；20 条最终 20 项、顺序不变、无重复、按钮消失；
- 追加项能按需加载摘要，并能参与跨页双修订选择；商务标第二页项可沿既有确认/expected/唯一重读流程恢复，成功后只重载第一页；
- 加载更多 HTTP/shape/额外键/超 10/坏 cursor/页内或跨页重复/超 20 失败时，原 10 条和 cursor 保留、固定错误、同 cursor 可重试；
- 双击/连续点击精确一个在途请求；load-more 期间刷新与恢复禁用；不自动请求第三页；
- 折叠、刷新、项目切换和恢复重载后的迟到 load-more 到达与完成均被隔离，旧 finally 不清新 loading/error/cursor；
- 第二页 ID/version/cursor/正文不进入 DOM、应用 URL、存储或 console；零外网、零新存储、零额外 API。

Grok 至少运行 P12F-C 新测试标题、完整 `editor-state-revision-history.spec.ts`、技术/商务 editor-state truth、checkpoint restore、lint、build、`git diff --check`、精确三文件和空暂存区。所有 Playwright 必须逐条串行，显式 `--workers=1 --retries=0`；前端全量留给 Codex 独立执行。

## 8. 明确未做

本包不做无限滚动、自动加载、搜索、来源筛选、日期筛选、删除、命名、固定、导出、分享、total/hasMore、页码、跨项目历史、多人协作、历史回填或后端变更。P12F-C 完成后若继续扩展，必须另包审计和冻结。
