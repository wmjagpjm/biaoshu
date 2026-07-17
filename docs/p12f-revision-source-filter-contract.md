# P12F-D 修订历史来源筛选契约

模块：P12F-D 双工作区修订历史来源筛选
用途：在 P12F-B/C 有界游标页和共用修订面板上，按九类服务端权威来源筛选技术标与商务标修订，同时保持分页稳定、写链安全和数据最小化。
对接：`editor_state_revisions` 路由、`editor_state_revision_history_service`、`editorStateRevisionApi`、`EditorStateRevisionPanel`、后端专项测试与既有修订历史 E2E。
状态：2026-07-17 已冻结，待 Grok 按本文 failure-first 实现；Codex 独立审查、验收、文档闭环和提交推送。

## 1. 审计结论

修订账本当前最多保留 20 条，元数据已包含固定 `sourceKind`，前后端也已有同一组九类来源和中文标签；真正缺口是 `/page` 只支持无条件游标页，面板不能按来源缩小时间线。因数据量固定有界，本包不需要全文索引、数据库迁移、搜索引擎或缓存。

筛选必须发生在服务端查询中，不能只过滤当前已加载的 10 条或 20 条，否则第一页可能错误显示空态或漏掉后页命中。筛选条件必须与游标绑定；把无筛选游标用于筛选页、把筛选 A 的游标用于筛选 B，均不得静默返回可能漏项的结果。

## 2. API 合同

只扩展既有静态页：

```text
GET /api/projects/{projectId}/editor-state-revisions/page
GET /api/projects/{projectId}/editor-state-revisions/page?cursor={esrc1Cursor}
GET /api/projects/{projectId}/editor-state-revisions/page?sourceKind={kind}
GET /api/projects/{projectId}/editor-state-revisions/page?sourceKind={kind}&cursor={esrc2Cursor}
```

规则：

- `sourceKind` 缺失表示全部来源；显式空串、空白、大小写变体、别名或不在权威九类枚举中的值固定返回 HTTP 400，detail 精确 `code=editor_state_revision_source_invalid`、固定中文 message、`Cache-Control: no-store`，不得回显输入；
- 未知 `source/search/q/limit/offset/page/order/total/hasMore` 仍按 FastAPI 既有兼容语义忽略；旧 `/editor-state-revisions` 继续忽略所有筛选/分页参数，顶层仍仅 `{items}`；
- 响应 shape 不变，顶层精确 `items/nextCursor`，每页最多 10 条，固定 `LIMIT 11` 前瞻，五列投影，排序仍为 `created_at DESC,id DESC`；
- 有筛选时 SQL 必须同时限定 workspace、project 和精确 `source_kind`，再应用键集位置；不得加载 `snapshot_json`、COUNT、OFFSET、当前 editor-state 或检查点；
- 项目不存在/跨空间仍优先固定 404；合法项目上的非法来源或游标固定 400；任何元数据/lookahead 损坏仍固定 500 corrupt；成功和业务错误均 `no-store`；
- 全程只读，禁止 commit/rollback/flush/refresh、锁、审计、修订裁剪或其他五域写入。

## 3. 游标版本与筛选绑定

无筛选页保持 P12F-B 的 `esrc1_` 规范游标，载荷精确 `{i,t}`，既有值和测试全部兼容。

有筛选页使用新 `esrc2_` 规范游标，载荷精确 `{i,s,t}`：

- `i` 为末条合法 revision ID；`t` 为既有 UTC 微秒位置；`s` 为权威来源字面量；
- 继续使用紧凑、`sort_keys`、无填充 base64url 和完整规范往返校验；总长度上限仍为 192；
- `esrc2` 只能与同值 `sourceKind` 同时使用；缺筛选、不同筛选或非法筛选均固定 `editor_state_revision_cursor_invalid`，不得自动采用游标内来源；
- `esrc1` 只能用于无筛选页；携带任意合法 `sourceKind` 时固定 cursor invalid；
- 前端只校验 `esrc1_` 或 `esrc2_` 外壳，禁止解码、生成或从游标读取来源；筛选状态以当前内存选择和显式 query 为准。

## 4. 前端交互

共用面板展开后增加：

```text
data-testid="editor-state-revision-source-filter"
默认文案：全部来源
其余选项：既有九类固定中文来源标签
```

交互规则：

1. 默认全部来源，首次展开保持精确一次无 query 的页 GET；选择来源后精确一次第一页 GET，只带一个 `sourceKind`，不带空 cursor；
2. 切换筛选立即作废旧列表、加载更多、摘要、当前对比、单/双正文差异、双侧选择和恢复确认，不能让旧来源内容暂时挂在新标签下；
3. 筛选第一页失败显示既有固定列表失败文案和空态，不回退未筛选列表；当前筛选保留，用户可点刷新重试；
4. `nextCursor` 非空时沿用手动“加载更多”，筛选第二页只带当前 `sourceKind` 与服务端原样 `esrc2`；成功追加、失败保值、同步单飞、最多 20 条和不自动预取合同不变；
5. 刷新、恢复成功后的历史重载以及恢复后刷新失败路径继续使用当前筛选；折叠再展开保留当前项目的内存筛选；切换 projectId 重置为“全部来源”；卸载不持久化；
6. 列表/加载更多/恢复在途时筛选器禁用。筛选切换需独立代次或复用严格列表会话代次，使旧请求的 success/catch/finally 均不能污染新筛选；
7. 技术标与商务标共享同一实现，不新增第二套面板、路由、缓存或 hook。

## 5. 安全与数据最小化

- 只展示固定中文标签；revision ID、stateVersion、游标和快照正文继续不得进入 DOM、应用 URL、存储、Cookie、console、剪贴板或下载；`sourceKind` 只允许存在于组件内存、`select` 值和规定 API query，不写浏览器存储或应用路由；
- 不反射非法来源、后端 detail、路径、SQL、异常类型或响应正文；
- 不新增外网、依赖、计时器、自动轮询、自动预取、模块全局缓存或 AbortController 唯一隔离证据；
- E2E 禁止固定 sleep、`.or(...)`、宽泛 2xx、`>=1`、只等 arrived 不等 complete、`force:true` 或 route fallback 冒充成功。

## 6. 六文件白名单

Grok 只允许修改：

1. `backend/app/api/editor_state_revisions.py`
2. `backend/app/services/editor_state_revision_history_service.py`
3. `backend/tests/test_p12f_revision_source_filter.py`（新建）
4. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
5. `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
6. `frontend/e2e/editor-state-revision-history.spec.ts`

禁止修改 schema/model/database/migration、旧 P12F-B 测试、共享 `apiFetch`、workspace/hook、依赖/锁文件、配置、文档或 Git 历史。Grok 不得 `git add/commit/push`。

## 7. Failure-first 与验收门

Grok 必须先只新增后端专项测试并修改既有 E2E，生产四文件不得先改。分别运行后端 P12F-D 专项和前端 `--grep P12F-D`，得到由“接口仍忽略 sourceKind/面板无筛选器”造成的真实业务红测；收集、导入、fixture、浏览器/服务启动、TypeScript 或语法错误不算红测。

后端至少覆盖：九来源逐值、0/1/10/11/20、混排筛选不重不漏、同时间 ID 稳定、确定性重复；`esrc1/esrc2` 正反绑定、A 游标用于 B、缺/空/坏/非规范/超长/额外键游标；非法来源矩阵；项目优先级、跨项目/空间；五列、`source_kind` 谓词、LIMIT 11、无 snapshot/COUNT/主动 OFFSET；lookahead 损坏整页失败；旧页无筛选和旧列表完全兼容；五域零写与脱敏。

前端至少覆盖：技术/商务共用筛选；中文九选项；默认无 query；选择后只带 `sourceKind`；筛选第二页精确带同来源+原 `esrc2`；切换清旧意图；空结果；HTTP/shape/非法 cursor 失败；同值不重发；在途禁用；刷新/恢复保留筛选；折叠保留、项目切换重置；旧筛选首屏与第二页 arrived+complete 迟到隔离；零额外 API、零外网、零泄漏与零持久化。

Grok 至少运行：后端新专项、`test_p12f_revision_cursor_page.py`、`test_p12c_revision_history_read.py`；前端 P12F-D 聚焦、完整 history、技术/商务 truth、checkpoint restore、lint、build、`git diff --check`、精确六文件和空暂存区。Playwright 必须逐条串行，显式 `--workers=1 --retries=0`。后端/前端全量由 Codex 独立执行。

## 8. 明确未做

本包不做正文/标题搜索、日期筛选、多来源组合、命名、固定、删除、导出、分享、total/hasMore、页码、无限滚动、自动加载、跨项目历史、历史回填、多人协作、SSE 扩展或数据库变更。
